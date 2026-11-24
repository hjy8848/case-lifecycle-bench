"""Shared engine for the card-based servicing templates.

It adds what the credit and field-service cards have in common to the lifecycle
engine: a case intake wake, one CRM ticket per subject, outbound contact with an
opt-in, a daily window and a per-channel rolling limit, a synchronous call with
identity verification, notifications and handoffs to a human. A template subclass
supplies its authoritative record, domain tools and world events.

An outbound call is synchronous. When the customer answers, the call is live in the
same session until it ends: the agent verifies, informs and speaks to the customer
right away, as on a real phone call. A simulated customer, when one is supplied,
answers what the agent says. Without one the customer follows the instance script
and the scripted intent rides on the tool results.
"""

from copy import deepcopy
from datetime import datetime, time, timedelta

from ... import eval_checks as checks
from ...lifecycle import (
    LifecycleEnvironment,
    _FOLLOWUP,
    _PUSH,
    _VISIT,
    _case_time,
    _iso,
)

_INTAKE = _VISIT + 1
_TERMINAL = {"CONFIRMED", "LEFT", "UNRESOLVED"}

# Errors that mean the agent broke a contact rule, as opposed to a plain failure.
CONTACT_VIOLATIONS = {
    "outside_contact_window",
    "rolling_attempt_limit",
    "contact_not_allowed",
    "contact_blocked",
}


def call_outcomes(trajectory) -> list[bool]:
    """One verdict per answered outbound call, from the trace alone.

    With a simulated customer a call succeeds when the customer closes it with a
    terminal signal. With the scripted customer, which has no voice, it succeeds when
    the agent actually spoke on the call. A call nobody answered is not a conversation.
    """
    answered = [
        row["result"]["contact_id"]
        for row in trajectory
        if row.get("tool") == "start_outbound_contact"
        and isinstance(row.get("result"), dict)
        and row["result"].get("status") == "answered"
    ]
    verdicts = []
    for contact_id in answered:
        # Anything the agent did on the live call: speaking, informing, offering.
        acts = [
            row["result"]
            for row in trajectory
            if row.get("tool") not in {None, "start_outbound_contact"}
            and isinstance(row.get("args"), dict)
            and row["args"].get("contact_id") == contact_id
            and isinstance(row.get("result"), dict)
            and "error" not in row["result"]
        ]
        simulated = any("customer_says" in result or "call_ended" in result for result in acts)
        verdicts.append(
            any(result.get("call_ended") for result in acts) if simulated else bool(acts)
        )
    return verdicts


def conversation_success(chats, run):
    """Chat visits and outbound calls count alike."""
    chat_rate = checks.conversation_success(chats)
    chat_count = sum(1 for rows in chats.values() if rows)
    calls = call_outcomes(run["trajectory"])
    if not calls:
        return chat_rate
    total = chat_count + len(calls)
    return ((chat_rate or 0) * chat_count + sum(calls)) / total


