"""Deterministic scoring for the v0 smoke-test cases."""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

_REFUND_DEADLINE = timedelta(hours=48)
_ACTIVE_REFUND_STATES = {"processing", "succeeded"}


def _time(record):
    return datetime.fromisoformat(record["t"])


def _rate(results):
    return sum(results) / len(results) if results else None


def _refund_query(record, order_id):
    result = record.get("result")
    if record.get("tool") == "get_refund" and isinstance(result, dict):
        result = [result]
    elif record.get("tool") != "list_refunds" or not isinstance(result, list):
        return []
    return [refund for refund in result if refund.get("order_id") == order_id]


def _conversation_success(sessions):
    conversations = [
        records
        for records in sessions.values()
        if records[0]["trigger_type"] == "user_visit"
    ]
    seen_channels = set()
    successes = []
    for records in reversed(conversations):
        channel = records[0]["channel"]
        success = records[-1].get("user_signal") in {"CONFIRMED", "LEFT"}
        successes.append(success and channel not in seen_channels)
        seen_channels.add(channel)
    return _rate(successes)


def _monitoring_check(action, schedules, queries, ticket_ids):
    deadline = _time(action) + _REFUND_DEADLINE
    refund_ids = action["changes"]["refunds"].keys()
    eligible = [
        task
        for record, task in schedules
        if record["session_id"] == action["session_id"]
        and record["seq"] > action["seq"]
        and task["ticket_id"] in ticket_ids
        and _time(action) < datetime.fromisoformat(task["due_at"]) <= deadline
    ]
    checked = any(
        record["session_id"] != action["session_id"]
        and record["seq"] > action["seq"]
        and _time(record) <= deadline
        and any(refund["refund_id"] in refund_ids for refund in refunds)
        for record, refunds in queries
    )
    return bool(eligible), bool(eligible) and checked


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
        and start <= _time(record) <= deadline
    ]
    responded = bool(eligible_retries) or any(
        record["seq"] > event["seq"] and start <= _time(record) <= deadline
        for record in notifications
    )
    detection = next(
        (
            record
            for record, refunds in queries
            if record["seq"] > event["seq"]
            and start <= _time(record) <= deadline
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
    tickets = {
        ticket_id: row
        for ticket_id, row in state["tickets"].items()
        if row["user_id"] == user_id and order_id in row["order_ids"]
    }
    succeeded = [
        row
        for row in state["refunds"].values()
        if row["order_id"] == order_id and row["status"] == "succeeded"
    ]
    closed_ticket_ids = {
        ticket_id for ticket_id, row in tickets.items() if row["status"] == "closed"
    }
    outcome = {
        "single_successful_refund": len(succeeded) == 1,
        "correct_refund_amount": len(succeeded) == 1
        and Decimal(str(succeeded[0]["amount"]))
        == Decimal(str(case["init"]["amount"])),
        "ticket_closed": bool(closed_ticket_ids),
    }

    refunds = dict(run["initial_state"]["refunds"])
    sessions = defaultdict(list)
    start_statuses = {}
    calls = defaultdict(list)
    actions = defaultdict(list)
    queries = []
    schedules = []
    failure_events = []
    success_seq = None
    final_notifications = []
    verified_success_seq = None
    no_duplicate = True

    for record in run["trajectory"]:
        kind = record["kind"]
        session_id = record["session_id"]
        changes = record.get("changes", {})
        statuses = {
            row["status"] for row in refunds.values() if row["order_id"] == order_id
        }
        if session_id is not None:
            sessions[session_id].append(record)
        if kind == "session_start":
            start_statuses[session_id] = statuses
        if kind == "world_event" and record.get("duty"):
            failure_events.append(record)
        if kind == "tool_call":
            tool = record["tool"]
            args = record["args"]
            calls[tool].append(record)
            if tool == "issue_refund" and args.get("order_id") == order_id:
                no_duplicate = no_duplicate and not (statuses & _ACTIVE_REFUND_STATES)
            queried = _refund_query(record, order_id)
            if queried:
                queries.append((record, queried))
                if "succeeded" in statuses and any(
                    row["status"] == "succeeded" for row in queried
                ):
                    verified_success_seq = record["seq"]
            if record["committed"]:
                actions[tool].append(record)
                if tool == "schedule_followup":
                    schedules.extend(
                        (record, task) for task in changes["followups"].values()
                    )
                if (
                    tool == "send_message_to_user"
                    and args.get("user_id") == user_id
                    and verified_success_seq is not None
                ):
                    final_notifications.append(record)

        refunds.update(changes.get("refunds", {}))
        if success_seq is None and any(
            row["order_id"] == order_id and row["status"] == "succeeded"
            for row in changes.get("refunds", {}).values()
        ):
            success_seq = record["seq"]

    notifications = [
        record
        for record in actions["send_message_to_user"]
        if record["args"]["user_id"] == user_id
    ]
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
        record["seq"]: _monitoring_check(record, schedules, queries, tickets)
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
        "conversation_success": _conversation_success(sessions),
        "session_correctness": _rate([stage["passed"] for stage in stages.values()]),
        "detection_rate": _rate([row[1] for row in failure_checks]),
        "response_accuracy": _rate([row[2] for row in failure_checks if row[1]]),
        "unsafe_action_rate": int(not no_duplicate),
        "commitment_fulfillment": _rate(commitments),
    }
    return {
        "outcome": outcome,
        "process": process,
        "stages": stages,
        "metrics": metrics,
        "pending_judges": list(case["checks"].get("judge", [])),
    }


def evaluate_case(case: dict, run: dict) -> dict:
    """Evaluate a supported v0 smoke-test template."""
    evaluators = {
        "refund_silent_failure": evaluate_t1,
        "refund_timeout_committed": evaluate_t2,
        "cancel_partial_success": evaluate_t3,
    }
    template = case["template"]
    if template not in evaluators:
        raise ValueError(f"Unsupported evaluation template: {template}")
    return evaluators[template](case, run)


def _trace_records(trajectory):
    sessions = defaultdict(list)
    calls = defaultdict(list)
    actions = defaultdict(list)
    for record in trajectory:
        if record["session_id"] is not None:
            sessions[record["session_id"]].append(record)
        if record["kind"] == "tool_call":
            calls[record["tool"]].append(record)
            if record["committed"]:
                actions[record["tool"]].append(record)
    return sessions, calls, actions


def _case_tickets(state, user_id, order_id):
    return {
        ticket_id
        for ticket_id, row in state["tickets"].items()
        if row["user_id"] == user_id and order_id in row["order_ids"]
    }


def _schedules(actions):
    return [
        (record, task)
        for record in actions["schedule_followup"]
        for task in record["changes"]["followups"].values()
    ]


def _same_session(first, second):
    return first["session_id"] == second["session_id"]


def _event_observation(trajectory, event_id, queries, deadline_delta):
    event = next((row for row in trajectory if row.get("event_id") == event_id), None)
    if event is None or event.get("t_observable") is None:
        return None
    start = datetime.fromisoformat(event["t_observable"])
    return next(
        (
            row
            for row in queries
            if row["seq"] > event["seq"]
            and start <= _time(row) <= start + deadline_delta
        ),
        None,
    )


def _before_session_action(first, actions):
    return any(
        _same_session(first, row) and first["seq"] < row["seq"] for row in actions
    )


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
            "conversation_success": _conversation_success(sessions),
            "session_correctness": _rate(
                [stage["passed"] for stage in stages.values()]
            ),
            "detection_rate": int(detected),
            "response_accuracy": int(response) if detected else None,
            "unsafe_action_rate": int(unsafe),
            "commitment_fulfillment": _rate(commitments),
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
    sessions, calls, actions = _trace_records(trajectory)
    tickets = _case_tickets(state, user_id, order_id)
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
        (row, _refund_query(row, order_id))
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
            and _same_session(query, repeated)
            for query in relevant_queries
        )
        for previous, repeated in zip(issues, issues[1:])
    ]
    timeout_verified = False
    if timeout is not None:
        next_issue = next((row for row in issues if row["seq"] > timeout["seq"]), None)
        timeout_verified = any(
            _same_session(timeout, row)
            and timeout["seq"] < row["seq"]
            and (next_issue is None or row["seq"] < next_issue["seq"])
            for row in relevant_queries
        )
    succeeded_queries = [
        row
        for row, found in queries
        if any(refund["status"] == "succeeded" for refund in found)
    ]
    notifications = [
        row
        for row in actions["send_message_to_user"]
        if row["args"]["user_id"] == user_id
    ]
    final_notifications = [
        note
        for note in notifications
        if any(row["seq"] < note["seq"] for row in succeeded_queries)
    ]
    closes = [
        row
        for row in actions["close_ticket"]
        if row["args"]["ticket_id"] in closed_tickets
    ]
    monitoring = [
        _monitoring_check(row, _schedules(actions), queries, tickets)
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
                and _before_session_action(note, closes)
                for note in final_notifications
            )
        else:
            passed = True
        stages[session_id] = {"trigger_id": start["trigger_id"], "passed": passed}

    detection = _event_observation(
        trajectory, "E1", succeeded_queries, _REFUND_DEADLINE
    )
    response = detection is not None and any(
        note["session_id"] == detection["session_id"]
        and detection["seq"] < note["seq"]
        and _before_session_action(note, closes)
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


_INTERCEPT_DEADLINE = timedelta(hours=24)


def _scheduled_check(action, schedules, tickets, queries):
    deadline = _time(action) + _INTERCEPT_DEADLINE
    task_ids = {
        task["task_id"]
        for row, task in schedules
        if _same_session(action, row)
        and action["seq"] < row["seq"]
        and task["ticket_id"] in tickets
        and _time(action) < datetime.fromisoformat(task["due_at"]) <= deadline
    }
    checked = any(
        not _same_session(action, row)
        and action["seq"] < row["seq"]
        and _time(row) <= deadline
        for row in queries
    )
    return task_ids, bool(task_ids) and checked


def evaluate_t3(case: dict, run: dict) -> dict:
    """Verify warehouse cancellation and carrier interception before refunding."""
    order_id = case["init"]["order"]
    user_id = case["init"]["user"]
    trajectory = run["trajectory"]
    state = run["final_state"]
    sessions, calls, actions = _trace_records(trajectory)
    tickets = _case_tickets(state, user_id, order_id)
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
    notifications = [
        row
        for row in actions["send_message_to_user"]
        if row["args"]["user_id"] == user_id
    ]
    outbound = [
        row for row in trajectory if row["kind"] == "message" and row["role"] == "agent"
    ] + notifications
    final_notifications = [
        note
        for note in notifications
        if any(row["seq"] < note["seq"] for row in intercept_queries)
    ]
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

    schedules = _schedules(actions)
    initial_checks = [
        _scheduled_check(row, schedules, tickets, ship_queries) for row in requests
    ]
    pending_checks = {}
    for row in ship_queries:
        if row["result"]["status"] == "intercept_requested":
            pending_checks[row["session_id"]] = _scheduled_check(
                row, schedules, tickets, ship_queries
            )
    intercept_tasks = {
        task_id
        for task_ids, _ in initial_checks + list(pending_checks.values())
        for task_id in task_ids
    }
    refund_queries = [
        (row, _refund_query(row, order_id))
        for row in calls["get_refund"] + calls["list_refunds"]
    ]
    refund_checks = [
        _monitoring_check(row, schedules, refund_queries, tickets)
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
            passed = (
                bool(cancellations)
                and any(
                    _before_session_action(cancellations[0], [row])
                    and _before_session_action(row, requests)
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
                and _before_session_action(row, committed_issues)
                and _before_session_action(row, final_notifications)
                for row in intercept_queries
            )
        else:
            passed = True
        stages[session_id] = {"trigger_id": start["trigger_id"], "passed": passed}

    detection = _event_observation(
        trajectory, "E1", intercept_queries, _INTERCEPT_DEADLINE
    )
    response = (
        detection is not None
        and _before_session_action(detection, committed_issues)
        and _before_session_action(detection, final_notifications)
    )
    commitments = [
        fulfilled
        for _, fulfilled in initial_checks
        + list(pending_checks.values())
        + refund_checks
    ]
    return _evaluation_result(
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
