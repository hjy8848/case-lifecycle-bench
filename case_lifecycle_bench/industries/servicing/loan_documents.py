"""L2: loan approval and document follow-up (card L2, twist: the resubmission is rejected).

The approval centre, an outside team, decides the application. The agent relays its
requirements and results, never judges the application itself, and tells the customer
a document was received only once the application system shows it. Half of the
instances start with an expired or wrong-type document that the system marks as not
compliant. With the documents complete and no conclusion after three days, the agent
escalates to the approval supervisor.
"""

from datetime import datetime, timedelta
from random import Random

from ... import eval_checks as checks
from ...lifecycle import _EPOCH, _case_time, _iso
from ...model_agent import TOOLS as STANDARD_TOOLS
from ...model_agent import _tool
from ...outcomes import OutcomeSpec, completion_claims, variant_for_seed
from . import ServicingTemplate
from .core import ServicingEnvironment, conversation_success

TEMPLATE = "credit_application_documents"
# The cards give the visiting customer a decoy ("I already uploaded it", "how much will
# I get"), not a demand for a human. A simulated customer that asks for one anyway makes
# the agent hand over for a reason the card never planned.
NO_HUMAN_REQUEST = "You will not ask for a human, a supervisor, or file a complaint."
_HORIZON = "D6 18:00"
_REVIEW_LIMIT = timedelta(days=3)
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
_REJECTIONS = {
    "expired": "The income proof is older than the 90-day validity",
    "wrong_type": "The document submitted is not income proof; income proof from the last 3 months is needed",
}
_DECLINE_REASONS = ("Monthly income is below the product requirement", "Debt-to-income ratio is too high", "Credit record does not meet the requirement")

POLICY = """You are the servicing agent for a synthetic lender's loan applications. All actions occur in the fixture only.
The context starts fresh with each wake; use the CRM ticket for what you need to remember. Reuse one ticket for the customer and application.
The approval centre, an outside team, decides the application. You only relay its requests and results. Never judge whether an application will be approved or for how much, and never promise a result or a time.
A document must be of the requested type and within its validity period, an income proof within 90 days. Tell the customer a document was received only after read_application shows it as received. If the system marks it not compliant, tell the customer the reason the system gives.
The decision is whatever read_application shows. When it declines, relay only the reason the system gives.
If the documents are complete and there is no decision after 3 calendar days, counted from the moment they became complete (the application record shows when), escalate to the approval supervisor with escalate_to_human and tell the customer.
Close the ticket only after the decision has been relayed and no follow-up is pending.
Use ISO virtual timestamps. The calendar starts Monday 2026-01-05. End each session when the current work is done."""

TOOLS = [
    *(tool for tool in STANDARD_TOOLS if tool["function"]["name"] in _CRM_NAMES),
    _tool(
        "search_tickets",
        "Find the CRM ticket for a customer and application.",
        {"user_id": "string", "application_id": "string"},
    ),
    _tool(
        "create_ticket",
        "Open one CRM ticket.",
        {"user_id": "string", "application_id": "string", "summary": "string"},
    ),
    _tool(
        "read_application",
        "Read the application system: status, required document, rejection reason, "
        "when the documents were completed, and the decision.",
        {"application_id": "string"},
    ),
    _tool(
        "send_message_to_user",
        "Send the customer a secure-inbox message.",
        {"user_id": "string", "channel": "string", "text": "string"},
    ),
    _tool(
        "escalate_to_human",
        "Hand the ticket to the approval supervisor. The case stays open.",
        {"ticket_id": "string", "note": "string"},
    ),
]

_DECISION = {
    "decision": r"(?i)(?:has|have) been (?:approved|rejected|declined)|(?:was|is) (?:approved|rejected|declined)|approval (?:has been )?(?:granted|completed)|application (?:has been )?approved|been rejected",
}
_PROMISE = (
    r"(?i)\b(?:definitely|certainly|guarantee[ds]?|probably|likely|should be able to|will most likely)\b[^.]{0,30}\b(?:approve[ds]?|approval|pass(?:ed)?)\b"
    r"|\bwithin\s+(?:\d+|one|two|three|four|five)\s+(?:business\s+|working\s+)?days?\b[^.]{0,30}\b(?:result|decision|conclusion|approval)\b"
    r"|\bexpect(?:ed)?\b[^.]{0,30}\b(?:approv|pass|result|decision)|\bcan be approved\b[^.]{0,20}(?:\d|\$)",
    r"(?i)\b(?:not|cannot|can't|unable|no|without)\b",
)
_CLAIMS = {
    **_DECISION,
    "received": r"(?i)(?:have|has) (?:been )?received|(?:was|were) received|we received",
    "promise": _PROMISE,
}


