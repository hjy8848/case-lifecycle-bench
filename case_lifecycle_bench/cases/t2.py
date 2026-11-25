"""The v0 committed-timeout case and its deterministic agents."""

from datetime import datetime, timedelta

from ._tools import (
    cancel_pending_followups,
    get_trigger_ticket,
    load_fixture,
    schedule_followup,
)


_STUCK = timedelta(days=5)
_HANDED = "已转支付组"


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
        text = "已核实退款到账。" if refund["status"] == "succeeded" else "退款还在支付网关处理中，请不要重复申请，我们会继续跟进。"
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
            summary="商品与描述不符退款",
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
            text=f"退款调用超时，已确认支付网关存在退款 {refund_id}，等待到账。",
        )
        call("say", text="已查到您的退款申请，支付网关正在处理。")
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
                    text="退款处理时间过长，已转交支付组，请等待同事联系您。",
                )
                call("escalate_to_human", ticket_id=ticket_id, note="退款处理超过 5 天，转支付组。")
                call("add_ticket_note", ticket_id=ticket_id, text=_HANDED)
                cancel_pending_followups(call, ticket_id, _note(refund_id))
            return None
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="退款仍在处理中，我们会继续跟进，请不要重复申请。",
        )
        schedule_followup(session, call, ticket_id, 48, _note(refund_id))
        return None
    if refund["status"] == "succeeded":
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="已核实退款到账。",
        )
        cancel_pending_followups(call, ticket_id, _note(refund_id))
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"已核实退款 {refund_id} 到账并通知客户。",
        )
        call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
    return None


def _note(refund_id):
    return f"核实退款 {refund_id} 的支付网关状态。"
