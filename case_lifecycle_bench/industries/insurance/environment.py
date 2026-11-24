"""Simulated renewal quote, customer contact and version-scoped consent."""

from copy import deepcopy
from datetime import datetime, time, timedelta

from ...lifecycle import (
    LifecycleEnvironment,
    _FOLLOWUP,
    _PUSH,
    _VISIT,
    _case_time,
    _iso,
)


_INTAKE = _VISIT + 1
_OUTBOUND = _VISIT + 2
_VOICE = "simulated_voice"


class InsuranceEnvironment(LifecycleEnvironment):
    def __init__(self, case: dict, wake: str):
        super().__init__(case, wake, single_session=False)
        self.contact_policy = case["contact_policy"]
        self.revision_after_consent = case.get("revision_after_consent")
        self.outbound_script = case["oracle"]["scripted_outbound_customer"]
        self.contact_scripts = {}
        self._enqueue(_case_time(case["intake"]["t"]), _INTAKE, case["intake"])

    def _initial_state(self, init: dict) -> tuple[str, str, dict]:
        customer = deepcopy(init["customer"])
        policy = deepcopy(init["policy"])
        customer.setdefault("confirmed_quote_version", None)
        policy.setdefault("accepted_quote_version", None)
        customer_id = customer["customer_id"]
        policy_id = policy["policy_id"]
        return (
            customer_id,
            policy_id,
            {
                "customers": {customer_id: customer},
                "policies": {policy_id: policy},
                "quotes": {
                    policy_id: {"policy_id": policy_id, **deepcopy(init["quote"])}
                },
                "tickets": {},
                "followups": {},
                "contacts": {},
                "consents": {},
            },
        )

    def _trigger(self, priority, payload):
        subject = {"customer_id": self.user_id, "policy_id": self.subject_id}
        changes = {}
        if priority == _INTAKE:
            trigger = {
                "trigger_type": "case_intake",
                "trigger_id": payload.get("id", "I1"),
                "note": payload["visible_note"],
            }
        elif priority in {_PUSH, _FOLLOWUP}:
            session, changes = super()._trigger(priority, payload)
            if session is None:
                return None, changes
            return {"t": session["t"], **subject, **session}, changes
        elif priority == _OUTBOUND:
            contact_id = payload["contact_id"]
            trigger = {
                "trigger_type": "outbound_contact",
                "trigger_id": contact_id,
                "contact_id": contact_id,
                "channel": _VOICE,
                "message": "你好，我接到了续保服务的模拟电话。",
            }
        else:
            if not self._visit_condition(payload.get("condition")):
                return None, changes
            contact = self._new_contact(payload["channel"], "answered")
            contact_id = contact["contact_id"]
            self.contact_scripts[contact_id] = payload.get(
                "scripted_customer", self.outbound_script
            )
            changes = {"contacts": {contact_id: contact}}
            trigger = {
                "trigger_type": "user_visit",
                "trigger_id": payload["id"],
                "contact_id": contact_id,
                "channel": payload["channel"],
                "user_id": self.user_id,
                "message": payload["utterance"],
            }
        return {"t": _iso(self.t), **subject, **trigger}, changes

    def _run_session(self, agent, session_id, session, changes):
        contact_id = session.get("contact_id")
        if contact_id is not None:
            rows = changes.get("contacts", self.state["contacts"])
            changes = {
                **changes,
                "contacts": {
                    contact_id: {**rows[contact_id], "session_id": session_id}
                },
            }
        super()._run_session(agent, session_id, session, changes)

    def _visit_condition(self, condition):
        if condition is None:
            return True
        records = {
            "customer": self.state["customers"][self.user_id],
            "policy": self.state["policies"][self.subject_id],
            "quote": self.state["quotes"][self.subject_id],
        }

        def field(path):
            table, name = path.split(".")
            return records[table].get(name)

        for rule in condition["any"]:
            expected = (
                field(rule["not_equals_field"])
                if "not_equals_field" in rule
                else rule["not_equals"]
            )
            if field(rule["field"]) != expected:
                return True
        return False

    def _world_event(self, event, *, in_session=False):
        quote = self.state["quotes"][self.subject_id]
        changes = {"quotes": {self.subject_id: {**quote, **event["changes"]["quote"]}}}
        self._record_world_event(
            {**event, "op": "insurance.quote_update"}, changes, in_session=in_session
        )

    def _execute(self, tool, args, session_id):
        reads = {
            "read_policy": ("policies", "policy_id"),
            "read_renewal_quote": ("quotes", "policy_id"),
            "read_contact_preferences": ("customers", "customer_id"),
        }
        if tool in reads:
            table, key = reads[tool]
            return self.state[table].get(args[key], {"error": "not_found"}), {}
        if tool == "search_tickets":
            return self._find_tickets(**args), {}
        if tool == "create_ticket":
            return self._open_ticket(**args)
        if tool == "start_outbound_call":
            return self._start_outbound(**args)
        live_tools = {
            "verify_customer_identity": self._verify_identity,
            "present_renewal_quote": self._present_quote,
            "submit_renewal": self._submit_renewal,
            "notify_renewal_confirmation": self._notify_confirmation,
        }
        if tool not in live_tools:
            return super()._execute(tool, args, session_id)
        contact = self.state["contacts"].get(args["contact_id"])
        if contact is None or contact["session_id"] != session_id:
            return {"error": "contact_not_active"}, {}
        return live_tools[tool](
            contact,
            **{key: value for key, value in args.items() if key != "contact_id"},
        )

    def _find_tickets(self, user_id=None, policy_id=None):
        return [
            row
            for row in self.state["tickets"].values()
            if (user_id is None or row["user_id"] == user_id)
            and (policy_id is None or row["policy_id"] == policy_id)
        ]

    def _open_ticket(self, user_id, policy_id, summary):
        if user_id != self.user_id or policy_id != self.subject_id:
            return {"error": "not_found"}, {}
        ticket_id = self._new_id("tickets", "T")
        return ticket_id, {
            "tickets": {
                ticket_id: {
                    "ticket_id": ticket_id,
                    "user_id": user_id,
                    "policy_id": policy_id,
                    "summary": summary,
                    "notes": [],
                    "status": "open",
                    "resolution_code": None,
                    "created_at": _iso(self.t),
                    "closed_at": None,
                }
            }
        }

    def _new_contact(self, channel, status):
        return {
            "contact_id": self._new_id("contacts", "C"),
            "customer_id": self.user_id,
            "policy_id": self.subject_id,
            "channel": channel,
            "status": status,
            "started_at": _iso(self.t),
            "session_id": None,
            "identity_verified": False,
            "presented_quote_version": None,
            "confirmed_quote_version": None,
        }

    def _start_outbound(self, customer_id, channel):
        policy = self.contact_policy
        if channel != _VOICE or channel != policy["channel"]:
            return {"error": "invalid_contact_channel"}, {}
        customer = self.state["customers"].get(customer_id)
        if customer is None or (
            policy["requires_opt_in"] and not customer["voice_contact_opt_in"]
        ):
            return {"error": "voice_contact_not_allowed"}, {}
        if (
            policy["prohibit_new_voice_attempt_after_renewal_confirmed"]
            and self.state["policies"][self.subject_id]["renewal_state"] == "confirmed"
        ):
            return {"error": "renewal_already_confirmed"}, {}
        if (
            not time.fromisoformat(policy["start_local"])
            <= self.t.time()
            < time.fromisoformat(policy["end_local_exclusive"])
        ):
            return {"error": "outside_contact_window"}, {}
        prior = sum(
            row["channel"] == _VOICE
            and self.t - datetime.fromisoformat(row["started_at"]) < timedelta(hours=24)
            for row in self.state["contacts"].values()
        )
        if prior >= policy["max_attempts_per_rolling_24h"]:
            return {"error": "rolling_attempt_limit"}, {}
        status = "answered" if self.outbound_script["answers"] else "no_answer"
        contact = self._new_contact(channel, status)
        contact_id = contact["contact_id"]
        return {"contact_id": contact_id, "status": status}, {
            "contacts": {contact_id: contact}
        }

    def _react_to_tool(self, tool, args, changes):
        self._schedule_reactive_events(
            tool, args.get("policy_id", args.get("customer_id"))
        )
        if tool == "start_outbound_call":
            contact_id, contact = next(iter(changes["contacts"].items()))
            if contact["status"] == "answered":
                self.contact_scripts[contact_id] = self.outbound_script
                self._enqueue(self.t, _OUTBOUND, {"contact_id": contact_id})

    def _verify_identity(self, contact):
        contact_id = contact["contact_id"]
        verified = self.contact_scripts[contact_id]["identity_verifiable"] is True
        changes = (
            {"contacts": {contact_id: {**contact, "identity_verified": True}}}
            if verified
            else {}
        )
        return {"verified": verified}, changes

    def _quote_access_error(self, contact, policy_id, quote_version):
        if not contact["identity_verified"]:
            return "identity_not_verified"
        quote = self.state["quotes"].get(policy_id)
        if (
            policy_id != contact["policy_id"]
            or quote is None
            or quote["status"] != "ready"
            or quote["version"] != quote_version
        ):
            return "quote_not_current"
        return None

    def _present_quote(self, contact, policy_id, quote_version):
        error = self._quote_access_error(contact, policy_id, quote_version)
        if error:
            return {"error": error}, {}
        contact_id = contact["contact_id"]
        customer_id = contact["customer_id"]
        customer = self.state["customers"][customer_id]
        accepts = self.contact_scripts[contact_id]["accepts_quote_version"]
        accepted = quote_version in (
            accepts if isinstance(accepts, list) else [accepts]
        )
        changes = {
            "contacts": {
                contact_id: {**contact, "presented_quote_version": quote_version}
            },
            "customers": {
                customer_id: {**customer, "notified_quote_version": quote_version}
            },
        }
        if accepted:
            changes["consents"] = {
                contact_id: {
                    "contact_id": contact_id,
                    "customer_id": customer_id,
                    "policy_id": policy_id,
                    "quote_version": quote_version,
                    "accepted": True,
                    "created_at": _iso(self.t),
                }
            }
        return {
            "delivered": True,
            "accepted": accepted,
            "quote_version": quote_version,
        }, changes

    def _submit_renewal(self, contact, policy_id, quote_version):
        error = self._quote_access_error(contact, policy_id, quote_version)
        if error:
            return {"error": error}, {}
        contact_id = contact["contact_id"]
        consent = self.state["consents"].get(contact_id)
        if (
            consent is None
            or not consent["accepted"]
            or consent["quote_version"] != quote_version
        ):
            return {"error": "missing_current_quote_consent"}, {}
        policy = self.state["policies"][policy_id]
        if policy["renewal_state"] == "confirmed":
            return {"error": "renewal_already_confirmed"}, {}
        confirmed = {
            **policy,
            "renewal_state": "confirmed",
            "accepted_quote_version": quote_version,
            "confirmed_at": _iso(self.t),
            "consent_contact_id": contact_id,
        }
        return {"ok": True}, {"policies": {policy_id: confirmed}}

    def _notify_confirmation(self, contact, policy_id, quote_version):
        error = self._quote_access_error(contact, policy_id, quote_version)
        if error:
            return {"error": error}, {}
        policy = self.state["policies"][policy_id]
        if (
            policy["renewal_state"] != "confirmed"
            or policy["accepted_quote_version"] != quote_version
        ):
            return {"error": "renewal_not_confirmed"}, {}
        contact_id = contact["contact_id"]
        customer_id = contact["customer_id"]
        customer = self.state["customers"][customer_id]
        return {"ok": True}, {
            "contacts": {
                contact_id: {**contact, "confirmed_quote_version": quote_version}
            },
            "customers": {
                customer_id: {**customer, "confirmed_quote_version": quote_version}
            },
        }

    def _call_tool(self, tool, args, session_id, step):
        result = super()._call_tool(tool, args, session_id, step)
        if tool == "verify_customer_identity" and "verified" in result:
            self._log(
                "message",
                session_id,
                role="agent",
                text="请先完成模拟身份核验。",
                step=step,
            )
            response = (
                "身份核验通过。" if result["verified"] else "我未能完成身份核验。"
            )
            self._log("message", session_id, role="user", text=response, step=step)
        elif tool == "present_renewal_quote" and result.get("delivered"):
            revision = result["quote_version"]
            credits = self.state["quotes"][self.subject_id]["premium_test_credits"]
            self._log(
                "message",
                session_id,
                role="agent",
                text=f"本次模拟续保报价为 {revision}，费用 {credits} 测试点。您是否明确同意此版本？",
                step=step,
            )
            response = (
                f"我明确同意按 {revision} 续保。"
                if result["accepted"]
                else f"我暂不同意按 {revision} 续保。"
            )
            self._log("message", session_id, role="user", text=response, step=step)
            revision_event = self.revision_after_consent
            if (
                result["accepted"]
                and revision_event
                and revision == revision_event["from_version"]
            ):
                self._world_event(revision_event, in_session=True)
                result["quote_revised"] = True
        elif tool == "notify_renewal_confirmation" and result.get("ok"):
            self._log(
                "message",
                session_id,
                role="agent",
                text=f"已核实按 {args['quote_version']} 续保完成。",
                step=step,
            )
        return result