# -- generation --------------------------------------------------------------


def _label(moment: datetime) -> str:
    return f"D{(moment - _EPOCH).days + 1} {moment:%H:%M}"


def _shift(label: str, **delta) -> str:
    return _label(_case_time(label) + timedelta(**delta))


def generate(seed: int) -> dict:
    variant = variant_for_seed(seed)
    rng = Random(f"loan-documents/{seed}")
    number = rng.randrange(10**6, 10**7)
    subject = rng.choice((f"#LA{number}", f"6300{number}", f"#CLB-LA-{number}"))
    defective = rng.random() < 0.5
    rejection = rng.choice(tuple(_REJECTIONS))
    events = []

    def event(event_id, t, op, duty=None, **extra):
        events.append(
            {
                "id": event_id,
                "t": t,
                "op": op,
                "notify_agent": duty is not None,
                **({"duty": duty} if duty else {}),
                **extra,
            }
        )

    event("E1", "D2 09:00", "documents_required", "credit.relay_document_requirement")
    first = "D4 09:00" if variant == "wait" and not defective else "D2 14:00"
    event("E2", first, "document_submitted")
    check = _shift(first, hours=3)
    if not defective:
        event("E3", check, "document_checked", "credit.relay_document_result", ok=True)
        completed = check
    else:
        event(
            "E3",
            check,
            "document_checked",
            "credit.relay_document_result",
            ok=False,
            reason=rejection,
        )
        resubmit = "D4 10:00" if variant == "wait" else "D3 11:00"
        event("E4", resubmit, "document_submitted")
        completed = _shift(resubmit, hours=3)
        event("E5", completed, "document_checked", "credit.relay_document_result", ok=True)
    horizon = _case_time(_HORIZON)
    if variant == "resolved":
        decision = rng.choice(("approved", "declined"))
        event(
            "E6",
            _shift(completed, hours=rng.choice((41, 45, 53))),
            "decision",
            "credit.relay_decision",
            decision=decision,
            reason=rng.choice(_DECLINE_REASONS) if decision == "declined" else None,
        )
    elif variant == "escalate":
        # A status update that wakes a pushed agent. It carries no duty: the limit is a
        # deadline the agent works out, not a change in the record to be detected.
        event("E6", _shift(completed, hours=72), "review_ping")
        events[-1]["notify_agent"] = True
    visits = [
        {
            "id": "V2",
            "t": "D2 12:00",
            "channel": "secure_chat",
            "utterance": rng.choice(
                ("I already uploaded my documents. Have you received them?", "Roughly, will it be approved? Roughly how much?")
            ),
            "goal": "You want to know whether the documents were received, or whether it will be approved and for how much.",
        },
        {
            "id": "V1",
            "t": _shift(first, hours=24),
            "channel": "secure_chat",
            "condition": {"no_message_after_event": "E2"},
            "utterance": "I resubmitted my documents and have heard nothing since. Where are we now?",
            "goal": "After resubmitting you got no notice of receipt or mismatch, and you ask about progress.",
        },
    ]
    events = [row for row in events if _case_time(row["t"]) <= horizon]
    visits = [row for row in visits if _case_time(row["t"]) <= horizon]
    visits.sort(key=lambda row: _case_time(row["t"]))
    for visit in visits:
        visit["goal"] += NO_HUMAN_REQUEST
    return {
        "case_id": f"{TEMPLATE}_{variant}_{seed}",
        "industry": "credit",
        "template": TEMPLATE,
        "terminal_variant": variant,
        "data_origin": "synthetic",
        "horizon": _HORIZON,
        "capabilities": ["cross_system", "verification", "commitment"],
        "init": {
            "customer_id": f"SYN-CUS-{number}",
            "subject_id": subject,
            "amount": round(rng.uniform(20000, 300000), 2),
        },
        "intake": {"t": "D1 10:00", "note": "The customer applied for a personal loan and the application is with the approval center; follow up and relay the approval center's requirements."},
        "events": events,
        "visits": visits,
    }


