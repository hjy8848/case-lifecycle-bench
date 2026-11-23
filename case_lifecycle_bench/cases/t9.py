"""Cross-channel revisit over the T1 refund lifecycle."""

from . import t1

oracle = t1.oracle


def t9_case() -> dict:
    case = t1.t1_case()
    case["case_id"] = "cross_channel_revisit_001"
    case["template"] = "cross_channel_revisit"
    case["capabilities"] = ["continuity"]
    case["visits"][1].update(
        channel="email",
        goal="查询之前申请的退款进度，不主动提供订单号或工单号。",
        utterance="之前申请的退款还没到账，请帮我查一下进度。",
    )
    checks = ["reused_original_ticket(V2)", "no_duplicate_ticket(V2)"]
    case["checks"]["process"].extend(checks)
    case["checks"]["stages"]["V2"].extend(checks)
    return case


def duplicate_ticket(session, call):
    """Open a second ticket for the same order on the customer's email revisit."""
    if session["trigger_type"] != "user_visit" or session["trigger_id"] != "V2":
        return oracle(session, call)

    user_id = session["user_id"]
    original = call("search_tickets", user_id=user_id)[0]
    ticket_id = call(
        "create_ticket",
        user_id=user_id,
        order_ids=original["order_ids"],
        summary="退款进度咨询",
    )
    call(
        "add_ticket_note", ticket_id=ticket_id, text="客户通过邮件咨询之前申请的退款。"
    )
    call("say", text="我已为您的退款进度咨询另建一个工单。")
    return "LEFT"
