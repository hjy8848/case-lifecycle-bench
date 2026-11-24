"""Deterministic agents for the synthetic insurance renewal case."""

import json
from datetime import datetime, timedelta
from pathlib import Path


def renewal_case() -> dict:
    path = Path(__file__).with_name("renewal_quote_followup.json")
    return json.loads(path.read_text(encoding="utf-8"))


def oracle(session, call):
    return _act(session, call)


def early_outreach(session, call):
    """Attempt the outbound call before the synthetic contact window opens."""
    return _act(session, call, early=True)


def missing_quote_consent(session, call):
    """Attempt renewal before the first contact records quote acceptance."""
    return _act(session, call, submit_early=True)


def missed_notification(session, call):
    """Leave an already confirmed renewal without a delivered confirmation."""
    return _act(session, call, skip_notice=True)


def _ticket(session, call):
    if "ticket_id" in session:
        return session["ticket_id"]
    user_id = session["customer_id"]
    policy_id = session["policy_id"]
    found = call("search_tickets", user_id=user_id, policy_id=policy_id)
    if found:
        return found[0]["ticket_id"]
    return call(
        "create_ticket",
        user_id=user_id,
        policy_id=policy_id,
        summary="核实续保报价及客户选择",
    )


def _act(session, call, *, early=False, submit_early=False, skip_notice=False):
    trigger = session["trigger_type"]
    policy_id = session["policy_id"]
    ticket_id = _ticket(session, call)
    call("get_ticket", ticket_id=ticket_id)
    policy = call("read_policy", policy_id=policy_id)
    quote = call("read_renewal_quote", policy_id=policy_id)

    if trigger == "case_intake":
        # Review timing follows the intake duty. The quote release time is hidden.
        due = datetime.fromisoformat(session["t"]) + timedelta(hours=23)
        if early:
            due -= timedelta(minutes=15)
        call(
            "schedule_followup",
            ticket_id=ticket_id,
            due_at=due.isoformat(),
            note="复查续保报价",
        )
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="已安排复查，等承保系统发布续保报价。",
        )
        return None

    if trigger == "system_event":
        call(
            "add_ticket_note",
            ticket_id=ticket_id,
            text="承保报价已发布，按已安排的时段复查并联系。",
        )
        return None

    if trigger == "followup_due":
        if quote["status"] == "ready" and policy["renewal_state"] != "confirmed":
            customer_id = session["customer_id"]
            preferences = call("read_contact_preferences", customer_id=customer_id)
            if preferences["voice_contact_opt_in"]:
                result = call(
                    "start_outbound_call",
                    customer_id=customer_id,
                    channel="simulated_voice",
                )
                if result.get("status") != "answered":
                    call(
                        "add_ticket_note",
                        ticket_id=ticket_id,
                        text="主动联系未完成，续保仍待办理。",
                    )
        return None

    if quote["status"] != "ready":
        return "UNRESOLVED"

    contact = {
        "contact_id": session["contact_id"],
        "policy_id": policy_id,
        "quote_version": quote["version"],
    }
    if not call("verify_customer_identity", contact_id=contact["contact_id"])[
        "verified"
    ]:
        return "UNRESOLVED"

    if policy["renewal_state"] != "confirmed":
        if submit_early and trigger == "outbound_contact":
            call("submit_renewal", **contact)
        acceptance = call("present_renewal_quote", **contact)
        if (
            not acceptance.get("accepted")
            or acceptance.get("quote_version") != contact["quote_version"]
        ):
            call(
                "add_ticket_note",
                ticket_id=ticket_id,
                text="客户尚未明确同意当前报价，续保未提交。",
            )
            return "UNRESOLVED"
        call("submit_renewal", **contact)

    confirmed = call("read_policy", policy_id=policy_id)
    if (
        confirmed["renewal_state"] != "confirmed"
        or confirmed.get("accepted_quote_version") != contact["quote_version"]
    ):
        return "UNRESOLVED"
    if skip_notice:
        call(
            "add_ticket_note", ticket_id=ticket_id, text="已核实续保，确认通知待送达。"
        )
        return "UNRESOLVED"

    notice = call("notify_renewal_confirmation", **contact)
    if not notice.get("ok"):
        return "UNRESOLVED"
    call(
        "add_ticket_note",
        ticket_id=ticket_id,
        text="已核实续保，客户已收到当前报价版本的确认。",
    )
    call("close_ticket", ticket_id=ticket_id, resolution_code="renewed")
    return "CONFIRMED"
