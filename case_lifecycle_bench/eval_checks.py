"""Trace and timing checks shared by the lifecycle template evaluators."""

from collections import defaultdict
from datetime import datetime


def record_time(record):
    return datetime.fromisoformat(record["t"])


def rate(results):
    return sum(results) / len(results) if results else None


def trace_records(trajectory):
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


def conversation_success(sessions):
    conversations = [
        records
        for records in sessions.values()
        if records[0]["trigger_type"] in {"user_visit", "outbound_contact"}
    ]
    seen_channels = set()
    successes = []
    for records in reversed(conversations):
        channel = records[0]["channel"]
        success = records[-1].get("user_signal") in {"CONFIRMED", "LEFT"}
        successes.append(success and channel not in seen_channels)
        seen_channels.add(channel)
    return rate(successes)


def case_tickets(state, user_id, order_id):
    return {
        ticket_id
        for ticket_id, row in state["tickets"].items()
        if row["user_id"] == user_id and order_id in row["order_ids"]
    }


def refund_query(record, order_id):
    result = record.get("result")
    if record.get("tool") == "get_refund" and isinstance(result, dict):
        result = [result]
    elif record.get("tool") != "list_refunds" or not isinstance(result, list):
        return []
    return [refund for refund in result if refund.get("order_id") == order_id]


def notifications_to(actions, user_id):
    return [
        row
        for row in actions["send_message_to_user"]
        if row["args"]["user_id"] == user_id
    ]


def notifications_after(notifications, observations):
    return [
        note
        for note in notifications
        if any(observation["seq"] < note["seq"] for observation in observations)
    ]


def scheduled_followups(actions):
    return [
        (record, task)
        for record in actions["schedule_followup"]
        for task in record["changes"]["followups"].values()
    ]


def same_session(first, second):
    return first["session_id"] == second["session_id"]


def before_session_action(first, actions):
    return any(
        same_session(first, row) and first["seq"] < row["seq"] for row in actions
    )


def followup_check(action, schedules, ticket_ids, queries, window):
    start = record_time(action)
    deadline = start + window
    task_ids = {
        task["task_id"]
        for row, task in schedules
        if same_session(action, row)
        and action["seq"] < row["seq"]
        and task["ticket_id"] in ticket_ids
        and start < datetime.fromisoformat(task["due_at"]) <= deadline
    }
    checked = any(
        not same_session(action, row)
        and action["seq"] < row["seq"]
        and record_time(row) <= deadline
        for row in queries
    )
    return task_ids, bool(task_ids) and checked


def refund_followup_check(action, schedules, queries, ticket_ids, window):
    refund_ids = action["changes"]["refunds"].keys()
    relevant_queries = (
        record
        for record, refunds in queries
        if any(refund["refund_id"] in refund_ids for refund in refunds)
    )
    task_ids, fulfilled = followup_check(
        action, schedules, ticket_ids, relevant_queries, window
    )
    return bool(task_ids), fulfilled


def event_observation(trajectory, event_id, queries, deadline_delta):
    event = next((row for row in trajectory if row.get("event_id") == event_id), None)
    if event is None or event.get("t_observable") is None:
        return None
    start = datetime.fromisoformat(event["t_observable"])
    return next(
        (
            row
            for row in queries
            if row["seq"] > event["seq"]
            and start <= record_time(row) <= start + deadline_delta
        ),
        None,
    )


# An authoritative read must expose the changed record and the observed state.
DUTY_READS = {
    "refunds": {
        "get_refund": ("refund_id", "order_id", "status"),
        "list_refunds": ("refund_id", "order_id", "status"),
    },
    "shipments": {"get_shipment": ("shipment_id", "status")},
    "quotes": {"read_renewal_quote": ("policy_id", "status", "version")},
    "work_items": {
        **{
            name: ("subject_id", "status")
            for name in ("read_application", "read_repair_order")
        },
        "read_installment": ("subject_id", "status", "broken_promises"),
    },
}


def _exposes_event(query, changes, read_specs):
    result = query.get("result")
    rows = result if isinstance(result, list) else [result]
    for table, tools in read_specs.items():
        fields = tools.get(query["tool"])
        if fields and any(
            isinstance(row, dict)
            and all(field in row and row[field] == expected[field] for field in fields)
            for row in rows
            for expected in changes.get(table, {}).values()
        ):
            return True
    return False


def duty_detection_times(trajectory, read_specs=DUTY_READS):
    """Measure from the world change and first actionable opportunity to the same read."""
    queries = [record for record in trajectory if record["kind"] == "tool_call"]
    detections = []
    for event in trajectory:
        if event["kind"] != "world_event" or not event.get("duty"):
            continue
        observed = next(
            (
                query
                for query in queries
                if query["seq"] > event["seq"]
                and _exposes_event(query, event.get("changes", {}), read_specs)
            ),
            None,
        )
        occurred_at = record_time(event)
        detected_at = record_time(observed) if observed is not None else None
        observable = event.get("t_observable")
        opportunity = datetime.fromisoformat(observable) if observable else None
        detections.append(
            {
                "event_id": event["event_id"],
                "event_seq": event["seq"],
                "query_seq": observed["seq"] if observed is not None else None,
                "event_to_detection_hours": (
                    (detected_at - occurred_at).total_seconds() / 3600
                    if detected_at is not None
                    else None
                ),
                "opportunity_to_detection_hours": (
                    (detected_at - opportunity).total_seconds() / 3600
                    if detected_at is not None and opportunity is not None
                    else None
                ),
            }
        )
    return detections


def benchmark_metrics(case, run, evaluation):
    """Measure the same trajectory observables across industries."""
    metrics = evaluation["metrics"]
    duties = duty_detection_times(run["trajectory"])
    conditional = {visit["id"] for visit in case["visits"] if visit.get("condition")}
    observed = {
        "conversation_success": metrics["conversation_success"],
        "case_resolution": metrics["case_resolution"],
        "unsafe_action": bool(metrics["unsafe_action_rate"]),
        "duty_detection_rate": rate([duty["query_seq"] is not None for duty in duties]),
        "duty_events": duties,
        "conditional_revisits": sum(
            row["kind"] == "session_start"
            and row["trigger_type"] == "user_visit"
            and row["trigger_id"] in conditional
            for row in run["trajectory"]
        ),
    }
    if "terminal_variant" in evaluation:
        observed.update(
            terminal_variant=evaluation["terminal_variant"],
            business_outcome=evaluation["business_outcome"],
            wrongful_closure=bool(metrics["wrongful_closure"]),
        )
    return observed
