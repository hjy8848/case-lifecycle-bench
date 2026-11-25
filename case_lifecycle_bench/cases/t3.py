"""The v0 partially cancelled order case and its deterministic agents."""

from ._tools import (
    cancel_pending_followups,
    get_trigger_ticket,
    load_fixture,
    schedule_followup,
)

_INTERCEPT_NOTE = "Verify whether the carrier completed the parcel interception."


def t3_case():
    return load_fixture("t3")


def oracle(session, call):
    """Keep monitoring the shipment until the carrier confirms interception."""
    return _run(session, call)


def missed_recheck(session, call):
    """Give up after the first follow-up finds the intercept still pending."""
    return _run(session, call, reschedule=False)


def premature_refund(session, call):
    """Refund a shipped order before the carrier confirms interception."""
    return _run(session, call, refund_early=True)


def do_nothing(session, call):
    """Never act: wake, read nothing, write nothing and leave."""
    return "LEFT" if session["trigger_type"] == "user_visit" else None


def close_early(session, call):
    """Request the interception, then close the ticket and call the matter done."""
    if session["trigger_type"] != "user_visit":
        return None
    if session["trigger_id"] != "V1":
        return "LEFT"
    user_id = session["user_id"]
    order_id = session["order_id"]
    ticket_id = call(
        "create_ticket", user_id=user_id, order_ids=[order_id], summary="Cancel the order and refund"
    )
    call("cancel_pending_order", order_id=order_id)
    call("get_fulfillment_status", order_id=order_id)
    call("cancel_fulfillment", order_id=order_id)
    call(
        "send_message_to_user",
        user_id=user_id,
        channel="chat",
        text="Your order has been fully cancelled and the refund is done.",
    )
    call("close_ticket", ticket_id=ticket_id, resolution_code="resolved")
    return "CONFIRMED"


def _revisit(session, call):
    """The customer is back asking for progress: read the ticket and the carrier, say so."""
    user_id = session["user_id"]
    order_id = session["order_id"]
    ticket = call("search_tickets", user_id=user_id, order_id=order_id)[0]
    shipment = call("get_shipment", order_id=order_id)
    if shipment["status"] == "delivered":
        text = "The parcel was delivered and the interception did not succeed; we will contact you through the returns process."
    elif shipment["status"] == "intercepted":
        text = "The carrier has confirmed the interception and the refund is processing; we will keep following up until it arrives."
    else:
        text = "The carrier is still processing the interception request; we cannot refund until there is a result, and we will keep following up."
    call("say", text=text)
    call("add_ticket_note", ticket_id=ticket["ticket_id"], text="Customer came back asking about progress; told truthfully.")
    return "CONFIRMED"


def _run(session, call, *, reschedule=True, refund_early=False):
    if session["trigger_type"] == "user_visit" and session["trigger_id"] != "V1":
        return _revisit(session, call)
    if session["trigger_type"] == "user_visit":
        user_id = session["user_id"]
        order_id = session["order_id"]
        call("search_tickets", user_id=user_id, order_id=order_id)
        ticket_id = call(
            "create_ticket",
            user_id=user_id,
            order_ids=[order_id],
            summary="Cancel a shipped order and refund",
        )
        call("cancel_pending_order", order_id=order_id)
        call("get_fulfillment_status", order_id=order_id)
        call("cancel_fulfillment", order_id=order_id)
        schedule_followup(session, call, ticket_id, 24, _INTERCEPT_NOTE)
        if refund_early:
            _issue_refund(session, call, ticket_id, order_id)
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="The order is cancelled in OMS and the carrier is processing the interception request.",
        )
        call("say", text="The parcel has shipped and I have requested interception; I can refund only after interception is confirmed.")
        return "CONFIRMED"

    ticket = get_trigger_ticket(session, call)
    ticket_id = ticket["ticket_id"]
    order_id = ticket["order_ids"][0]
    if session.get("note", "").startswith("Verify refund ") or session["trigger_id"] == "E2":
        _check_refund(call, ticket)
        return None

    shipment = call("get_shipment", order_id=order_id)
    if shipment["status"] == "intercept_requested":
        if reschedule:
            schedule_followup(session, call, ticket_id, 24, _INTERCEPT_NOTE)
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="First check; the carrier has not yet confirmed the interception.",
        )
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="The carrier is still processing the interception request; we cannot refund until there is a result, and we will keep following up.",
        )
    elif shipment["status"] == "delivered":
        cancel_pending_followups(call, ticket_id, _INTERCEPT_NOTE)
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="The parcel was delivered and the interception did not succeed. We have passed this to the returns team, who will contact you through the returns process.",
        )
        call(
            "escalate_to_human",
            ticket_id=ticket_id,
            note="Interception failed and the parcel was delivered; switching to the returns process.",
        )
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="Interception failed; customer told and handed to the returns team; no refund.",
        )
    elif shipment["status"] == "intercepted":
        cancel_pending_followups(call, ticket_id, _INTERCEPT_NOTE)
        if not call("list_refunds", order_id=order_id):
            _issue_refund(session, call, ticket_id, order_id)
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="The carrier has confirmed the interception, the refund is submitted, and the payment gateway is processing it.",
        )
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="The carrier confirmed the interception; customer told; refund to be verified.",
        )
    return None


def _issue_refund(session, call, ticket_id, order_id):
    order = call("get_order_details", order_id=order_id)
    refund_id = call("issue_refund", order_id=order_id, amount=order["amount"])
    schedule_followup(
        session, call, ticket_id, 48, f"Verify refund {refund_id} at the payment gateway."
    )


def _check_refund(call, ticket):
    if ticket["status"] == "closed":
        return
    ticket_id = ticket["ticket_id"]
    order_id = ticket["order_ids"][0]
    refund_id = call("list_refunds", order_id=order_id)[0]["refund_id"]
    refund = call("get_refund", refund_id=refund_id)
    if refund["status"] == "succeeded":
        for task in call("list_followups", ticket_id=ticket_id):
            if task["status"] == "pending":
                call("cancel_followup", task_id=task["task_id"])
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="Refund arrival verified.",
        )
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"Refund {refund_id} arrived; customer told.",
        )
        call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
    else:
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"Refund {refund_id} is still processing; ticket kept open.",
        )
