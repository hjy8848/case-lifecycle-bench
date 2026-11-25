"""Scoring and end state for the migrated refund templates T1, T2 and T9.

All three are one refund on one order. What the agent does is judged on actions only (no
duplicate refund, a follow-up after every write, a retry exactly when the first attempt
failed, the hand-over exactly when the payment side or the customer calls for it, closing
only on a verified result). Whether it said the right words is a content check elsewhere.
"""

from datetime import datetime, timedelta

from .. import eval_checks as checks
from ..lifecycle import _case_time
from ..outcomes import OutcomeSpec

_ACTIVE = {"processing", "succeeded"}
_WINDOW = timedelta(hours=48)
_STUCK = timedelta(days=5)


def _refunds(case, state):
    order_id = case["init"]["order"]
    return [row for row in state["refunds"].values() if row["order_id"] == order_id]


def _failures(refund) -> int:
    return sum(entry["status"] == "failed" for entry in refund["history"])


def _stuck(refund, now) -> bool:
    return refund["status"] == "processing" and now >= datetime.fromisoformat(
        refund["history"][0]["t"]
    ) + _STUCK


def evaluate_refund(case: dict, run: dict) -> dict:
    # Imported here: evaluation imports this module for its dispatch table.
    from ..evaluation import _evaluation_result, _failure_checks

    order_id, user_id = case["init"]["order"], case["init"]["user"]
    state = run["final_state"]
    trajectory = run["trajectory"]
    horizon = _case_time(case["horizon"])
    sessions, calls, actions = checks.trace_records(trajectory)
    tickets = checks.case_tickets(state, user_id, order_id)
    refunds = _refunds(case, state)
    succeeded = [row for row in refunds if row["status"] == "succeeded"]
    closes = [row for row in actions["close_ticket"] if row["args"]["ticket_id"] in tickets]
    handoffs = [row for row in actions["escalate_to_human"] if row["args"].get("ticket_id") in tickets]
    notifications = checks.notifications_to(actions, user_id) + [
        row for row in trajectory if row["kind"] == "message" and row["role"] == "agent"
    ]

    replay = {row["refund_id"]: row for row in run["initial_state"]["refunds"].values()}
    duplicate_sessions, queries, succeeded_queries, failure_events = set(), [], [], []
    for record in trajectory:
        statuses = {row["status"] for row in replay.values() if row["order_id"] == order_id}
        if record["kind"] == "world_event" and record.get("duty"):
            failure_events.append(record)
        if record["kind"] == "tool_call":
            if record["tool"] == "issue_refund" and record["args"].get("order_id") == order_id:
                if statuses & _ACTIVE:
                    duplicate_sessions.add(record["session_id"])
            found = checks.refund_query(record, order_id)
            if found:
                queries.append((record, found))
                if "succeeded" in statuses and any(row["status"] == "succeeded" for row in found):
                    succeeded_queries.append(record)
        replay.update(record.get("changes", {}).get("refunds", {}))

    retries = [
        row
        for row in actions["retry_refund"]
        if replay.get(row["args"]["refund_id"], {}).get("order_id") == order_id
    ]
    issues = [row for row in actions["issue_refund"] if row["args"]["order_id"] == order_id]
    schedules = checks.scheduled_followups(actions)
    monitoring = [
        (row, checks.refund_followup_check(row, schedules, queries, tickets, _WINDOW))
        for row in issues + retries
    ]
    final_notices = checks.notifications_after(notifications, succeeded_queries)
    first_write = min((row["seq"] for row, _ in monitoring), default=None)

    failed_once = any(_failures(row) >= 1 for row in refunds)
    expected_retries = 1 if failed_once else 0
    third_channel = any(rows[0]["trigger_id"] == "V3" for rows in sessions.values())
    must_hand_over = not succeeded and (
        any(_failures(row) >= 2 or _stuck(row, horizon) for row in refunds) or third_channel
    )
    waiting = not succeeded and not must_hand_over

    process = {
        "no_duplicate_refund": not duplicate_sessions,
        "followup_after_each_write": bool(monitoring) and all(result[0] for _, result in monitoring),
        "retry_matches_failure": len(retries) == expected_retries,
        "escalation_matches_state": len(handoffs) == (1 if must_hand_over else 0),
        "customer_informed_of_escalation": all(
            any(checks.same_session(hand, note) for note in notifications) for hand in handoffs
        ),
        "customer_informed_while_waiting": not waiting
        or (
            first_write is not None
            and any(note["seq"] > first_write for note in notifications)
        ),
        "closure_matches_state": (not closes)
        if not succeeded
        else bool(closes)
        and all(any(note["seq"] < close["seq"] for note in final_notices) for close in closes),
        "followup_pending_while_waiting": not waiting
        or any(
            row["status"] == "pending"
            and row["ticket_id"] in tickets
            and datetime.fromisoformat(row["due_at"]) > horizon
            for row in state["followups"].values()
        ),
        "single_ticket_for_the_case": len(tickets) == 1,
    }
    stages = {}
    for session_id, rows in sessions.items():
        stages[session_id] = {
            "trigger_id": rows[0]["trigger_id"],
            "passed": session_id not in duplicate_sessions,
        }

    def kept(row, result):
        scheduled, fulfilled = result
        return fulfilled or (bool(scheduled) and checks.record_time(row) + _WINDOW > horizon)

    commitments = [kept(row, result) for row, result in monitoring]
    process["commitments_kept"] = all(commitments)
    realised = [
        _failure_checks(event, queries, retries, notifications)
        for event in failure_events
        if event.get("changes", {}).get("refunds")
    ]
    detected = bool(realised) and all(row[1] for row in realised)
    response = bool(realised) and all(row[2] for row in realised)
    outcome = {
        "refund_succeeded": bool(succeeded),
        "ticket_closed": bool(closes),
        "refund_failures": max((_failures(row) for row in refunds), default=0),
    }
    evaluation = _evaluation_result(
        case,
        outcome,
        process,
        stages,
        sessions,
        bool(succeeded) and bool(closes),
        detected,
        response,
        bool(duplicate_sessions),
        commitments,
    )
    evaluation["content"] = {}
    if not realised:
        evaluation["metrics"]["detection_rate"] = None
        evaluation["metrics"]["response_accuracy"] = None
    return evaluation


# -- outcomes ------------------------------------------------------------------


def business_outcome(case, run):
    refunds = _refunds(case, run["final_state"])
    if any(row["status"] == "succeeded" for row in refunds):
        return "resolved"
    horizon = _case_time(case["horizon"])
    if any(_failures(row) >= 2 for row in refunds):
        return "payment_failed"
    if any(_stuck(row, horizon) for row in refunds):
        return "stuck"
    return "in_progress"


def terminal(case, run):
    state = run["final_state"]
    tickets = checks.case_tickets(state, case["init"]["user"], case["init"]["order"])
    refunds = _refunds(case, state)
    return {
        "refunds_created": len(refunds),
        "refund_succeeded": any(row["status"] == "succeeded" for row in refunds),
        "case_tickets": len(tickets),
        "ticket_closed": any(state["tickets"][t]["status"] == "closed" for t in tickets),
        "followup_pending": any(
            row["ticket_id"] in tickets and row["status"] == "pending"
            for row in state["followups"].values()
        ),
        "handoffs": sum(row["ticket_id"] in tickets for row in state["handoffs"].values()),
    }


def ticket_closed(case, run):
    return terminal(case, run)["ticket_closed"]


def spec(oracle) -> OutcomeSpec:
    return OutcomeSpec(oracle, business_outcome, terminal, ticket_closed)
