"""The v0 silent refund case and deterministic agents for its smoke test."""

from ._tools import cancel_pending_followups, load_fixture, schedule_followup


def t1_case() -> dict:
    """Load a fresh T1 fixture, written in the JSON subset of YAML 1.2."""
    return load_fixture("t1")


def oracle(session, call):
    """Resolve the case from the trigger and customer-facing tools."""
    return _run(session, call)


def missed_followup(session, call):
    """Skip the initial monitoring duty, then resume on the user's return."""
    return _run(session, call, schedule_initial=False)


def duplicate_refund(session, call):
    """Issue a second refund on the user's return while the first is pending."""
    return _run(session, call, repeat_on_revisit=True)


def _followup_note(refund_id):
    return f"核实退款 {refund_id} 的支付网关状态。"


def _run(session, call, *, schedule_initial=True, repeat_on_revisit=False):
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

    if status == "failed":
        call("retry_refund", refund_id=refund_id)
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
