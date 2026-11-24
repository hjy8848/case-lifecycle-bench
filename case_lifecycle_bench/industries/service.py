"""Synthetic staged servicing cases on the shared lifecycle engine."""

from dataclasses import dataclass
from random import Random

from .. import eval_checks as checks
from ..lifecycle import LifecycleEnvironment, _VISIT, _case_time, _iso
from ..model_agent import TOOLS as STANDARD_TOOLS
from ..model_agent import _tool

_INTAKE = _VISIT + 1
_REVIEWS = ("D2 10:00", "D3 13:00", "D3 16:00")
_CRM_NAMES = {
    "get_current_time",
    "get_ticket",
    "add_ticket_note",
    "list_followups",
    "schedule_followup",
    "cancel_followup",
    "close_ticket",
    "end_session",
}


@dataclass(frozen=True)
class Milestone:
    status: str
    action: str
    instructions: str


@dataclass(frozen=True)
class Workflow:
    industry: str
    template: str
    title: str
    read_tool: str
    milestones: tuple[Milestone, ...]
    revisit: str

    @property
    def final(self):
        return self.milestones[-1]

    def generate(self, seed):
        rng = Random(seed)
        number = rng.randrange(10**6, 10**7)
        subject = rng.choice((f"#W{number}", f"810001{number}", f"#CLB-{number}"))
        candidates = (
            ("D2 08:15", "D2 08:30", "D2 09:15", "D2 09:45"),
            ("D3 10:30", "D3 11:00", "D3 11:30", "D3 12:30"),
            ("D3 14:00", "D3 14:30", "D3 15:00", "D3 15:30"),
        )
        events = [
            {
                "id": f"E{index + 1}",
                "t": rng.choice(candidates[index]),
                "op": f"{self.template}.{milestone.status}",
                "status": milestone.status,
                "notify_agent": True,
                "duty": milestone.action,
            }
            for index, milestone in enumerate(self.milestones)
        ]
        if self.template == "credit_application_documents":
            events[0]["data"] = {
                "missing_document": rng.choice(
                    ("income_summary", "address_confirmation")
                )
            }
            events[1]["requires_action"] = self.milestones[0].action
            events[2]["requires_action"] = self.milestones[1].action
            events[2]["data"] = {"decision": rng.choice(("approved", "declined"))}
        elif self.industry == "field_service":
            events[1]["data"] = {
                "appointment_slot": rng.choice(("D5 09:00–11:00", "D5 14:00–16:00"))
            }
        return {
            "case_id": f"{self.template}_seed_{seed}",
            "industry": self.industry,
            "template": self.template,
            "horizon": "D4 18:00",
            "data_origin": "synthetic",
            "init": {
                "customer_id": f"SYN-CUS-{number}",
                "subject_id": subject,
                **(
                    {"offers_repayment_commitment": rng.choice((True, False))}
                    if self.template == "credit_delinquency_followup"
                    else {}
                ),
            },
            "intake": {"t": "D1 10:00", "note": self.title},
            "events": events,
            "visits": [
                {
                    "id": "V1",
                    "t": rng.choice(("D4 10:00", "D4 13:00", "D4 15:00")),
                    "channel": "secure_chat",
                    "condition": "missing_final_notice",
                    "utterance": self.revisit,
                }
            ],
        }

    @property
    def policy(self):
        actions = "\n".join(
            f"When status is {step.status}: {step.instructions}"
            for step in self.milestones
        )
        return f"""You handle the synthetic service case {self.title}. All actions occur in the fixture only.
Reuse one CRM ticket per customer and subject. At intake, schedule the first review for D2 10:00 virtual local time.
The virtual calendar starts Monday 2026-01-05. Use the session date and ISO timestamps for scheduling.
On each wake, read the current authority with {self.read_tool} before taking a business action.
If still waiting, use the next relevant review slot: D2 10:00, D3 13:00 and D3 16:00.
{actions}
If a simulated customer supplies a repayment commitment timestamp, record that exact value and schedule a check at that time.
Use secure channels only. Do not initiate a payment, decide a loan application, or collect personal information.
Close the ticket after the verified final notice is delivered. Do not repeat a completed action. End each session when done."""

    @property
    def tools(self):
        subject = {"subject_id": "string"}
        return [
            *(
                tool
                for tool in STANDARD_TOOLS
                if tool["function"]["name"] in _CRM_NAMES
            ),
            _tool(
                "search_tickets", "Find the CRM case.", {"user_id": "string", **subject}
            ),
            _tool(
                "create_ticket",
                "Open one CRM case.",
                {"user_id": "string", **subject, "summary": "string"},
            ),
            _tool(
                self.read_tool,
                "Read the authoritative current state and completed actions.",
                subject,
            ),
            *(
                _tool(step.action, step.instructions, subject)
                for step in self.milestones
            ),
            *(
                [
                    _tool(
                        "record_repayment_commitment",
                        "Record the exact scripted customer's timestamp.",
                        {**subject, "due_at": "string"},
                    )
                ]
                if self.template == "credit_delinquency_followup"
                else []
            ),
        ]


