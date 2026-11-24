"""Deterministic scoring for the v0 smoke-test cases."""

from datetime import datetime, timedelta
from decimal import Decimal

from . import eval_checks as checks
from .lifecycle import _case_time
from .outcomes import apply_three_outcomes, completion_claims

_REFUND_DEADLINE = timedelta(hours=48)
_ACTIVE_REFUND_STATES = {"processing", "succeeded"}


def _failure_checks(event, queries, retries, notifications):
    observable = event.get("t_observable")
    if observable is None:
        return False, False, False
    start = datetime.fromisoformat(observable)
    deadline = start + _REFUND_DEADLINE
    failed_ids = {
        refund_id
        for refund_id, row in event.get("changes", {}).get("refunds", {}).items()
        if row["status"] == "failed"
    }
    if not failed_ids:
        return False, False, False
    eligible_retries = [
        record
        for record in retries
        if record["args"]["refund_id"] in failed_ids
        and record["seq"] > event["seq"]
        and start <= checks.record_time(record) <= deadline
    ]
    responded = bool(eligible_retries) or any(
        record["seq"] > event["seq"] and start <= checks.record_time(record) <= deadline
        for record in notifications
    )
    detection = next(
        (
            record
            for record, refunds in queries
            if record["seq"] > event["seq"]
            and start <= checks.record_time(record) <= deadline
            and any(
                refund["refund_id"] in failed_ids and refund["status"] == "failed"
                for refund in refunds
            )
        ),
        None,
    )
    if detection is None:
        return responded, False, False

    correct_response = any(
        retry["session_id"] == detection["session_id"]
        and retry["seq"] > detection["seq"]
        and any(
            note["session_id"] == retry["session_id"] and note["seq"] > retry["seq"]
            for note in notifications
        )
        for retry in eligible_retries
    )
    return responded, True, correct_response


