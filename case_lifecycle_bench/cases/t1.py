"""The v0 silent refund case and deterministic agents for its smoke test."""

from datetime import datetime, timedelta

from ._tools import cancel_pending_followups, load_fixture, schedule_followup

_STUCK = timedelta(days=5)
_HANDED = "已转支付组"


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
    ticket_id = call("create_ticket", user_id=user_id, order_ids=[order_id], summary="商品故障退款")
    call("issue_refund", order_id=order_id, amount=call("get_order_details", order_id=order_id)["amount"])
    call("send_message_to_user", user_id=user_id, channel="chat", text="退款已到账，问题已解决。")
    call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
    return "CONFIRMED"


def _followup_note(refund_id):
    return f"核实退款 {refund_id} 的支付网关状态。"


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
                summary="商品故障退款",
            )
            refund_id = call("issue_refund", order_id=order_id, amount=order["amount"])
            if schedule_initial:
                schedule_followup(
                    session, call, ticket_id, 47, _followup_note(refund_id)
                )
            call(
                "add_ticket_note",
                ticket_id=ticket_id,
                text=f"已提交退款 {refund_id}，待核实到账。",
            )
            call("say", text="已提交退款申请，支付网关还在处理。")
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
                text="退款问题未能自动解决，已转交支付组处理，请等待同事联系您。",
            )
            call("escalate_to_human", ticket_id=ticket_id, note="退款第二次失败、处理超时或客户多次换渠道，转支付组。")
            call("add_ticket_note", ticket_id=ticket_id, text=_HANDED)
            cancel_pending_followups(call, ticket_id, _followup_note(refund_id))
        if is_visit:
            call("say", text="您的退款问题已转交支付组，请等待同事联系。")
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
            text="原退款处理失败，已重试同一笔退款。",
        )
        schedule_followup(session, call, ticket_id, 48, _followup_note(refund_id))
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"退款 {refund_id} 已重试，待核实到账。",
        )
        if is_visit:
            call("say", text="已查到原工单，刚刚重试了原退款，我会继续跟进。")
            return "LEFT"
    elif status == "succeeded":
        call(
            "send_message_to_user",
            user_id=user_id,
            channel=channel,
            text="已核实退款到账。",
        )
        cancel_pending_followups(call, ticket_id, _followup_note(refund_id))
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"已核实退款 {refund_id} 到账并通知客户。",
        )
        call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
        if is_visit:
            call("say", text="已核实退款到账，这个工单已处理完毕。")
            return "CONFIRMED"
    else:
        if not is_visit and not handed:
            # Still processing and nothing to hand over yet: tell the customer, look again.
            call(
                "send_message_to_user",
                user_id=user_id,
                channel=channel,
                text="退款仍在处理中，我们会继续跟进，请不要重复申请。",
            )
            schedule_followup(session, call, ticket_id, 48, _followup_note(refund_id))
        if is_visit and repeat_on_revisit:
            call("issue_refund", order_id=order_id, amount=refund["amount"])
            call("say", text="我又提交了一笔退款。")
            call(
                "add_ticket_note",
                ticket_id=ticket_id,
                text="原退款处理中，又发起了一笔退款。",
            )
            return "CONFIRMED"
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"退款 {refund_id} 仍在处理，待核实到账。",
        )
        if is_visit:
            call("say", text="已找到原工单，退款仍在处理。原退款有效，无需重新发起。")
            return "LEFT"

    return None