class ServiceEnvironment(LifecycleEnvironment):
    def __init__(self, case, wake, workflow):
        self.workflow = workflow
        self.commitment_at = (
            _iso(_case_time("D3 13:00"))
            if case["init"].get("offers_repayment_commitment")
            else None
        )
        super().__init__(case, wake, single_session=False)
        self._enqueue(_case_time(case["intake"]["t"]), _INTAKE, case["intake"])
        self._observed = {}

    def _initial_state(self, init):
        user, subject = init["customer_id"], init["subject_id"]
        return (
            user,
            subject,
            {
                "work_items": {
                    subject: {"subject_id": subject, "status": "waiting", "actions": []}
                },
                "tickets": {},
                "followups": {},
            },
        )

    def _event_subject(self):
        return {"user_id": self.user_id, "subject_id": self.subject_id}

    def _trigger(self, priority, payload):
        if priority == _INTAKE:
            trigger = {
                "trigger_type": "case_intake",
                "trigger_id": "I1",
                "note": payload["note"],
            }
        elif priority == _VISIT:
            record = self.state["work_items"][self.subject_id]
            if self.workflow.final.action in record["actions"]:
                return None, {}
            trigger = {
                "trigger_type": "user_visit",
                "trigger_id": payload["id"],
                "message": payload["utterance"],
                "channel": payload["channel"],
            }
        else:
            session, changes = super()._trigger(priority, payload)
            return (
                {**session, **self._event_subject()} if session is not None else None
            ), changes
        return self._session_context({**trigger, **self._event_subject()}), {}

    def _world_event(self, event, *, in_session=False):
        old = self.state["work_items"][self.subject_id]
        requirement = event.get("requires_action")
        updated = {**old, "status": event["status"], **event.get("data", {})}
        changes = (
            {}
            if requirement and requirement not in old["actions"]
            else {"work_items": {self.subject_id: updated}}
        )
        self._record_world_event(event, changes, in_session=in_session)

    def _execute(self, tool, args, session_id):
        if tool in {"search_tickets", "create_ticket"}:
            if (args.get("user_id"), args.get("subject_id")) != (
                self.user_id,
                self.subject_id,
            ):
                return {"error": "unknown_subject"}, {}
            tickets = self.state["tickets"]
            if tool == "search_tickets":
                return list(tickets.values()), {}
            if tickets:
                return {"error": "ticket_exists"}, {}
            ticket = self._new_id("tickets", "T")
            return ticket, {
                "tickets": {
                    ticket: {"ticket_id": ticket, **args, "status": "open", "notes": []}
                }
            }
        steps = {step.action: step for step in self.workflow.milestones}
        record = self.state["work_items"][self.subject_id]
        if (
            tool == "close_ticket"
            and self.workflow.final.action not in record["actions"]
        ):
            return {"error": "close_before_notice"}, {}
        if tool not in {self.workflow.read_tool, "record_repayment_commitment", *steps}:
            return super()._execute(tool, args, session_id)
        if args.get("subject_id") != self.subject_id:
            return {"error": "unknown_subject"}, {}
        if tool == self.workflow.read_tool:
            self._observed[session_id] = record["status"]
            return record, {}
        if tool == "record_repayment_commitment":
            if (
                "send_past_due_reminder" not in record["actions"]
                or self.commitment_at is None
                or args.get("due_at") != self.commitment_at
                or record["status"] != "past_due"
            ):
                return {"error": "commitment_not_verified"}, {}
            return {"ok": True}, {
                "work_items": {
                    self.subject_id: {
                        **record,
                        "repayment_commitment": self.commitment_at,
                    }
                }
            }
        if (
            self._observed.get(session_id) != record["status"]
            or steps[tool].status != record["status"]
        ):
            return {"error": "status_not_verified"}, {}
        if (
            tool == "book_and_notify_repair"
            and "reserve_required_part" not in record["actions"]
        ):
            return {"error": "parts_not_reserved"}, {}
        if tool in record["actions"]:
            return {"error": "already_completed"}, {}
        result = {"ok": True}
        if tool == "send_past_due_reminder":
            result["repayment_commitment"] = self.commitment_at
        return result, {
            "work_items": {
                self.subject_id: {**record, "actions": [*record["actions"], tool]}
            }
        }