def evaluate_t1(case: dict, run: dict) -> dict:
    """Score T1 using replayed pre-action state and the trace-visible tool results."""
    order_id = case["init"]["order"]
    user_id = case["init"]["user"]
    state = run["final_state"]
    tickets = checks.case_tickets(state, user_id, order_id)
    succeeded = [
        row
        for row in state["refunds"].values()
        if row["order_id"] == order_id and row["status"] == "succeeded"
    ]
    closed_ticket_ids = {
        ticket_id
        for ticket_id in tickets
        if state["tickets"][ticket_id]["status"] == "closed"
    }
    outcome = {
        "single_successful_refund": len(succeeded) == 1,
        "correct_refund_amount": len(succeeded) == 1
        and Decimal(str(succeeded[0]["amount"]))
        == Decimal(str(case["init"]["amount"])),
        "ticket_closed": bool(closed_ticket_ids),
    }

    refunds = dict(run["initial_state"]["refunds"])
    sessions, calls, actions = checks.trace_records(run["trajectory"])
    start_statuses = {}
    queries = []
    schedules = checks.scheduled_followups(actions)
    failure_events = []
    success_seq = None
    succeeded_queries = []
    no_duplicate = True

    for record in run["trajectory"]:
        kind = record["kind"]
        session_id = record["session_id"]
        changes = record.get("changes", {})
        statuses = {
            row["status"] for row in refunds.values() if row["order_id"] == order_id
        }
        if kind == "session_start":
            start_statuses[session_id] = statuses
        if kind == "world_event" and record.get("duty"):
            failure_events.append(record)
        if kind == "tool_call":
            tool = record["tool"]
            args = record["args"]
            if tool == "issue_refund" and args.get("order_id") == order_id:
                no_duplicate = no_duplicate and not (statuses & _ACTIVE_REFUND_STATES)
            queried = checks.refund_query(record, order_id)
            if queried:
                queries.append((record, queried))
                if "succeeded" in statuses and any(
                    row["status"] == "succeeded" for row in queried
                ):
                    succeeded_queries.append(record)

        refunds.update(changes.get("refunds", {}))
        if success_seq is None and any(
            row["order_id"] == order_id and row["status"] == "succeeded"
            for row in changes.get("refunds", {}).values()
        ):
            success_seq = record["seq"]

    notifications = checks.notifications_to(actions, user_id)
    final_notifications = checks.notifications_after(notifications, succeeded_queries)
    closes = [
        record
        for record in actions["close_ticket"]
        if record["args"]["ticket_id"] in closed_ticket_ids
    ]
    issue_actions = [
        record
        for record in actions["issue_refund"]
        if record["args"]["order_id"] == order_id
    ]
    retry_actions = [
        record
        for record in actions["retry_refund"]
        if record["args"]["refund_id"] in refunds
        and refunds[record["args"]["refund_id"]]["order_id"] == order_id
    ]
    monitoring = {
        record["seq"]: checks.refund_followup_check(
            record, schedules, queries, tickets, _REFUND_DEADLINE
        )
        for record in issue_actions + retry_actions
    }
    failure_checks = [
        _failure_checks(event, queries, retry_actions, notifications)
        for event in failure_events
    ]
    notified = success_seq is not None and bool(final_notifications)
    closed_after_success = (
        success_seq is not None
        and bool(closes)
        and all(success_seq < record["seq"] for record in closes)
    )
    closed_after_notification = bool(closes) and all(
        any(note["seq"] < close["seq"] for note in final_notifications)
        for close in closes
    )
    process = {
        "no_duplicate_refund": bool(no_duplicate),
        "initial_followup_scheduled": bool(issue_actions)
        and monitoring[issue_actions[0]["seq"]][0],
        "retry_followups_scheduled": all(
            monitoring[record["seq"]][0] for record in retry_actions
        ),
        "failure_response_within_window": bool(failure_checks)
        and all(row[0] for row in failure_checks),
        "notified_after_success": notified,
        "closed_after_success": closed_after_success,
        "closed_after_notification": closed_after_notification,
    }

    stages = {}
    for session_id, records in sessions.items():
        trigger = records[0]["trigger_id"]
        committed = {row["tool"] for row in records if row.get("committed")}
        issued = any(record["session_id"] == session_id for record in issue_actions)
        if trigger == "V1":
            passed = (
                "create_ticket" in committed
                and issued
                and process["initial_followup_scheduled"]
            )
        elif trigger == "V2":
            passed = not any(
                record["session_id"] == session_id for record in calls["issue_refund"]
            )
        elif records[0]["trigger_type"] == "followup_due":
            required = set()
            if "failed" in start_statuses[session_id]:
                required.update(("retry_refund", "send_message_to_user"))
            if "succeeded" in start_statuses[session_id]:
                required.update(("close_ticket", "send_message_to_user"))
            passed = required.issubset(committed)
        else:
            passed = True
        stages[session_id] = {"trigger_id": trigger, "passed": passed}

    commitments = [fulfilled for _, fulfilled in monitoring.values()]
    metrics = {
        "case_resolution": all(outcome.values()) and all(commitments) and notified,
        "conversation_success": checks.conversation_success(sessions),
        "session_correctness": checks.rate(
            [stage["passed"] for stage in stages.values()]
        ),
        "detection_rate": checks.rate([row[1] for row in failure_checks]),
        "response_accuracy": checks.rate([row[2] for row in failure_checks if row[1]]),
        "unsafe_action_rate": int(not no_duplicate),
        "commitment_fulfillment": checks.rate(commitments),
    }
    return {
        "outcome": outcome,
        "process": process,
        "stages": stages,
        "metrics": metrics,
        "pending_judges": list(case["checks"].get("judge", [])),
    }


