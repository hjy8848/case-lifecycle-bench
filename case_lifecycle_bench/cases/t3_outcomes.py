"""Business result and end state of a T3 case, for three-outcome scoring."""

from ..outcomes import OutcomeSpec
from . import t3

_ACTIVE_REFUND = {"processing", "succeeded"}


def _tickets(case, run):
    order_id, user_id = case["init"]["order"], case["init"]["user"]
    return {
        ticket_id
        for ticket_id, row in run["final_state"]["tickets"].items()
        if row["user_id"] == user_id and order_id in row["order_ids"]
    }


def _shipment_status(case, run):
    order_id = case["init"]["order"]
    return next(
        (
            row["status"]
            for row in run["final_state"]["shipments"].values()
            if row["order_id"] == order_id
        ),
        None,
    )


def _active_refunds(case, run):
    order_id = case["init"]["order"]
    return [
        row
        for row in run["final_state"]["refunds"].values()
        if row["order_id"] == order_id and row["status"] in _ACTIVE_REFUND
    ]


def business_outcome(case, run):
    """The world at the deadline, whatever the agent did about it."""
    shipment = _shipment_status(case, run)
    if shipment == "intercepted" and any(
        row["status"] == "succeeded" for row in _active_refunds(case, run)
    ):
        return "resolved"
    if shipment == "delivered":
        return "intercept_failed"
    return "in_progress"


def terminal(case, run):
    """The part of the end state that the agent's decisions determine."""
    order_id = case["init"]["order"]
    state = run["final_state"]
    tickets = _tickets(case, run)
    refunds = _active_refunds(case, run)
    amount = state["orders"][order_id]["amount"]
    return {
        "order_cancelled": state["orders"][order_id]["status"] == "cancelled",
        "interception_requested": any(
            row["kind"] == "tool_call"
            and row["tool"] == "cancel_fulfillment"
            and row["committed"]
            and row["args"].get("order_id") == order_id
            and row["result"] == {"result": "intercept_requested"}
            for row in run["trajectory"]
        ),
        "active_refunds": len(refunds),
        "refund_amount_ok": all(abs(row["amount"] - amount) < 0.005 for row in refunds),
        "refund_succeeded": any(row["status"] == "succeeded" for row in refunds),
        "case_tickets": len(tickets),
        "ticket_closed": any(
            state["tickets"][ticket_id]["status"] == "closed" for ticket_id in tickets
        ),
        "followup_pending": any(
            row["ticket_id"] in tickets and row["status"] == "pending"
            for row in state["followups"].values()
        ),
        "handoffs": sum(
            row["ticket_id"] in tickets for row in state["handoffs"].values()
        ),
    }


def ticket_closed(case, run):
    return terminal(case, run)["ticket_closed"]


SPEC = OutcomeSpec(t3.oracle, business_outcome, terminal, ticket_closed)