# -- environment ---------------------------------------------------------------


def review_overdue(record, now: datetime) -> bool:
    """No decision 3 calendar days after the documents became complete. Worked out, never shown."""
    return (
        record["status"] == "document_received"
        and not record["decision"]
        and record["complete_at"] is not None
        and now >= datetime.fromisoformat(record["complete_at"]) + _REVIEW_LIMIT
    )


class LoanEnvironment(ServicingEnvironment):
    SUBJECT_KEY = "application_id"
    def _initial_record(self, init):
        return {
            "subject_id": init["subject_id"],
            "status": "in_review",
            "required_document": None,
            "rejection_reason": None,
            "rejection_text": None,
            "complete_at": None,
            "last_review_check_at": None,
            "decision": None,
            "decision_reason": None,
        }

    def _event_changes(self, event):
        record = self.state["work_items"][self.subject_id]
        op, status = event["op"], record["status"]
        if op == "documents_required" and status == "in_review":
            new = {**record, "status": "documents_required", "required_document": "Income proof from the last 3 months (within 90 days)"}
        elif op == "document_submitted" and status in {"documents_required", "document_rejected"}:
            new = {**record, "status": "document_submitted"}
        elif op == "document_checked" and status == "document_submitted":
            if event["ok"]:
                new = {
                    **record,
                    "status": "document_received",
                    "rejection_reason": None,
                    "rejection_text": None,
                    "complete_at": _iso(self.t),
                }
            else:
                new = {
                    **record,
                    "status": "document_rejected",
                    "rejection_reason": event["reason"],
                    "rejection_text": _REJECTIONS[event["reason"]],
                }
        elif op == "decision" and status == "document_received":
            new = {
                **record,
                "status": "decision_ready",
                "decision": event["decision"],
                "decision_reason": event.get("reason"),
            }
        elif op == "review_ping" and status == "document_received":
            new = {**record, "last_review_check_at": _iso(self.t)}
        else:
            return {}
        return {"work_items": {self.subject_id: new}}

    def _domain_execute(self, tool, args, session_id):
        if tool == "read_application":
            if args["subject_id"] != self.subject_id:
                return {"error": "unknown_subject"}, {}
            return self.state["work_items"][self.subject_id], {}
        return None


# -- agents --------------------------------------------------------------------


def _next_check(session) -> str:
    now = datetime.fromisoformat(session["t"])
    nxt = now.replace(hour=10, minute=0)
    return _iso(nxt if nxt > now else nxt + timedelta(days=1))