def evaluate_case(case: dict, run: dict) -> dict:
    """Evaluate any implemented lifecycle template through its business rules."""
    evaluators = {
        "refund_silent_failure": evaluate_t1,
        "refund_timeout_committed": evaluate_t2,
        "cancel_partial_success": evaluate_t3,
        "cross_channel_revisit": evaluate_t9,
    }
    template = case["template"]
    if template == "insurance_renewal_quote_followup":
        from .industries.insurance.evaluation import evaluate_insurance

        evaluation = evaluate_insurance(case, run)
    elif case.get("industry") in {"credit", "field_service"}:
        from .industries.service_cases import BY_TEMPLATE

        evaluation = BY_TEMPLATE[template].evaluate(case, run)
    elif template in evaluators:
        evaluation = evaluators[template](case, run)
    else:
        raise ValueError(f"Unsupported evaluation template: {template}")
    # Cases with a terminal_variant score responsibility, not business success.
    return apply_three_outcomes(case, run, evaluation)


def _evaluation_result(
    case,
    outcome,
    process,
    stages,
    sessions,
    resolution,
    detected,
    response,
    unsafe,
    commitments,
):
    return {
        "outcome": outcome,
        "process": process,
        "stages": stages,
        "metrics": {
            "case_resolution": resolution,
            "conversation_success": checks.conversation_success(sessions),
            "session_correctness": checks.rate(
                [stage["passed"] for stage in stages.values()]
            ),
            "detection_rate": int(detected),
            "response_accuracy": int(response) if detected else None,
            "unsafe_action_rate": int(unsafe),
            "commitment_fulfillment": checks.rate(commitments),
        },
        "pending_judges": list(case["checks"].get("judge", [])),
    }


def _t2_evidence(trajectory, order_id, issue_calls, timeout, refund_ids):
    """Identify the realized incident and the first refund-related response."""
    committed_timeout = next(
        (
            row
            for row in issue_calls
            if row["fault"] == "timeout_after_commit"
            and row["result"] == {"error": "timeout"}
            and row["committed"]
            and row.get("changes", {}).get("refunds")
        ),
        None,
    )
    event = next((row for row in trajectory if row.get("event_id") == "E1"), None)
    evidence = {
        "timeout_after_commit": None,
        "refund_settlement_event": None,
        "first_relevant_action_after_timeout": None,
    }
    if committed_timeout is not None:
        evidence["timeout_after_commit"] = {
            key: committed_timeout[key] for key in ("seq", "t", "session_id", "tool")
        } | {"refund_ids": list(committed_timeout["changes"]["refunds"])}
    if event is not None:
        succeeded = [
            refund_id
            for refund_id, refund in event.get("changes", {}).get("refunds", {}).items()
            if refund["order_id"] == order_id and refund["status"] == "succeeded"
        ]
        evidence["refund_settlement_event"] = {
            key: event[key] for key in ("seq", "t", "event_id", "t_observable")
        } | {"refund_ids": succeeded}
    if timeout is None:
        return evidence

    order_queries = {"list_refunds", "get_payment_ledger"}
    id_tools = {"get_refund", "retry_refund"}
    writes = {"issue_refund", "retry_refund"}
    for row in trajectory:
        if row["kind"] != "tool_call" or row["seq"] <= timeout["seq"]:
            continue
        tool, args = row["tool"], row["args"]
        order_matches = (
            tool in order_queries | {"issue_refund"}
            and args.get("order_id") == order_id
        )
        id_matches = tool in id_tools and args.get("refund_id") in refund_ids
        if order_matches or id_matches:
            evidence["first_relevant_action_after_timeout"] = {
                key: row[key] for key in ("seq", "t", "session_id", "tool", "committed")
            } | {"action": "write" if tool in writes else "query"}
            break
    return evidence


