"""Cross-channel revisit over the T1 refund lifecycle."""

from . import t1

oracle = t1.oracle


def t9_case() -> dict:
    case = t1.t1_case()
    case["case_id"] = "cross_channel_revisit_001"
    case["template"] = "cross_channel_revisit"
    case["capabilities"] = ["continuity"]
    # Place the email after both Oracle retries and before either settles.
    case["visits"][1].update(
        t="D3 15:00",
        channel="email",
        goal="Ask about the progress of a refund you applied for earlier; you do not volunteer an order number or ticket number.",
        utterance="The refund I applied for earlier has not arrived. Please check its progress for me.",
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
        summary="Refund progress inquiry",
    )
    call(
        "add_ticket_note", ticket_id=ticket_id, text="The customer asked by email about a refund applied for earlier."
    )
    call("say", text="I have opened a separate ticket for your refund progress inquiry.")
    return "LEFT"
