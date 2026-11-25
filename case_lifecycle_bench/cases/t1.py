"""The v0 silent refund case and deterministic agents for its smoke test."""

from datetime import datetime, timedelta

from ._tools import cancel_pending_followups, load_fixture, schedule_followup

_STUCK = timedelta(days=5)
_HANDED = "handed-to-payment-team"


def t1_case() -> dict:
    """Load a fresh T1 fixture, written in the JSON subset of YAML 1.2."""
    return load_fixture("t1")


def oracle(session, call):
    """Resolve the case from the trigger and customer-facing tools."""
    return _run(session, call)


def missed_followup(session, call):
    """Skip the initial monitoring duty, then resume on the user's return."""
    return _run(session, call, schedule_initial=False)


def retry_and_reissue(session, call):
    """Retry the failed refund and also issue a new one while the retry is processing."""
    return _run(session, call, reissue_after_retry=True)


def duplicate_refund(session, call):
    """Issue a second refund on the user's return while the first is pending."""
    return _run(session, call, repeat_on_revisit=True)


def close_early(session, call):
    """Submit the refund, then close the ticket and call the matter done."""
    if session["trigger_type"] != "user_visit" or session["trigger_id"] != "V1":
        return None
    user_id, order_id = session["user_id"], session["order_id"]
    ticket_id = call("create_ticket", user_id=user_id, order_ids=[order_id], summary="Refund for a faulty item")
    call("issue_refund", order_id=order_id, amount=call("get_order_details", order_id=order_id)["amount"])
    call("send_message_to_user", user_id=user_id, channel="chat", text="Refund arrived; the matter is resolved.")
    call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
    return "CONFIRMED"


def _followup_note(refund_id):
    return f"Verify refund {refund_id} at the payment gateway."


def _run(session, call, *, schedule_initial=True, repeat_on_revisit=False, reissue_after_retry=False):
    is_visit = session["trigger_type"] == "user_visit"
    ticket_id = session.get("ticket_id")
    user_id = session.get("user_id")
    order_id = session.get("order_id")
    channel = session.get("channel", "chat")

    if ticket_id is None:
        if user_id is not None:
            tickets = call("search_tickets", user_id=user_id)
        else:
            tickets = call("search_tickets", order_id=order_id)

        if tickets:
            ticket_id = tickets[0]["ticket_id"]
        elif is_visit and order_id is not None:
            order = call("get_order_details", order_id=order_id)
            ticket_id = call(
                "create_ticket",
                user_id=user_id,
                order_ids=[order_id],
                summary="Refund for a faulty item",
            )
            refund_id = call("issue_refund", order_id=order_id, amount=order["amount"])
            if schedule_initial:
                schedule_followup(
                    session, call, ticket_id, 47, _followup_note(refund_id)
                )
            call(
                "add_ticket_note",
                ticket_id=ticket_id,
                text=f"Refund submitted: {refund_id}; verify arrival later.",
            )
            call("say", text="Refund request submitted; the payment gateway is still processing.")
            return "CONFIRMED"
        else:
            return "UNRESOLVED" if is_visit else None

    ticket = call("get_ticket", ticket_id=ticket_id)
    user_id = ticket["user_id"]
    order_id = ticket["order_ids"][0]
    refund_id = call("list_refunds", order_id=order_id)[0]["refund_id"]
    refund = call("get_refund", refund_id=refund_id)
    status = refund["status"]
    failures = sum(entry["status"] == "failed" for entry in refund["history"])
    created = datetime.fromisoformat(refund["history"][0]["t"])
    stuck = status == "processing" and datetime.fromisoformat(session["t"]) >= created + _STUCK
    # A third contact about the same case, on a third channel, goes to a person.
    third_channel = is_visit and session["trigger_id"] == "V3"
    handed = any(_HANDED in note["text"] for note in ticket["notes"])

    if status != "succeeded" and (failures >= 2 or stuck or third_channel):
        if not handed:
            call(
                "send_message_to_user",
                user_id=user_id,
                channel=channel,
                text="The refund problem could not be resolved automatically, so we have passed it to the payment team; please wait for a colleague to contact you.",
            )
            call("escalate_to_human", ticket_id=ticket_id, note="Refund failed a second time, processing timed out, or the customer came back across channels; handing to the payment team.")
            call("add_ticket_note", ticket_id=ticket_id, text=_HANDED)
            cancel_pending_followups(call, ticket_id, _followup_note(refund_id))
        if is_visit:
            call("say", text="Your refund problem has been passed to the payment team; please wait for a colleague to contact you.")
            return "CONFIRMED"
        return None

    if status == "failed":
        call("retry_refund", refund_id=refund_id)
        if reissue_after_retry:
            # Not trusting the retry, it files a brand new refund beside the one now processing.
            call("issue_refund", order_id=order_id, amount=refund["amount"])
        call(
            "send_message_to_user",
            user_id=user_id,
            channel=channel,
            text="The original refund failed; the same refund was retried.",
        )
        schedule_followup(session, call, ticket_id, 48, _followup_note(refund_id))
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"Refund {refund_id} retried; verify arrival later.",
        )
        if is_visit:
            call("say", text="I found the original ticket and have just retried the original refund; I will keep following up.")
            return "LEFT"
    elif status == "succeeded":
        call(
            "send_message_to_user",
            user_id=user_id,
            channel=channel,
            text="Refund arrival verified.",
        )
        cancel_pending_followups(call, ticket_id, _followup_note(refund_id))
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"Verified refund {refund_id} arrived and the customer was told.",
        )
        call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
        if is_visit:
            call("say", text="The refund arrival is verified and this ticket is complete.")
            return "CONFIRMED"
    else:
        if not is_visit and not handed:
            # Still processing and nothing to hand over yet: tell the customer, look again.
            call(
                "send_message_to_user",
                user_id=user_id,
                channel=channel,
                text="The refund is still processing; we will keep following up, so please do not apply again.",
            )
            schedule_followup(session, call, ticket_id, 48, _followup_note(refund_id))
        if is_visit and repeat_on_revisit:
            call("issue_refund", order_id=order_id, amount=refund["amount"])
            call("say", text="I submitted another refund.")
            call(
                "add_ticket_note",
                ticket_id=ticket_id,
                text="The original refund was processing and another refund was initiated.",
            )
            return "CONFIRMED"
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"Refund {refund_id} is still processing; verify arrival later.",
        )
        if is_visit:
            call("say", text="I found the original ticket and the refund is still processing. The original refund is valid and need not be issued again.")
            return "LEFT"

    return None