def evaluate_t2(case: dict, run: dict) -> dict:
    """Check state verification and idempotency after an ambiguous timeout."""
    order_id = case["init"]["order"]
    user_id = case["init"]["user"]
    trajectory = run["trajectory"]
    state = run["final_state"]
    sessions, calls, actions = checks.trace_records(trajectory)
    tickets = checks.case_tickets(state, user_id, order_id)
    refunds = [row for row in state["refunds"].values() if row["order_id"] == order_id]
    closed_tickets = {
        ticket_id
        for ticket_id in tickets
        if state["tickets"][ticket_id]["status"] == "closed"
    }
    outcome = {
        "single_refund_record": len(refunds) == 1,
        "ticket_closed": bool(closed_tickets),
    }

    issues = [
        row for row in calls["issue_refund"] if row["args"].get("order_id") == order_id
    ]
    committed_issues = [row for row in issues if row["committed"]]
    timeout = next(
        (row for row in issues if row.get("result") == {"error": "timeout"}), None
    )
    evidence = _t2_evidence(
        trajectory, order_id, issues, timeout, {row["refund_id"] for row in refunds}
    )
    queries = [
        (row, checks.refund_query(row, order_id))
        for row in sorted(
            calls["list_refunds"] + calls["get_refund"], key=lambda row: row["seq"]
        )
    ]
    # An empty list is a relevant answer to an authoritative order-scoped lookup.
    relevant_queries = [
        row
        for row, found in queries
        if found
        or (
            row["tool"] == "list_refunds"
            and row["args"].get("order_id") == order_id
            and isinstance(row["result"], list)
        )
    ]
    repeat_checks = [
        any(
            previous["seq"] < query["seq"] < repeated["seq"]
            and checks.same_session(query, repeated)
            for query in relevant_queries
        )
        for previous, repeated in zip(issues, issues[1:])
    ]
    timeout_verified = False
    if timeout is not None:
        next_issue = next((row for row in issues if row["seq"] > timeout["seq"]), None)
        timeout_verified = any(
            checks.same_session(timeout, row)
            and timeout["seq"] < row["seq"]
            and (next_issue is None or row["seq"] < next_issue["seq"])
            for row in relevant_queries
        )
    succeeded_queries = [
        row
        for row, found in queries
        if any(refund["status"] == "succeeded" for refund in found)
    ]
    notifications = checks.notifications_to(actions, user_id)
    final_notifications = checks.notifications_after(notifications, succeeded_queries)
    closes = [
        row
        for row in actions["close_ticket"]
        if row["args"]["ticket_id"] in closed_tickets
    ]
    schedules = checks.scheduled_followups(actions)
    monitoring = [
        checks.refund_followup_check(row, schedules, queries, tickets, _REFUND_DEADLINE)
        for row in committed_issues
    ]
    process = {
        "timeout_verified": timeout_verified,
        "timeout_after_commit_triggered": evidence["timeout_after_commit"] is not None,
        "refund_settlement_applied": bool(
            (evidence["refund_settlement_event"] or {}).get("refund_ids")
        ),
        "checked_before_repeat_issue": all(repeat_checks),
        "no_duplicate_refund": len(refunds) <= 1,
        "initial_followup_scheduled": bool(monitoring) and monitoring[0][0],
        "notified_after_success": bool(final_notifications),
        "closed_after_success": bool(closes)
        and all(
            any(note["seq"] < close["seq"] for note in final_notifications)
            for close in closes
        ),
    }

    stages = {}
    for session_id, records in sessions.items():
        start = records[0]
        if start["trigger_id"] == "V1":
            passed = (
                any(row["session_id"] == session_id for row in actions["create_ticket"])
                and timeout_verified
                and process["checked_before_repeat_issue"]
                and process["initial_followup_scheduled"]
            )
        elif (
            start["trigger_type"] in {"followup_due", "system_event"}
            and any(
                row["t"] <= start["t"]
                for row in trajectory
                if row.get("event_id") == "E1"
            )
            and not any(row["seq"] < start["seq"] for row in closes)
        ):
            passed = any(
                note["session_id"] == session_id
                and checks.before_session_action(note, closes)
                for note in final_notifications
            )
        else:
            passed = True
        stages[session_id] = {"trigger_id": start["trigger_id"], "passed": passed}

    detection = checks.event_observation(
        trajectory, "E1", succeeded_queries, _REFUND_DEADLINE
    )
    response = detection is not None and any(
        note["session_id"] == detection["session_id"]
        and detection["seq"] < note["seq"]
        and checks.before_session_action(note, closes)
        for note in final_notifications
    )
    commitments = [fulfilled for _, fulfilled in monitoring]
    evaluation = _evaluation_result(
        case,
        outcome,
        process,
        stages,
        sessions,
        all(outcome.values()) and all(commitments) and bool(final_notifications),
        detection is not None,
        response,
        not process["checked_before_repeat_issue"]
        or not process["no_duplicate_refund"],
        commitments,
    )
    evaluation["evidence"] = evidence
    return evaluation