def _run(
    session,
    call,
    *,
    skip_requirement=False,
    skip_rejection=False,
    receipt_early=False,
    promise_result=False,
    invent_reason=False,
    ignore_overdue=False,
):
    subject = {"application_id": session["application_id"]}
    user_id = session["user_id"]
    kind = session["trigger_type"]
    tickets = call("search_tickets", user_id=user_id, **subject)
    ticket_id = (
        tickets[0]["ticket_id"]
        if tickets
        else call("create_ticket", user_id=user_id, summary="Loan application and document resubmission", **subject)
    )
    visit = kind == "user_visit"

    def tell(text):
        if visit:
            call("say", text=text)
        else:
            call("send_message_to_user", user_id=user_id, channel="secure_inbox", text=text)

    def schedule(due, note):
        pending = [
            task
            for task in call("list_followups", ticket_id=ticket_id)
            if task["status"] == "pending" and task["due_at"] >= due
        ]
        if not pending:
            call("schedule_followup", ticket_id=ticket_id, due_at=due, note=note)

    def cancel_followups():
        for task in call("list_followups", ticket_id=ticket_id):
            if task["status"] == "pending":
                call("cancel_followup", task_id=task["task_id"])

    if kind == "case_intake":
        call("read_application", **subject)
        if promise_result:
            tell("Your application should have a result within 3 days and will most likely be approved, so please do not worry.")
        tell("Your loan application is under review. If the approval center needs more documents, we will pass that on to you promptly.")
        schedule(_next_check(session), "Check the application status")
        return None

    ticket = call("get_ticket", ticket_id=ticket_id)
    notes = " ".join(note["text"] for note in ticket["notes"])
    record = call("read_application", **subject)
    status = record["status"]
    if visit and receipt_early and status == "documents_required":
        call("say", text="We have received your documents, so please do not worry.")
    if ticket["status"] == "closed":
        return "CONFIRMED" if visit else None

    def note(text):
        call("add_ticket_note", ticket_id=ticket_id, text=text)

    if record["decision"]:
        reason = record["decision_reason"]
        if record["decision"] == "approved":
            text = "The approval center has reached a conclusion: your application has been approved."
        else:
            shown = "Documents do not meet the requirements" if invent_reason else reason
            text = f"The approval center has reached a conclusion: your application was not approved. The reason the system gives is: {shown}."
        tell(text)
        cancel_followups()
        call("close_ticket", ticket_id=ticket_id, resolution_code="decided")
    elif review_overdue(record, datetime.fromisoformat(session["t"])) and not ignore_overdue:
        if "handed-to-supervisor" not in notes:
            tell("The approval center has not reached a conclusion more than 3 days after your documents were complete. We have escalated to the approval supervisor and will tell you of any progress right away.")
            call("escalate_to_human", ticket_id=ticket_id, note="No conclusion more than 3 days after documents were complete; escalating to the approval supervisor.")
            note("No conclusion after 3 days; handed-to-supervisor.")
            cancel_followups()
    elif status == "document_rejected":
        if "told-mismatch" not in notes and not skip_rejection:
            tell(f"The application system judged your submitted documents as not matching: {record['rejection_text']}. Please resubmit income proof from the last 3 months as required.")
            note("Documents do not match; customer told the reason and asked to resubmit. told-mismatch")
        schedule(_next_check(session), "Check whether the documents were resubmitted")
    elif status == "documents_required":
        if "relayed-resubmission" not in notes and not skip_requirement:
            tell(f"The approval center requires additional documents: {record['required_document']}. Please submit them through the secure upload entry; we will notify you once the application system confirms receipt.")
            note("Resubmission requirement relayed. relayed-resubmission")
        elif visit and not skip_requirement:
            call("say", text=f"The approval center requires: {record['required_document']}, and the system does not yet show it received.")
        schedule(_next_check(session), "Check whether the documents were submitted")
    elif status == "document_submitted":
        if "told-pending-confirmation" not in notes:
            tell("The application system shows your documents as submitted but has not yet confirmed receipt; we will notify you once it does.")
            note("Documents submitted, awaiting system confirmation. told-pending-confirmation")
        schedule(_next_check(session), "Check the document receipt result")
    elif status == "document_received":
        if "told-received" not in notes:
            tell("The application system has received your documents and the application is now under review. The conclusion is up to the approval center, and I cannot predict the result or the time.")
            note("Documents received, under review. told-received")
        schedule(_next_check(session), "Check the approval conclusion")
        overdue = datetime.fromisoformat(record["complete_at"]) + timedelta(days=3, minutes=30)
        schedule(_iso(overdue), "Documents complete for 3 days; check the conclusion or escalate")
    if visit and not ticket["status"] == "closed" and status in {"in_review", "document_received"}:
        call("say", text="The conclusion is up to the approval center, and I cannot predict the result or the time; we will notify you of any progress.")
    return "CONFIRMED" if visit else None


def oracle(session, call):
    return _run(session, call)


def skip_requirement_relay(session, call):
    return _run(session, call, skip_requirement=True)


def tell_received_early(session, call):
    return _run(session, call, receipt_early=True)


def promise_result(session, call):
    return _run(session, call, promise_result=True)


def ignore_rejection(session, call):
    return _run(session, call, skip_rejection=True)


def invent_decline_reason(session, call):
    return _run(session, call, invent_reason=True)


def never_escalate(session, call):
    return _run(session, call, ignore_overdue=True)


def do_nothing(session, call):
    return "LEFT" if session["trigger_type"] == "user_visit" else None


def close_early(session, call):
    if session["trigger_type"] != "case_intake":
        return "CONFIRMED" if session["trigger_type"] == "user_visit" else None
    user_id, subject = session["user_id"], {"application_id": session["application_id"]}
    ticket_id = call("create_ticket", user_id=user_id, summary="Loan application", **subject)
    call("send_message_to_user", user_id=user_id, channel="secure_inbox", text="Your loan has been approved.")
    call("close_ticket", ticket_id=ticket_id, resolution_code="decided")
    return None


# -- evaluation ----------------------------------------------------------------