def run_oracle(
    workflow, session, call, *, skip_initial=False, early=False, skip_notice=False
):
    subject = {"subject_id": session["subject_id"]}
    user_id = session["user_id"]
    tickets = call("search_tickets", user_id=user_id, **subject)
    ticket = (
        tickets[0]["ticket_id"]
        if tickets
        else call("create_ticket", user_id=user_id, summary=workflow.title, **subject)
    )
    state = call(workflow.read_tool, **subject)
    if session["trigger_type"] == "case_intake":
        if early:
            call(workflow.final.action, **subject)
        if not skip_initial:
            for due in _REVIEWS[: len(workflow.milestones)]:
                call(
                    "schedule_followup",
                    ticket_id=ticket,
                    due_at=_iso(_case_time(due)),
                    note="核实业务进度",
                )
        return None
    step = next(
        (row for row in workflow.milestones if row.status == state["status"]), None
    )
    if (
        step
        and step.action not in state["actions"]
        and not (skip_notice and step == workflow.final)
    ):
        result = call(step.action, **subject)
        if result.get("repayment_commitment"):
            due_at = result["repayment_commitment"]
            call("record_repayment_commitment", due_at=due_at, **subject)
            call(
                "add_ticket_note", ticket_id=ticket, text=f"承诺：{due_at} 核实还款状态"
            )
            call(
                "schedule_followup",
                ticket_id=ticket,
                due_at=due_at,
                note="核实客户还款承诺",
            )
        if step == workflow.final and result.get("ok"):
            call("close_ticket", ticket_id=ticket, resolution_code="notified")
            for task in call("list_followups", ticket_id=ticket):
                if task["status"] == "pending":
                    call("cancel_followup", task_id=task["task_id"])
    if session["trigger_type"] == "user_visit":
        end = call(workflow.read_tool, **subject)
        return "CONFIRMED" if workflow.final.action in end["actions"] else "UNRESOLVED"
    return None


def evaluate_workflow(workflow, case, run):
    sessions, calls, actions = checks.trace_records(run["trajectory"])
    notice = actions[workflow.final.action]
    closed = any(a["seq"] > n["seq"] for a in actions["close_ticket"] for n in notice)
    unsafe = [
        row
        for step in workflow.milestones
        for row in calls[step.action]
        if row["result"].get("error") in {"status_not_verified", "parts_not_reserved"}
    ]
    unsafe.extend(
        row
        for row in calls["close_ticket"]
        if row["result"] == {"error": "close_before_notice"}
    )
    unsafe.extend(
        row for row in calls["record_repayment_commitment"] if not row["committed"]
    )
    all_milestones = all(actions[step.action] for step in workflow.milestones)
    offered = case["init"].get("offers_repayment_commitment", False)
    recorded = actions["record_repayment_commitment"]
    promised_review = (
        any(
            row["args"]["due_at"] == _iso(_case_time("D3 13:00"))
            and row["seq"] > recorded[0]["seq"]
            for row in actions["schedule_followup"]
        )
        if recorded
        else False
    )
    promise_satisfied = not offered or promised_review
    resolution = bool(notice and closed and all_milestones and promise_satisfied)
    return {
        "outcome": {"case_completed": resolution},
        "process": {
            "initial_review_scheduled": any(
                row["args"]["due_at"] == _iso(_case_time("D2 10:00"))
                for row in actions["schedule_followup"]
            ),
            "all_milestones_completed": all_milestones,
            "valid_actions_only": not unsafe,
            "promise_followup_scheduled": promise_satisfied,
        },
        "stages": {
            sid: {"passed": not any(row["session_id"] == sid for row in unsafe)}
            for sid in sessions
        },
        "metrics": {
            "case_resolution": resolution,
            "conversation_success": checks.conversation_success(sessions),
            "unsafe_action_rate": int(bool(unsafe)),
        },
    }
