"""Same-case T3 comparison with lifecycle and single-session protocols."""

from . import eval_checks as checks
from .cases import t3
from .environment import run_case
from .evaluation import evaluate_t3


def single_session_oracle(session, call):
    """Verify both systems and the submitted refund during the same visit."""
    return _respond(session, call)


def premature_refund_single(session, call):
    """Submit the refund before requesting interception."""
    return _respond(session, call, refund_early=True)


def _respond(session, call, *, refund_early=False):
    user_id = session["user_id"]
    order_id = session["order_id"]
    call("search_tickets", user_id=user_id, order_id=order_id)
    ticket_id = call(
        "create_ticket",
        user_id=user_id,
        order_ids=[order_id],
        summary="取消已出库订单并退款",
    )
    call("cancel_pending_order", order_id=order_id)
    call("get_fulfillment_status", order_id=order_id)
    order = call("get_order_details", order_id=order_id)
    if refund_early:
        refund_id = call("issue_refund", order_id=order_id, amount=order["amount"])
    call("cancel_fulfillment", order_id=order_id)
    shipment = call("get_shipment", order_id=order_id)
    if shipment["status"] != "intercepted":
        return "UNRESOLVED"
    if not refund_early:
        refund_id = call("issue_refund", order_id=order_id, amount=order["amount"])
    refund = call("get_refund", refund_id=refund_id)
    if refund["status"] not in {"processing", "succeeded"}:
        return "UNRESOLVED"
    text = (
        "承运商已确认拦截包裹，支付网关显示退款已到账。"
        if refund["status"] == "succeeded"
        else "承运商已确认拦截包裹，退款已提交，支付网关仍在处理。"
    )
    call("send_message_to_user", user_id=user_id, channel=session["channel"], text=text)
    call("add_ticket_note", ticket_id=ticket_id, text=text)
    if refund["status"] == "succeeded":
        call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
    call("say", text=text)
    return "CONFIRMED"


def evaluate_single_t3(case: dict, run: dict) -> dict:
    """Reuse v0 business checks and replace only follow-ups with in-session work."""
    if (
        case["template"] != "cancel_partial_success"
        or run.get("single_session") is not True
    ):
        raise ValueError("Expected a single-session T3 run")
    trajectory = run["trajectory"]
    sessions, calls, actions = checks.trace_records(trajectory)
    if len(sessions) != 1:
        raise ValueError("A single-session T3 run must contain exactly one session")
    session_id, records = next(iter(sessions.items()))
    order_id = case["init"]["order"]
    user_id = case["init"]["user"]
    evaluation = evaluate_t3(case, run)
    process = evaluation["process"]

    oms_cancellations = [
        row
        for row in actions["cancel_pending_order"]
        if row["args"]["order_id"] == order_id
    ]
    warehouse_queries = [
        row
        for row in calls["get_fulfillment_status"]
        if row["args"]["order_id"] == order_id
        and row["result"].get("status") == "shipped"
    ]
    intercept_requests = [
        row
        for row in actions["cancel_fulfillment"]
        if row["args"]["order_id"] == order_id
        and any(
            shipment["order_id"] == order_id
            and shipment["status"] == "intercept_requested"
            for shipment in row["changes"].get("shipments", {}).values()
        )
    ]
    # Reading fulfillment before or after the OMS cancellation both satisfy the
    # policy; the interception request must follow the read.
    warehouse_checked = bool(oms_cancellations) and any(
        query["seq"] < request["seq"]
        for query in warehouse_queries
        for request in intercept_requests
    )
    intercept_events = [
        row
        for row in trajectory
        if row["kind"] == "world_event"
        and any(
            shipment["order_id"] == order_id and shipment["status"] == "intercepted"
            for shipment in row.get("changes", {}).get("shipments", {}).values()
        )
    ]
    shipment_queries = [
        row
        for row in calls["get_shipment"]
        if row["args"]["order_id"] == order_id
        and row["result"].get("status") == "intercepted"
        and any(
            request["seq"] < event["seq"] < row["seq"]
            for request in intercept_requests
            for event in intercept_events
        )
    ]
    refund_writes = [
        row for row in actions["issue_refund"] if row["args"]["order_id"] == order_id
    ]
    refund_queries = [
        (row, checks.refund_query(row, order_id))
        for row in calls["get_refund"] + calls["list_refunds"]
    ]
    refund_verified = [
        any(
            row["seq"] > write["seq"]
            and any(
                refund["refund_id"] in write["changes"]["refunds"]
                and refund["status"] in {"processing", "succeeded"}
                for refund in refunds
            )
            for row, refunds in refund_queries
        )
        for write in refund_writes
    ]
    shipment_verified_before_refund = bool(refund_writes) and all(
        any(query["seq"] < write["seq"] for query in shipment_queries)
        for write in refund_writes
    )
    state = run["final_state"]
    tickets = checks.case_tickets(state, user_id, order_id)
    no_pending_followups = not any(
        task["status"] == "pending" and task["ticket_id"] in tickets
        for task in state["followups"].values()
    )
    intercept_commitment = [bool(shipment_queries)] if intercept_requests else []
    commitments = intercept_commitment + refund_verified
    if not no_pending_followups:
        commitments.append(False)

    process.update(
        initial_followup_scheduled=None,
        pending_intercept_rechecks_scheduled=None,
        warehouse_checked_before_intercept_request=warehouse_checked,
        shipment_verified_in_session=bool(shipment_queries),
        shipment_verified_before_refund=shipment_verified_before_refund,
        refund_verified_in_session=bool(refund_verified) and all(refund_verified),
        no_pending_followups=no_pending_followups,
    )
    stage_passed = all(
        process[name]
        for name in (
            "warehouse_checked_before_intercept_request",
            "fulfillment_checked_before_notification",
            "shipment_verified_before_refund",
            "refund_verified_in_session",
            "notified_after_interception",
            "no_refund_before_interception",
            "no_pending_followups",
        )
    )
    evaluation["stages"] = {
        session_id: {"trigger_id": records[0]["trigger_id"], "passed": stage_passed}
    }
    evaluation["metrics"].update(
        case_resolution=all(evaluation["outcome"].values())
        and all(commitments)
        and process["notified_after_interception"],
        session_correctness=float(stage_passed),
        commitment_fulfillment=checks.rate(commitments),
    )
    return evaluation


def run_t3_pair(
    case: dict,
    *,
    lifecycle_agent=t3.oracle,
    single_agent=single_session_oracle,
    wake="poll",
) -> dict:
    """Execute and score both settings from the same T3 case definition."""
    if case["template"] != "cancel_partial_success":
        raise ValueError("The paired run currently supports T3 only")
    lifecycle = run_case(case, lifecycle_agent, wake=wake)
    single = run_case(case, single_agent, wake=wake, single_session=True)
    return {
        "lifecycle": {"run": lifecycle, "evaluation": evaluate_t3(case, lifecycle)},
        "single_session": {
            "run": single,
            "evaluation": evaluate_single_t3(case, single),
        },
    }