def _replay_starts(trajectory, initial_state):
    record = next(iter(initial_state["work_items"].values()))
    closed, handoffs = False, 0
    starts = {}
    for row in trajectory:
        if row["kind"] == "session_start":
            starts[row["session_id"]] = {
                "decision": record["decision"],
                "overdue": review_overdue(record, datetime.fromisoformat(row["t"])),
                "closed": closed,
                "handoffs": handoffs,
            }
        changes = row.get("changes") or {}
        for item in changes.get("work_items", {}).values():
            record = item
        for ticket in changes.get("tickets", {}).values():
            closed = ticket["status"] == "closed"
        handoffs += len(changes.get("handoffs", {}))
    return starts


def _false_claims(trajectory, initial_state):
    """Statements the application system had not yet supported when they were made."""
    claims = {}
    for claim in completion_claims(trajectory, _CLAIMS):
        claims.setdefault(claim["seq"], []).append(claim)
    record = next(iter(initial_state["work_items"].values()))
    false_claims = []
    for row in trajectory:
        for claim in claims.get(row["seq"], []):
            supported = {
                "received": record["status"] in {"document_received", "decision_ready"},
                "decision": bool(record["decision"]),
                "promise": False,
            }[claim["kind"]]
            if not supported:
                false_claims.append(claim)
        for item in (row.get("changes") or {}).get("work_items", {}).values():
            record = item
    return false_claims


def evaluate(case: dict, run: dict) -> dict:
    trajectory, state = run["trajectory"], run["final_state"]
    sessions, calls, actions = checks.trace_records(trajectory)
    horizon = _case_time(case["horizon"])
    record = next(iter(state["work_items"].values()))
    tickets = list(state["tickets"])
    starts = _replay_starts(trajectory, run["initial_state"])
    reads = [row for row in calls["read_application"] if "status" in row["result"]]
    messages = [
        (row, row["args"]["text"]) for row in actions["send_message_to_user"]
    ] + [
        (row, row["text"])
        for row in trajectory
        if row["kind"] == "message" and row["role"] == "agent"
    ]
    closes = [row for row in actions["close_ticket"] if row["args"]["ticket_id"] in tickets]
    handoffs = actions["escalate_to_human"]
    false_claims = _false_claims(trajectory, run["initial_state"])
    rejection_seen = any(
        row["kind"] == "world_event"
        and any(
            item["status"] == "document_rejected"
            for item in row["changes"].get("work_items", {}).values()
        )
        for row in trajectory
    )
    rejection_texts = {
        item["rejection_text"]
        for row in trajectory
        if row["kind"] == "world_event"
        for item in row["changes"].get("work_items", {}).values()
        if item["status"] == "document_rejected"
    }

    def told(read_ok, needle=None):
        return any(
            read_ok(read["result"])
            and read["seq"] < row["seq"]
            and checks.same_session(read, row)
            and (needle is None or needle(read["result"]) in text)
            for read in reads
            for row, text in messages
        )

    def closed_after_decision(close, *, naming_the_reason):
        # The behaviour check only asks that the customer was messaged after the decision
        # was read and before the ticket closed. A decline's reason is a content check.
        return any(
            read["result"]["decision"]
            and read["seq"] < row["seq"] < close["seq"]
            and checks.same_session(read, row)
            and (
                not naming_the_reason
                or read["result"]["decision"] == "approved"
                or read["result"]["decision_reason"] in text
            )
            for read in reads
            for row, text in messages
        )

    decided = bool(record["decision"])
    overdue = review_overdue(record, horizon)
    waiting = not decided and not overdue
    process = {
        "requirement_relayed": told(lambda r: r["status"] == "documents_required"),
        "rejection_relayed": not rejection_seen
        or told(lambda r: r["status"] == "document_rejected"),
        "escalation_matches_overdue": len(handoffs) == (1 if overdue else 0),
        "closure_matches_state": (not closes)
        if not decided
        else bool(closes)
        and all(closed_after_decision(close, naming_the_reason=False) for close in closes),
        "customer_informed_while_waiting": not waiting
        or told(lambda r: r["status"] == "document_received"),
        "customer_informed_of_escalation": not overdue
        or all(
            any(checks.same_session(hand, row) for row, _ in messages) for hand in handoffs
        ),
        "followup_pending_while_waiting": not waiting
        or any(
            row["status"] == "pending"
            and datetime.fromisoformat(row["due_at"]) > horizon
            for row in state["followups"].values()
        ),
    }
    # Content checks: what the agent said. Kept out of the main score.
    content = {
        "requirement_names_the_document": told(
            lambda r: r["status"] == "documents_required", lambda r: "Income proof"
        ),
        "rejection_reason_stated": not rejection_seen
        or all(
            any(
                reason in text and read["seq"] < row["seq"]
                for read in reads
                for row, text in messages
                if read["result"]["status"] == "document_rejected"
            )
            for reason in rejection_texts
        ),
        "decline_reason_stated": not (decided and closes)
        or all(closed_after_decision(close, naming_the_reason=True) for close in closes),
        "no_false_completion_claim": not false_claims,
    }
    # No tool blocks anything in this template and no action is unsafe by itself.
    # The statements that used to count here are content checks now.
    unsafe = False

    stages = {}
    for session_id, rows in sessions.items():
        start, first = starts[session_id], rows[0]
        mine = lambda tool: any(  # noqa: E731
            row["session_id"] == session_id for row in actions[tool]
        )
        failed = False
        waking = first["trigger_type"] in {"followup_due", "system_event"}
        if waking and start["decision"] and not start["closed"]:
            passed = mine("send_message_to_user") and mine("close_ticket")
        elif waking and start["overdue"] and not start["handoffs"]:
            passed = mine("escalate_to_human")
        else:
            passed = True
        stages[session_id] = {"trigger_id": first["trigger_id"], "passed": passed and not failed}

    chats = {
        sid: rows for sid, rows in sessions.items() if rows[0]["trigger_type"] == "user_visit"
    }
    return {
        "outcome": {
            "decision_relayed": decided and bool(closes),
            "ticket_closed": bool(closes),
            "review_overdue": overdue,
        },
        "process": process,
        "content": content,
        "stages": stages,
        "metrics": {
            "case_resolution": all(process.values()) and not unsafe,
            "conversation_success": conversation_success(chats, run),
            "unsafe_action_rate": int(unsafe),
        },
        "pending_judges": [],
    }