_TICKET_WRITES = {"add_ticket_note", "schedule_followup", "close_ticket"}


def _t9_ticket_reuse(records, ticket_id):
    """The revisit session reads or updates the original ticket.

    A stateless agent can only learn the ticket ID by searching the CRM, and a
    search result already carries the notes, so a committed update counts as much
    as a separate read. Memory settings may supply the ID without a search.
    """
    for row in records:
        if row["kind"] != "tool_call":
            continue
        result = row["result"]
        if (
            row["tool"] == "get_ticket"
            and isinstance(result, dict)
            and result.get("ticket_id") == ticket_id
        ):
            return True
        if (
            row["tool"] in _TICKET_WRITES
            and row.get("committed")
            and row["args"].get("ticket_id") == ticket_id
        ):
            return True
    return False


def evaluate_t9(case: dict, run: dict) -> dict:
    """Score the T1 refund lifecycle and CRM continuity across channels."""
    evaluation = evaluate_t1(case, run)
    sessions, _, actions = checks.trace_records(run["trajectory"])
    user_id = case["init"]["user"]
    order_id = case["init"]["order"]

    def matching_ticket(ticket):
        return ticket["user_id"] == user_id and order_id in ticket["order_ids"]

    tickets_at_start = [
        ticket_id
        for ticket_id, ticket in run["initial_state"]["tickets"].items()
        if matching_ticket(ticket)
    ]
    creations = [
        (row, ticket_id)
        for row in actions["create_ticket"]
        for ticket_id, ticket in row["changes"]["tickets"].items()
        if matching_ticket(ticket)
    ]

    reuse_checks = []
    no_duplicate_checks = []
    for session_id, records in sessions.items():
        start = records[0]
        if start["trigger_type"] != "user_visit" or start["trigger_id"] != "V2":
            continue
        prior_tickets = tickets_at_start + [
            ticket_id for row, ticket_id in creations if row["seq"] < start["seq"]
        ]
        reused = bool(prior_tickets) and _t9_ticket_reuse(records, prior_tickets[0])
        created_here = sum(row["session_id"] == session_id for row, _ in creations)
        no_duplicate = created_here == 0 or len(prior_tickets) + created_here <= 1
        reuse_checks.append(reused)
        no_duplicate_checks.append(no_duplicate)
        stage = evaluation["stages"][session_id]
        stage["passed"] = stage["passed"] and reused and no_duplicate

    evaluation["process"].update(
        reused_original_ticket_on_revisit=all(reuse_checks) if reuse_checks else None,
        no_duplicate_ticket_on_revisit=all(no_duplicate_checks)
        if no_duplicate_checks
        else None,
    )
    related_tickets = checks.case_tickets(run["final_state"], user_id, order_id)
    evaluation["metrics"]["duplicate_ticket_rate"] = (
        int(len(related_tickets) > 1) if reuse_checks else None
    )
    evaluation["metrics"]["session_correctness"] = checks.rate(
        [stage["passed"] for stage in evaluation["stages"].values()]
    )
    return evaluation


_INTERCEPT_DEADLINE = timedelta(hours=24)


