"""The v0 partially cancelled order case and its deterministic agents."""

from ._tools import (
    cancel_pending_followups,
    get_trigger_ticket,
    load_fixture,
    schedule_followup,
)

_INTERCEPT_NOTE = "核实承运商是否完成包裹拦截。"


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
    user_id = session["user_id"]
    order_id = session["order_id"]
    ticket_id = call(
        "create_ticket", user_id=user_id, order_ids=[order_id], summary="取消订单并退款"
    )
    call("cancel_pending_order", order_id=order_id)
    call("get_fulfillment_status", order_id=order_id)
    call("cancel_fulfillment", order_id=order_id)
    call(
        "send_message_to_user",
        user_id=user_id,
        channel="chat",
        text="您的订单已全部取消，退款已办好。",
    )
    call("close_ticket", ticket_id=ticket_id, resolution_code="resolved")
    return "CONFIRMED"


def _run(session, call, *, reschedule=True, refund_early=False):
    if session["trigger_type"] == "user_visit":
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
        call("cancel_fulfillment", order_id=order_id)
        schedule_followup(session, call, ticket_id, 24, _INTERCEPT_NOTE)
        if refund_early:
            _issue_refund(session, call, ticket_id, order_id)
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="订单已在 OMS 取消，承运商正在处理拦截请求。",
        )
        call("say", text="包裹已经发货，我已申请拦截，确认拦截后才能退款。")
        return "CONFIRMED"

    ticket = get_trigger_ticket(session, call)
    ticket_id = ticket["ticket_id"]
    order_id = ticket["order_ids"][0]
    if session.get("note", "").startswith("核实退款 ") or session["trigger_id"] == "E2":
        _check_refund(call, ticket)
        return None

    shipment = call("get_shipment", order_id=order_id)
    if shipment["status"] == "intercept_requested":
        if reschedule:
            schedule_followup(session, call, ticket_id, 24, _INTERCEPT_NOTE)
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="第一次核实，承运商暂未确认拦截。",
        )
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="承运商仍在处理拦截请求，结果出来前暂不能退款，我们会继续跟进。",
        )
    elif shipment["status"] == "delivered":
        cancel_pending_followups(call, ticket_id, _INTERCEPT_NOTE)
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="包裹已被签收，拦截没有成功。我们已转交退货组，会按退货流程联系您。",
        )
        call(
            "escalate_to_human",
            ticket_id=ticket_id,
            note="拦截失败，包裹已签收，改走退货流程。",
        )
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="拦截失败，已通知客户并转退货组，不退款。",
        )
    elif shipment["status"] == "intercepted":
        cancel_pending_followups(call, ticket_id, _INTERCEPT_NOTE)
        if not call("list_refunds", order_id=order_id):
            _issue_refund(session, call, ticket_id, order_id)
        call(
            "send_message_to_user",
            user_id=ticket["user_id"],
            channel="chat",
            text="承运商已确认拦截包裹，退款已提交，支付网关正在处理。",
        )
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="承运商已确认拦截，已通知客户，退款待核实。",
        )
    return None


def _issue_refund(session, call, ticket_id, order_id):
    order = call("get_order_details", order_id=order_id)
    refund_id = call("issue_refund", order_id=order_id, amount=order["amount"])
    schedule_followup(
        session, call, ticket_id, 48, f"核实退款 {refund_id} 的支付网关状态。"
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
            text="已核实退款到账。",
        )
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"退款 {refund_id} 已到账，客户已通知。",
        )
        call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
    else:
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text=f"退款 {refund_id} 仍在处理中，工单保留。",
        )
