"""Deterministic agents for the synthetic insurance renewal case."""

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from random import Random

from ...lifecycle import _case_time

_NO_ANSWER = "已尝试主动联系，客户未接听，等待客户通过安全聊天回访。"


def generate_renewal_case(seed: int) -> dict:
    """Vary quote timing, customer availability and synthetic identifier format."""
    rng = Random(seed)
    ready_at = rng.choice(
        (
            "D2 08:00",
            "D2 08:15",
            "D2 08:30",
            "D2 08:45",
            "D2 13:00",
            "D2 14:00",
            "D2 15:30",
            "D2 17:00",
        )
    )
    case = renewal_case(ready_at)
    case["case_id"] = f"insurance_renewal_quote_followup_seed_{seed}"
    number = rng.randrange(10**6, 10**7)
    policy_id, customer_id = rng.choice(
        (
            (f"POL-{number}", f"CUS-{number}"),
            (f"810001{number}", f"910001{number}"),
            (f"#CLB-POL-{number}", f"#CLB-CUS-{number}"),
        )
    )
    case["init"]["policy"]["policy_id"] = policy_id
    case["init"]["customer"]["customer_id"] = customer_id
    case["visits"][0]["t"] = rng.choice(("D3 14:00", "D3 15:00", "D3 17:00"))
    script = case["oracle"]["scripted_outbound_customer"]
    script["answers"] = rng.choice((True, True, False))
    if not script["answers"]:
        case["oracle"]["outreach"] = [
            "Make one compliant call after checking the quote. If unanswered, record the attempt and handle the conditional secure-chat revisit."
        ]
        case["checks"]["process"][0]["evidence"][-1] = (
            "a compliant timely voice attempt, followed by renewal in secure chat if unanswered"
        )
    return case


def renewal_case(
    quote_ready_at: str | None = None, *, revise_after_consent=False
) -> dict:
    path = Path(__file__).with_name("renewal_quote_followup.json")
    case = json.loads(path.read_text(encoding="utf-8"))
    quote_ready_at = quote_ready_at or case["events"][0]["t"]
    if revise_after_consent:
        case["case_id"] += "_revision"
        case["revision_after_consent"] = {
            "id": "E2",
            "from_version": "Q2",
            "changes": {"quote": {"version": "Q3", "premium_test_credits": 110}},
        }
        case["oracle"]["scripted_outbound_customer"]["accepts_quote_version"] = [
            "Q2",
            "Q3",
        ]
        case["oracle"]["outreach"][1:3] = [
            "Obtain Q2 consent. When the insurer revises the quote, re-read the quote and obtain fresh consent for Q3.",
            "Submit Q3, verify the authoritative renewal state and deliver the confirmation.",
        ]
    if quote_ready_at == case["events"][0]["t"]:
        return case
    ready = _case_time(quote_ready_at)
    if not _case_time("D1 10:00") < ready <= _case_time("D3 09:00"):
        raise ValueError("Quote release must be after D1 10:00 and by D3 09:00")
    case["case_id"] += "_" + quote_ready_at.lower().replace(" ", "_").replace(":", "")
    case["events"][0]["t"] = quote_ready_at
    if ready > _case_time("D2 09:00"):
        case["deadlines"]["proactive_contact"] = "D3 10:00"
    case["oracle"]["poll"] = [
        "At D2 09:00, if the quote is still under review, recheck at D3 09:00."
    ]
    case["oracle"]["push"] = [
        "Check the quote at E1. Contact immediately if the window is open. Otherwise retain or schedule the next opening."
    ]
    case["oracle"]["outreach"][0] = (
        "Verify opt-in and the current quote before starting one voice contact within the window."
    )
    case["checks"]["process"][0]["evidence"][-1] = (
        "current quote delivered by the fixture's proactive_contact deadline"
    )
    return case


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
    ticket = call("get_ticket", ticket_id=ticket_id)
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

    if trigger in {"system_event", "followup_due"}:
        now = datetime.fromisoformat(session["t"])
        if policy["renewal_state"] == "confirmed":
            return None
        if any(note["text"] == _NO_ANSWER for note in ticket["notes"]):
            return None
        if quote["status"] == "ready" and (
            time(9) <= now.time() < time(18) or early and trigger == "followup_due"
        ):
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
                        text=_NO_ANSWER
                        if result.get("status") == "no_answer"
                        else "主动联系未完成，续保仍待办理。",
                    )
        elif trigger == "followup_due" or not any(
            row["status"] == "pending"
            for row in call("list_followups", ticket_id=ticket_id)
        ):
            due = now.replace(hour=9, minute=0)
            if due <= now:
                due += timedelta(days=1)
            call(
                "schedule_followup",
                ticket_id=ticket_id,
                due_at=due.isoformat(),
                note="复查续保报价",
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
        if acceptance.get("quote_revised"):
            quote = call("read_renewal_quote", policy_id=policy_id)
            contact["quote_version"] = quote["version"]
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