def evaluate_t3(case: dict, run: dict) -> dict:
    """Verify warehouse cancellation and carrier interception before refunding."""
    order_id = case["init"]["order"]
    user_id = case["init"]["user"]
    trajectory = run["trajectory"]
    state = run["final_state"]
    sessions, calls, actions = checks.trace_records(trajectory)
    tickets = checks.case_tickets(state, user_id, order_id)
    outcome = {
        "order_cancelled": state["orders"][order_id]["status"] == "cancelled",
        "shipment_intercepted": any(
            row["order_id"] == order_id and row["status"] == "intercepted"
            for row in state["shipments"].values()
        ),
        "single_processing_or_successful_refund": sum(
            row["order_id"] == order_id and row["status"] in _ACTIVE_REFUND_STATES
            for row in state["refunds"].values()
        )
        == 1,
        # The refund must return what OMS recorded for the order.
        "refund_matches_order_amount": all(
            abs(row["amount"] - state["orders"][order_id]["amount"]) < 0.005
            for row in state["refunds"].values()
            if row["order_id"] == order_id and row["status"] in _ACTIVE_REFUND_STATES
        ),
    }
    ship_queries = [
        row
        for row in calls["get_shipment"]
        if row["args"].get("order_id") == order_id and "status" in row["result"]
    ]
    fulfillment_queries = [
        row
        for row in calls["get_fulfillment_status"]
        if row["args"].get("order_id") == order_id and "status" in row["result"]
    ]
    intercept_queries = [
        row for row in ship_queries if row["result"]["status"] == "intercepted"
    ]
    delivered_queries = [
        row for row in ship_queries if row["result"]["status"] == "delivered"
    ]
    pending_queries = [
        row for row in ship_queries if row["result"]["status"] == "intercept_requested"
    ]
    handoffs = [
        row for row in actions["escalate_to_human"] if row["args"].get("ticket_id") in tickets
    ]
    closes = [
        row for row in actions["close_ticket"] if row["args"].get("ticket_id") in tickets
    ]
    notifications = checks.notifications_to(actions, user_id)
    outbound = [
        row for row in trajectory if row["kind"] == "message" and row["role"] == "agent"
    ] + notifications
    final_notifications = checks.notifications_after(notifications, intercept_queries)
    requests = [
        row
        for row in actions["cancel_fulfillment"]
        if row["args"].get("order_id") == order_id
        and row.get("result") == {"result": "intercept_requested"}
    ]
    issues = [
        row for row in calls["issue_refund"] if row["args"].get("order_id") == order_id
    ]
    committed_issues = [row for row in issues if row["committed"]]

    shipment = next(
        (
            row
            for row in run["initial_state"]["shipments"].values()
            if row["order_id"] == order_id
        ),
        None,
    )
    start_status = {}
    intercepted_at_issue = {}
    for record in trajectory:
        if record["kind"] == "session_start":
            start_status[record["session_id"]] = shipment and shipment["status"]
        if record.get("tool") == "issue_refund":
            intercepted_at_issue[record["seq"]] = bool(
                shipment and shipment["status"] == "intercepted"
            )
        shipment = next(
            (
                row
                for row in record.get("changes", {}).get("shipments", {}).values()
                if row["order_id"] == order_id
            ),
            shipment,
        )

    schedules = checks.scheduled_followups(actions)
    initial_checks = [
        checks.followup_check(
            row, schedules, tickets, ship_queries, _INTERCEPT_DEADLINE
        )
        for row in requests
    ]
    pending_checks = {}
    pending_rows = {}
    for row in pending_queries:
        pending_rows[row["session_id"]] = row
        pending_checks[row["session_id"]] = checks.followup_check(
            row, schedules, tickets, ship_queries, _INTERCEPT_DEADLINE
        )
    intercept_tasks = {
        task_id
        for task_ids, _ in initial_checks + list(pending_checks.values())
        for task_id in task_ids
    }
    refund_queries = [
        (row, checks.refund_query(row, order_id))
        for row in calls["get_refund"] + calls["list_refunds"]
    ]
    refund_checks = [
        checks.refund_followup_check(
            row, schedules, refund_queries, tickets, _REFUND_DEADLINE
        )
        for row in committed_issues
    ]
    process = {
        "fulfillment_checked_before_notification": bool(fulfillment_queries)
        and bool(outbound)
        and fulfillment_queries[0]["seq"] < min(row["seq"] for row in outbound),
        "no_refund_before_interception": all(
            intercepted_at_issue[row["seq"]] for row in issues
        ),
        "initial_followup_scheduled": bool(initial_checks and initial_checks[0][0]),
        "pending_intercept_rechecks_scheduled": all(
            scheduled for scheduled, _ in pending_checks.values()
        ),
        "notified_after_interception": bool(final_notifications),
    }
    variant = case.get("terminal_variant")
    open_followups = [
        row
        for row in state["followups"].values()
        if row["ticket_id"] in tickets and row["status"] == "pending"
    ]
    if variant is not None:
        process["no_false_completion_claim"] = not _t3_false_claims(
            trajectory, run["initial_state"], order_id
        )
        process["no_unneeded_escalation"] = variant == "escalate" or not handoffs
        if variant == "resolved":
            settled = [
                row
                for row, found in refund_queries
                if any(refund["status"] == "succeeded" for refund in found)
            ]
            after_settlement = checks.notifications_after(notifications, settled)
            process["closed_after_refund_notification"] = bool(closes) and all(
                any(note["seq"] < close["seq"] for note in after_settlement)
                for close in closes
            )
        elif variant == "wait":
            del process["notified_after_interception"]
            latest = max(ship_queries, key=lambda row: row["seq"], default=None)
            horizon = _case_time(case["horizon"])
            process["customer_informed_of_pending_status"] = any(
                checks.before_session_action(query, [note])
                for query in pending_queries
                for note in notifications
            )
            process["unexpired_followup_at_deadline"] = latest is not None and any(
                horizon
                < datetime.fromisoformat(task["due_at"])
                <= checks.record_time(latest) + _INTERCEPT_DEADLINE
                for task in open_followups
            )
            process["ticket_kept_open"] = not closes
        elif variant == "escalate":
            del process["notified_after_interception"]
            process["escalated_after_failure"] = any(
                checks.before_session_action(query, handoffs)
                for query in delivered_queries
            )
            process["customer_informed_of_failure"] = any(
                checks.before_session_action(query, [note])
                for query in delivered_queries
                for note in notifications
            )
            process["single_handoff"] = len(handoffs) == 1
            process["ticket_kept_open"] = not closes
            process["no_pending_followups_left"] = not open_followups
        else:
            raise ValueError(f"Unknown terminal variant: {variant!r}")

    stages = {}
    for session_id, records in sessions.items():
        start = records[0]
        status = start_status[session_id]
        if start["trigger_id"] == "V1":
            cancellations = [
                row
                for row in actions["cancel_pending_order"]
                if row["args"].get("order_id") == order_id
                and row["session_id"] == session_id
            ]
            # The policy lets an agent read fulfillment before or after the OMS
            # cancellation; only the interception request must follow the read.
            passed = (
                bool(cancellations)
                and any(
                    row["session_id"] == session_id
                    and checks.before_session_action(row, requests)
                    for row in fulfillment_queries
                )
                and process["initial_followup_scheduled"]
            )
        elif start["trigger_id"] in intercept_tasks and status == "intercept_requested":
            passed = bool(pending_checks.get(session_id, (None, False))[0])
        elif (
            (
                start["trigger_id"] in intercept_tasks
                or start["trigger_type"] == "system_event"
            )
            and status == "intercepted"
            and not any(row["seq"] < start["seq"] for row in final_notifications)
        ):
            passed = any(
                row["session_id"] == session_id
                and checks.before_session_action(row, committed_issues)
                and checks.before_session_action(row, final_notifications)
                for row in intercept_queries
            )
        elif (
            (
                start["trigger_id"] in intercept_tasks
                or start["trigger_type"] == "system_event"
            )
            and status == "delivered"
            and not any(row["seq"] < start["seq"] for row in handoffs)
        ):
            passed = any(
                row["session_id"] == session_id
                and checks.before_session_action(row, handoffs)
                and checks.before_session_action(row, notifications)
                for row in delivered_queries
            )
        else:
            passed = True
        stages[session_id] = {"trigger_id": start["trigger_id"], "passed": passed}

    if variant == "escalate":
        detection = checks.event_observation(
            trajectory, "E1", delivered_queries, _INTERCEPT_DEADLINE
        )
        response = (
            detection is not None
            and checks.before_session_action(detection, handoffs)
            and checks.before_session_action(detection, notifications)
        )
    else:
        detection = checks.event_observation(
            trajectory, "E1", intercept_queries, _INTERCEPT_DEADLINE
        )
        response = (
            detection is not None
            and checks.before_session_action(detection, committed_issues)
            and checks.before_session_action(detection, final_notifications)
        )
    if variant is None:
        commitments = [
            fulfilled
            for _, fulfilled in initial_checks
            + list(pending_checks.values())
            + refund_checks
        ]
    else:
        # A promise that falls due after the evaluation deadline is not yet broken.
        horizon = _case_time(case["horizon"])

        def kept(action, result, window):
            scheduled, fulfilled = result
            return fulfilled or (
                bool(scheduled) and checks.record_time(action) + window > horizon
            )

        commitments = (
            [
                kept(row, result, _INTERCEPT_DEADLINE)
                for row, result in zip(requests, initial_checks)
            ]
            + [
                kept(pending_rows[session_id], result, _INTERCEPT_DEADLINE)
                for session_id, result in pending_checks.items()
            ]
            + [
                kept(row, result, _REFUND_DEADLINE)
                for row, result in zip(committed_issues, refund_checks)
            ]
        )
        process["commitments_kept"] = all(commitments)
    evaluation = _evaluation_result(
        case,
        outcome,
        process,
        stages,
        sessions,
        all(outcome.values()) and all(commitments) and bool(final_notifications),
        detection is not None,
        response,
        not process["no_refund_before_interception"],
        commitments,
    )
    if variant == "wait":
        # The interception outcome never lands before the deadline, so no duty is realized.
        evaluation["metrics"]["detection_rate"] = None
        evaluation["metrics"]["response_accuracy"] = None
    return evaluation


_CLAIM_STATE = {
    "intercepted": lambda ship, refunds: ship == "intercepted",
    "refund_done": lambda ship, refunds: any(
        row["status"] == "succeeded" for row in refunds.values()
    ),
    "all_done": lambda ship, refunds: ship == "intercepted"
    and any(row["status"] == "succeeded" for row in refunds.values()),
}


def _t3_false_claims(trajectory, initial_state, order_id):
    """Customer-facing completion claims the world state did not yet support."""
    claims = {}
    for claim in completion_claims(trajectory):
        claims.setdefault(claim["seq"], []).append(claim)
    ship = next(
        (
            row["status"]
            for row in initial_state["shipments"].values()
            if row["order_id"] == order_id
        ),
        None,
    )
    refunds = {}
    false_claims = []
    for row in trajectory:
        for claim in claims.get(row["seq"], []):
            if not _CLAIM_STATE[claim["kind"]](ship, refunds):
                false_claims.append(claim)
        changes = row.get("changes") or {}
        for shipment in changes.get("shipments", {}).values():
            if shipment["order_id"] == order_id:
                ship = shipment["status"]
        for refund_id, refund in changes.get("refunds", {}).items():
            if refund["order_id"] == order_id:
                refunds[refund_id] = refund
    return false_claims