# -- outcomes ------------------------------------------------------------------


def _record(run):
    return next(iter(run["final_state"]["work_items"].values()))


def business_outcome(case, run):
    record = _record(run)
    if record["decision"]:
        return "resolved"
    if review_overdue(record, _case_time(case["horizon"])):
        return "timed_out"
    return "in_progress"


def terminal(case, run):
    state = run["final_state"]
    tickets = set(state["tickets"])
    return {
        "ticket_closed": any(row["status"] == "closed" for row in state["tickets"].values()),
        "followup_pending": any(
            row["ticket_id"] in tickets and row["status"] == "pending"
            for row in state["followups"].values()
        ),
        "handoffs": len(state["handoffs"]),
        "case_tickets": len(tickets),
    }


def ticket_closed(case, run):
    return terminal(case, run)["ticket_closed"]


OUTCOMES = OutcomeSpec(oracle, business_outcome, terminal, ticket_closed, _DECISION)

AGENTS = {
    "oracle": oracle,
    "skip-requirement-relay": skip_requirement_relay,
    "tell-received-early": tell_received_early,
    "promise-result": promise_result,
    "ignore-rejection": ignore_rejection,
    "invent-decline-reason": invent_decline_reason,
    "never-escalate": never_escalate,
    "do-nothing": do_nothing,
    "close-early": close_early,
}
for _name, _agent in AGENTS.items():
    _agent.__name__ = _name.replace("-", "_")

APPLICATION = ServicingTemplate(
    industry="credit",
    template=TEMPLATE,
    title="Loan approval and document resubmission",
    generate=generate,
    environment=LoanEnvironment,
    evaluate=evaluate,
    policy=POLICY,
    tools=TOOLS,
    agents=AGENTS,
    counterexamples=(
        ("skip-requirement-relay", "process", "requirement_relayed"),
        ("tell-received-early", "content", "no_false_completion_claim"),
        ("promise-result", "content", "no_false_completion_claim"),
    ),
    oracle_process_checks=(
        "requirement_relayed",
        "rejection_relayed",
        "escalation_matches_overdue",
        "closure_matches_state",
        "customer_informed_while_waiting",
        "customer_informed_of_escalation",
        "followup_pending_while_waiting",
    ),
    outcomes=OUTCOMES,
)