class ServicingEnvironment(LifecycleEnvironment):
    contact_tools: tuple[str, ...] = ()

    def __init__(self, case: dict, wake: str, customer=None):
        self.case_id = case["case_id"]
        # With a simulated customer, what they privately intend must reach the agent
        # only through what they say, never through a tool result.
        self.hide_customer_intent = case.get("customer_mode") == "llm"
        # customer(visit, dialogue) -> reply text or a terminal token
        self.customer = customer if self.hide_customer_intent else None
        self.contact_policy = case.get("contact_policy")
        self.script = deepcopy(case["init"].get("script", {}))
        self.contact_scripts = {}
        self.dialogues = {}
        self.ended_calls = set()
        super().__init__(case, wake, single_session=False)
        self._enqueue(_case_time(case["intake"]["t"]), _INTAKE, case["intake"])

    # -- state and triggers ------------------------------------------------

    def _initial_record(self, init: dict) -> dict:
        raise NotImplementedError

    def _initial_state(self, init: dict) -> tuple[str, str, dict]:
        customer_id, subject_id = init["customer_id"], init["subject_id"]
        customer = {
            "customer_id": customer_id,
            **{f"{channel}_opt_in": True for channel in ("voice", "sms")},
            **deepcopy(init.get("customer", {})),
        }
        return (
            customer_id,
            subject_id,
            {
                "customers": {customer_id: customer},
                "work_items": {subject_id: self._initial_record(init)},
                "tickets": {},
                "followups": {},
                "contacts": {},
                "outbox": {},
                "handoffs": {},
            },
        )

    def _event_subject(self):
        return {"user_id": self.user_id, "subject_id": self.subject_id}

    def _trigger(self, priority, payload):
        changes = {}
        if priority == _INTAKE:
            trigger = {
                "trigger_type": "case_intake",
                "trigger_id": "I1",
                "note": payload["note"],
            }
        elif priority == _VISIT:
            if not self._visit_condition(payload.get("condition")):
                return None, changes
            trigger = {
                "trigger_type": "user_visit",
                "trigger_id": payload["id"],
                "channel": payload["channel"],
                "message": payload["utterance"],
            }
        else:
            session, changes = super()._trigger(priority, payload)
            if session is None:
                return None, changes
            return {**session, **self._event_subject()}, changes
        return self._session_context({**trigger, **self._event_subject()}), changes

    def _visit_condition(self, condition) -> bool:
        if condition is None:
            return True
        if condition.get("no_message_yet"):
            # The customer has heard nothing from the agent by the time they come back.
            return not self.state["outbox"]
        if "no_message_after_event" in condition:
            # True unless the customer was messaged after the event took effect.
            applied = next(
                (
                    row
                    for row in self.trace
                    if row["kind"] == "world_event"
                    and row["event_id"] == condition["no_message_after_event"]
                    and row["changes"]
                ),
                None,
            )
            if applied is None:
                return True
            return not any(
                row["t"] >= applied["t"] for row in self.state["outbox"].values()
            )
        raise ValueError(f"Unsupported visit condition: {condition!r}")

    # -- world events ------------------------------------------------------

    def _event_changes(self, event: dict) -> dict:
        raise NotImplementedError

    def _world_event(self, event, *, in_session=False):
        self._record_world_event(
            event, self._event_changes(event), in_session=in_session
        )

    # -- tools -------------------------------------------------------------

    def _domain_execute(self, tool, args, session_id):
        """Return (result, changes) for a domain tool, or None if it is not one."""
        return None

    def _contact_block(self, channel: str) -> str | None:
        return None

    def _execute(self, tool, args, session_id):
        if tool == "search_tickets":
            return self._search_tickets(**args), {}
        if tool == "create_ticket":
            return self._create_ticket(**args)
        if tool == "read_contact_preferences":
            return self.state["customers"].get(
                args["customer_id"], {"error": "not_found"}
            ), {}
        if tool == "start_outbound_contact":
            return self._start_contact(session_id=session_id, **args)
        if tool == "send_message_to_user":
            return self._send_message(**args)
        if tool == "escalate_to_human":
            return self._escalate(**args)
        if (
            tool in {"verify_customer_identity", "say_to_customer"}
            or tool in self.contact_tools
        ):
            contact = self.state["contacts"].get(args.get("contact_id"))
            if contact is None or contact["session_id"] != session_id:
                return {"error": "contact_not_active"}, {}
            if contact["contact_id"] in self.ended_calls:
                return {"error": "call_ended"}, {}
            if tool == "verify_customer_identity":
                return self._verify_identity(contact)
            if tool == "say_to_customer":
                return self._say_to_customer(contact, args["text"])
            others = {key: value for key, value in args.items() if key != "contact_id"}
            return self._contact_tool(tool, contact, others)
        domain = self._domain_execute(tool, args, session_id)
        if domain is not None:
            return domain
        return super()._execute(tool, args, session_id)

    def _contact_tool(self, tool, contact, args):
        raise NotImplementedError

    def _search_tickets(self, user_id=None, subject_id=None):
        return [
            row
            for row in self.state["tickets"].values()
            if (user_id is None or row["user_id"] == user_id)
            and (subject_id is None or row["subject_id"] == subject_id)
        ]

    def _create_ticket(self, user_id, subject_id, summary):
        if user_id != self.user_id or subject_id != self.subject_id:
            return {"error": "unknown_subject"}, {}
        if self.state["tickets"]:
            return {"error": "ticket_exists"}, {}
        ticket_id = self._new_id("tickets", "T")
        return ticket_id, {
            "tickets": {
                ticket_id: {
                    "ticket_id": ticket_id,
                    "user_id": user_id,
                    "subject_id": subject_id,
                    "summary": summary,
                    "notes": [],
                    "status": "open",
                    "resolution_code": None,
                    "created_at": _iso(self.t),
                    "closed_at": None,
                }
            }
        }

    def _send_message(self, user_id, channel, text):
        if user_id != self.user_id:
            return {"error": "not_found"}, {}
        msg_id = self._new_id("outbox", "M")
        return {"ok": True}, {
            "outbox": {
                msg_id: {
                    "msg_id": msg_id,
                    "user_id": user_id,
                    "channel": channel,
                    "t": _iso(self.t),
                    "text": text,
                }
            }
        }

    def _escalate(self, ticket_id, note):
        if ticket_id not in self.state["tickets"]:
            return {"error": "not_found"}, {}
        handoff_id = self._new_id("handoffs", "H")
        return handoff_id, {
            "handoffs": {
                handoff_id: {
                    "handoff_id": handoff_id,
                    "ticket_id": ticket_id,
                    "note": note,
                    "status": "pending",
                    "decision": None,
                    "returned_at": None,
                }
            }
        }

    def _start_contact(self, customer_id, channel, session_id):
        policy = self.contact_policy
        if channel not in policy["channels"]:
            return {"error": "invalid_contact_channel"}, {}
        customer = self.state["customers"].get(customer_id)
        if customer is None or (
            policy["requires_opt_in"] and not customer.get(f"{channel}_opt_in")
        ):
            return {"error": "contact_not_allowed"}, {}
        if self._contact_block(channel):
            return {"error": "contact_blocked"}, {}
        if "start_local" in policy and not (
            time.fromisoformat(policy["start_local"])
            <= self.t.time()
            < time.fromisoformat(policy["end_local_exclusive"])
        ):
            return {"error": "outside_contact_window"}, {}
        recent = sum(
            row["channel"] == channel
            and self.t - datetime.fromisoformat(row["started_at"]) < timedelta(hours=24)
            for row in self.state["contacts"].values()
        )
        if recent >= policy["max_attempts_per_rolling_24h"]:
            return {"error": "rolling_attempt_limit"}, {}
        index = len(self.state["contacts"])
        scripts = self.script.get("contacts", [])
        # Once the scripted calls are used up, the customer keeps answering the
        # way the instance defines. One wasted call must not doom the case.
        script = (
            scripts[index]
            if index < len(scripts)
            else self.script.get("default_contact", {"answers": False})
        )
        status = "answered" if script["answers"] else "no_answer"
        contact_id = self._new_id("contacts", "C")
        self.contact_scripts[contact_id] = script
        result = {"contact_id": contact_id, "status": status}
        if status == "answered":
            # The call is live in this session until the session ends.
            opening = script.get("opening", "你好，我接到了你们的来电。")
            result["customer_says"] = opening
            self.dialogues[contact_id] = [{"customer": opening}]
        return result, {
            "contacts": {
                contact_id: {
                    "contact_id": contact_id,
                    "customer_id": customer_id,
                    "subject_id": self.subject_id,
                    "channel": channel,
                    "status": status,
                    "started_at": _iso(self.t),
                    "session_id": session_id if status == "answered" else None,
                    "identity_verified": False,
                }
            }
        }

    def _say_to_customer(self, contact, text):
        """The agent speaks on the live call; a simulated customer may answer."""
        contact_id = contact["contact_id"]
        dialogue = self.dialogues[contact_id]
        dialogue.append({"agent": text})
        if self.customer is None:
            # A scripted customer has no voice; the intent rides on the tool results.
            return {"delivered": True}, {}
        script = self.contact_scripts[contact_id]
        visit = {
            "goal": script["persona"],
            "channel": contact["channel"],
            "utterance": dialogue[0]["customer"],
        }
        reply = self.customer(visit, dialogue)
        if reply.upper() in _TERMINAL:
            self.ended_calls.add(contact_id)
            return {"delivered": True, "call_ended": True}, {}
        dialogue.append({"customer": reply})
        return {"delivered": True, "customer_says": reply}, {}

    def _call_tool(self, tool, args, session_id, step):
        result = super()._call_tool(tool, args, session_id, step)
        # Put the spoken words in the trace so claims on a call are judged like chat.
        if tool == "say_to_customer" and "delivered" in result:
            self._log("message", session_id, role="agent", text=args["text"], step=step)
            if result.get("customer_says"):
                self._log(
                    "message", session_id, role="user", text=result["customer_says"], step=step
                )
        return result

    def _verify_identity(self, contact):
        verified = self.contact_scripts[contact["contact_id"]].get(
            "identity_verifiable", True
        )
        changes = (
            {
                "contacts": {
                    contact["contact_id"]: {**contact, "identity_verified": True}
                }
            }
            if verified
            else {}
        )
        return {"verified": verified}, changes

    def _react_to_tool(self, tool, args, changes):
        self._schedule_reactive_events(tool, args.get("subject_id"))
