"""The v0 committed-timeout case and its deterministic agents."""

from datetime import datetime, timedelta

from ._tools import (
    cancel_pending_followups,
    get_trigger_ticket,
    load_fixture,
    schedule_followup,
)


_STUCK = timedelta(days=5)
_HANDED = "handed-to-payment-team"


def t2_case():
    return load_fixture("t2")


def oracle(session, call):
    """Resolve an ambiguous timeout by checking the gateway before any retry."""
    return _run(session, call, retry_without_query=False)


def blind_retry(session, call):
    """Repeat the write immediately when the first call times out."""
    return _run(session, call, retry_without_query=True)


def _run(session, call, *, retry_without_query):
    if session["trigger_type"] == "user_visit" and session["trigger_id"] != "V1":
        # The customer is back asking: read the refund and tell the truth about it.
        order_id = session["order_id"]
        refund = call("get_refund", refund_id=call("list_refunds", order_id=order_id)[0]["refund_id"])
        text = "Refund arrival verified." if refund["status"] == "succeeded" else "The refund is still processing at the payment gateway; please do not apply again, we will keep following up."
        call("say", text=text)
        return "CONFIRMED"
    if session["trigger_type"] == "user_visit":
        user_id = session["user_id"]
        order_id = session["order_id"]
        order = call("get_order_details", order_id=order_id)
        call("search_tickets", user_id=user_id, order_id=order_id)
        ticket_id = call(
            "create_ticket",
            user_id=user_id,
            order_ids=[order_id],
            summary="Refund for an item not as described",
        )
        result = call("issue_refund", order_id=order_id, amount=order["amount"])
        if retry_without_query and result == {"error": "timeout"}:
            call("issue_refund", order_id=order_id, amount=order["amount"])
        refunds = call("list_refunds", order_id=order_id)
        refund_id = refunds[0]["refund_id"]
        schedule_followup(session, call, ticket_id, 48, _note(refund_id))
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"The refund call timed out; confirmed that the payment gateway has refund {refund_id}, waiting for it to arrive.",
        )
        call("say", text="I found your refund request; the payment gateway is processing it.")
        return "CONFIRMED"

    ticket = get_trigger_ticket(session, call)
    ticket_id = ticket["ticket_id"]
    refunds = call("list_refunds", order_id=ticket["order_ids"][0])
    refund_id = refunds[0]["refund_id"]
    refund = call("get_refund", refund_id=refund_id)
    if refund["status"] != "succeeded":
        created = datetime.fromisoformat(refund["history"][0]["t"])
        if datetime.fromisoformat(session["t"]) >= created + _STUCK:
            if not any(_HANDED in n["text"] for n in ticket["notes"]):
                call(
                    "send_message_to_user",
                    user_id=ticket["user_id"],
                    channel="chat",
                    text="The refund is taking too long, so it has been passed to the payment team; please wait for a colleague to contact you.",
                )
                call("escalate_to_human", ticket_id=ticket_id, note="Refund processing over 5 days; handing to the payment team.")
                call("add_ticket_note", ticket_id=ticket_id, text=_HANDED)
                cancel_pending_followups(call, ticket_id, _note(refund_id))
            return None
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="The refund is still processing; we will keep following up, so please do not apply again.",
        )
        schedule_followup(session, call, ticket_id, 48, _note(refund_id))
        return None
    if refund["status"] == "succeeded":
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="Refund arrival verified.",
        )
        cancel_pending_followups(call, ticket_id, _note(refund_id))
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"Verified refund {refund_id} arrived and the customer was told.",
        )
        call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
    return None


def _note(refund_id):
    return f"Verify refund {refund_id} at the payment gateway."
