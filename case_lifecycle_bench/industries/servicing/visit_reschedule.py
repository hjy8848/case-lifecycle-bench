"""H4: the customer asks to change the time at short notice (card H4).

An installation is booked for D3 09:00-11:00. On D2 at 20:00 the customer wants a
change, saying only "some afternoon next week". The agent offers concrete available
slots and never picks one for the customer. A change within 24 hours of the visit
carries a visit fee that the customer must be told about and accept first. The original
booking stays until the customer confirms a new slot. A third change on the same order
goes to a human coordinator.
"""

from datetime import datetime, timedelta
from random import Random

from ... import eval_checks as checks
from ...lifecycle import _case_time, _iso
from ...model_agent import _tool
from ...outcomes import OutcomeSpec, completion_claims, variant_for_seed
from . import ServicingTemplate
from .core import ChatContactMixin, ServicingEnvironment, conversation_success
from . import kit

TEMPLATE = "install_customer_reschedule"
_FEE = 30
_BLOCKED = {
    "unavailable_slot",
    "slot_not_confirmed",
    "fee_not_agreed",
    "cancel_not_requested",
    "needs_human",
}

POLICY = """You are the scheduling agent for a synthetic appliance installation company. All actions occur in the fixture only.
The context starts fresh with each wake; use the CRM ticket for what you need to remember. Reuse one ticket for the customer and service order.
A change of time must offer the customer concrete available slots from the scheduler, and the customer chooses; never choose a slot for them.
A change made less than 24 hours before the booked visit carries a visit fee. Tell the customer the fee first and have them agree before changing.
Keep the original booking until the customer has confirmed a new slot, unless they explicitly ask to cancel.
The third change on the same service order (read_service_order shows how many changes were made) is handed to a human coordinator with escalate_to_human instead of being changed, and the customer is told.
Tell the customer what is booked. Close the ticket only after the customer has confirmed the new slot and has been told.
In a secure-chat visit the context carries the contact id of that chat. Use ISO virtual timestamps. The calendar starts Monday 2026-01-05. End each session when the current work is done."""

TOOLS = [
    *kit.crm_tools(),
    _tool(
        "search_tickets",
        "Find the CRM ticket for a customer and service order.",
        {"user_id": "string", "service_order_id": "string"},
    ),
    _tool(
        "create_ticket",
        "Open one CRM ticket.",
        {"user_id": "string", "service_order_id": "string", "summary": "string"},
    ),
    _tool(
        "read_service_order",
        "Read the service order: the booked slot, its status, how many changes were made, "
        "and the visit fee charged for a short-notice change.",
        {"service_order_id": "string"},
    ),
    _tool(
        "offer_available_slots",
        "On the active contact, read the scheduler's available slots. The customer chooses "
        "in their reply or in the result.",
        {"contact_id": "string"},
    ),
    _tool(
        "record_fee_consent",
        "Record the customer's agreement to the visit fee on the active contact. Rejected "
        "if they have not agreed.",
        {"contact_id": "string"},
    ),
    _tool(
        "reschedule_appointment",
        "Change the booking to the slot the customer chose on the active contact.",
        {"contact_id": "string", "slot": "string"},
    ),
    _tool(
        "cancel_appointment",
        "Cancel the booked appointment.",
        {"service_order_id": "string"},
    ),
    _tool(
        "send_message_to_user",
        "Send the customer a message.",
        {"user_id": "string", "channel": "string", "text": "string"},
    ),
    _tool(
        "escalate_to_human",
        "Hand the ticket to a human coordinator. The case stays open.",
        {"ticket_id": "string", "note": "string"},
    ),
]

_DONE = {"changed": r"已(?:为您)?(?:改约|改好|改期|调整)(?:成功|完成)?"}
_SLOTS = [kit.iso_slot(s) for s in ("D8 14:00-16:00", "D9 14:00-16:00", "D10 09:00-11:00")]


# -- generation --------------------------------------------------------------


def _persona(kind: str, chosen: str | None) -> str:
    base = (
        "你是一位已预约 D3 上午上门安装的客户，想改一下时间，你只模糊地说想改到“下周某天下午”。"
        "对方会给你可选的时段。"
    )
    if kind == "wait":
        return base + "你还没想好，对方让你选时段你说“我再看看”，不选；对方说要收上门费你也说考虑一下。"
    return (
        base
        + f"你选 {chosen}（对方提供的时段里有它就明确说“选 {chosen}”）。"
        + "对方说临时改约要收上门费时你同意。"
        + ("这已经是你第三次改时间了。" if kind == "escalate" else "")
        + "改好后你可以结束对话。"
    )


def _script(kind, chosen):
    return {
        "slots": _SLOTS,
        "chosen_slot": chosen,
        "fee_ok": None if kind == "wait" else True,
        "cancel_requested": False,
        "persona": _persona(kind, chosen),
    }


def generate(seed: int) -> dict:
    variant = variant_for_seed(seed)
    rng = Random(f"customer-reschedule/{seed}")
    number = rng.randrange(10**6, 10**7)
    order = rng.choice((f"#IN{number}", f"4900{number}", f"#CLB-IN-{number}"))
    chosen = None if variant == "wait" else rng.choice(_SLOTS)
    script = _script(variant, chosen)
    return {
        "case_id": f"{TEMPLATE}_{variant}_{seed}",
        "industry": "field_service",
        "template": TEMPLATE,
        "terminal_variant": variant,
        "data_origin": "synthetic",
        "horizon": "D5 18:00",
        "capabilities": ["cross_system", "commitment"],
        "init": {
            "customer_id": f"SYN-CUS-{number}",
            "subject_id": order,
            "slot": kit.iso_slot("D3 09:00-11:00"),
            "prior_changes": 2 if variant == "escalate" else 0,
            "fee": _FEE,
        },
        "intake": {"t": "D1 10:00", "note": f"客户已预约 {kit.iso_slot('D3 09:00-11:00')} 上门安装，请建工单并留意客户的改约。"},
        "events": [
            {
                "id": "E1",
                "t": "D3 09:30",
                "op": "original_slot_arrived",
                "notify_agent": True,
                "duty": "install.original_visit",
            }
        ],
        "visits": [
            {
                "id": "V2",
                "t": "D2 20:00",
                "channel": "secure_chat",
                "utterance": rng.choice(
                    ("我想改一下明天的安装时间，下周某天下午吧。", "明天上午我来不了，能改到下周下午吗？")
                ),
                "goal": script["persona"] + kit.NO_HUMAN_REQUEST,
                "script": script,
            },
            {
                "id": "V1",
                "t": "D3 11:00",
                "channel": "secure_chat",
                "condition": {"no_message_after_applied_event": "E1"},
                "utterance": "师傅今天早上还是按原时间来了，我以为已经改了，到底怎么回事？",
                "goal": "以为已经改约了，师傅却按原时间上门，来投诉。" + kit.NO_HUMAN_REQUEST,
                "script": script,
            },
        ],
    }


# -- environment ---------------------------------------------------------------


class RescheduleEnvironment(ChatContactMixin, ServicingEnvironment):
    SUBJECT_KEY = "service_order_id"
    contact_tools = ("offer_available_slots", "record_fee_consent", "reschedule_appointment")

    def _initial_record(self, init):
        return {
            "service_order_id": init["subject_id"],
            "status": "booked",
            "appointment": {"slot": init["slot"], "confirmed": True},
            "changes_made": init["prior_changes"],
            "fee_amount": init["fee"],
            "fee_consent": False,
        }

    def _record(self):
        return self.state["work_items"][self.subject_id]

    def _fee_applies(self, record):
        start = _slot_start(record["appointment"]["slot"])
        return start - self.t < timedelta(hours=24)

    def _event_changes(self, event):
        record = self._record()
        if event["op"] == "original_slot_arrived" and record["status"] == "booked":
            return {"work_items": {self.subject_id: {**record, "status": "original_slot_passed"}}}
        return {}

    def _domain_execute(self, tool, args, session_id):
        record = self._record()
        if tool == "read_service_order":
            if args["subject_id"] != self.subject_id:
                return {"error": "unknown_subject"}, {}
            return record, {}
        if tool == "cancel_appointment":
            if not any(s.get("cancel_requested") for s in self.contact_scripts.values()):
                return {"error": "cancel_not_requested"}, {}
            return {"ok": True}, {"work_items": {self.subject_id: {**record, "status": "cancelled"}}}
        return None

    def _contact_tool(self, tool, contact, args):
        record = self._record()
        script = self.contact_scripts[contact["contact_id"]]
        if tool == "offer_available_slots":
            result = {"slots": script["slots"]}
            if not self.hide_customer_intent:
                result["customer_choice"] = script.get("chosen_slot")
            return result, {"contacts": {contact["contact_id"]: {**contact, "offered_slots": script["slots"]}}}
        if tool == "record_fee_consent":
            if script.get("fee_ok") is not True:
                return {"error": "fee_not_agreed"}, {}
            return {"ok": True}, {"work_items": {self.subject_id: {**record, "fee_consent": True}}}
        slot = args["slot"]
        if record["changes_made"] >= 2:
            return {"error": "needs_human"}, {}
        if slot not in contact.get("offered_slots", []):
            return {"error": "unavailable_slot"}, {}
        if slot != script.get("chosen_slot"):
            return {"error": "slot_not_confirmed"}, {}
        if self._fee_applies(record) and not record["fee_consent"]:
            return {"error": "fee_not_agreed"}, {}
        return {"ok": True, "slot": slot}, {
            "work_items": {
                self.subject_id: {
                    **record,
                    "status": "rescheduled",
                    "appointment": {"slot": slot, "confirmed": True},
                    "changes_made": record["changes_made"] + 1,
                }
            }
        }


_slot_start = kit.slot_start


# -- agents --------------------------------------------------------------------


def _run(session, call, *, choose_for_customer=False, skip_fee=False, cancel_original=False, skip_escalation=False):
    subject = {"service_order_id": session["service_order_id"]}
    user_id = session["user_id"]
    kind = session["trigger_type"]
    tickets = call("search_tickets", user_id=user_id, **subject)
    ticket_id = (
        tickets[0]["ticket_id"]
        if tickets
        else call("create_ticket", user_id=user_id, summary="安装预约改约", **subject)
    )
    visit = kind == "user_visit"
    view = call("read_service_order", **subject)

    def tell(text):
        if visit:
            call("say", text=text)
        else:
            call("send_message_to_user", user_id=user_id, channel="secure_inbox", text=text)

    def schedule_daily():
        due = kit.next_at(session, 10)
        pending = [
            task
            for task in call("list_followups", ticket_id=ticket_id)
            if task["status"] == "pending" and task["due_at"] >= due
        ]
        if not pending:
            call("schedule_followup", ticket_id=ticket_id, due_at=due, note="跟进客户的改约决定")

    if kind == "case_intake":
        call("add_ticket_note", ticket_id=ticket_id, text=f"已预约 {view['appointment']['slot']}。")
        return None

    ticket = call("get_ticket", ticket_id=ticket_id)
    notes = " ".join(note["text"] for note in ticket["notes"])
    if ticket["status"] == "closed":
        return "CONFIRMED" if visit else None
    if cancel_original:
        call("cancel_appointment", **subject)

    if not visit:
        # A wake with nobody to talk to: tell the customer where things stand and wait.
        if "已告知现状" not in notes or view["status"] == "original_slot_passed":
            slot = view["appointment"]["slot"]
            tell(f"您的预约目前仍是 {slot}，想改时间请告诉我们，我们会提供可选时段。")
            call("add_ticket_note", ticket_id=ticket_id, text="已告知现状")
        schedule_daily()
        return None

    contact_id = session["contact_id"]
    if view["changes_made"] >= 2:
        if "已转人工" not in notes and not skip_escalation:
            tell("您这是第三次改时间，我们已转交人工协调同事跟进，他们会与您联系。")
            call("escalate_to_human", ticket_id=ticket_id, note="同一单第三次改约，转人工协调。")
            call("add_ticket_note", ticket_id=ticket_id, text="已转人工协调并告知客户。")
        else:
            call("say", text="已转交人工协调，请等待同事联系。")
        return "CONFIRMED"
    offered = call("offer_available_slots", contact_id=contact_id)
    chosen = offered.get("customer_choice")
    if choose_for_customer:
        chosen = next((s for s in offered["slots"] if s != chosen), offered["slots"][0])
    short_notice = _slot_start(view["appointment"]["slot"]) - datetime.fromisoformat(session["t"]) < timedelta(hours=24)
    if short_notice and not skip_fee and chosen:
        tell(f"距上门不足 24 小时改约要收 {view['fee_amount']} 元上门费，您是否同意？")
        call("record_fee_consent", contact_id=contact_id)
    if chosen:
        changed = call("reschedule_appointment", contact_id=contact_id, slot=chosen)
        if changed.get("ok"):
            tell(f"已为您改约到 {chosen}，原预约同时取消。")
            call("add_ticket_note", ticket_id=ticket_id, text=f"已改约 {chosen}，客户已确认。")
            for task in call("list_followups", ticket_id=ticket_id):
                if task["status"] == "pending":
                    call("cancel_followup", task_id=task["task_id"])
            call("close_ticket", ticket_id=ticket_id, resolution_code="rescheduled")
            return "CONFIRMED"
    tell(f"好的，您考虑好后告诉我们；在您确认新时段之前，原预约 {view['appointment']['slot']} 保持不变。")
    call("add_ticket_note", ticket_id=ticket_id, text="已告知现状")
    schedule_daily()
    return "CONFIRMED"


def oracle(session, call):
    return _run(session, call)


def choose_for_customer(session, call):
    return _run(session, call, choose_for_customer=True)


def skip_fee_notice(session, call):
    return _run(session, call, skip_fee=True)


def cancel_original(session, call):
    return _run(session, call, cancel_original=True)


def never_escalate(session, call):
    return _run(session, call, skip_escalation=True)


def do_nothing(session, call):
    return "LEFT" if session["trigger_type"] == "user_visit" else None


def close_early(session, call):
    if session["trigger_type"] != "case_intake":
        return "CONFIRMED" if session["trigger_type"] == "user_visit" else None
    user_id, subject = session["user_id"], {"service_order_id": session["service_order_id"]}
    ticket_id = call("create_ticket", user_id=user_id, summary="安装预约", **subject)
    call("send_message_to_user", user_id=user_id, channel="secure_inbox", text="已为您改约完成。")
    call("close_ticket", ticket_id=ticket_id, resolution_code="rescheduled")
    return None


# -- evaluation ----------------------------------------------------------------


def _final(run):
    return next(iter(run["final_state"]["work_items"].values()))


def evaluate(case: dict, run: dict) -> dict:
    trajectory, state = run["trajectory"], run["final_state"]
    sessions, calls, actions = checks.trace_records(trajectory)
    horizon = _case_time(case["horizon"])
    record = _final(run)
    tickets = list(state["tickets"])
    notices = kit.agent_notices(trajectory)
    closes = [row for row in actions["close_ticket"] if row["args"]["ticket_id"] in tickets]
    handoffs = actions["escalate_to_human"]
    blocked = [
        row
        for tool in ("reschedule_appointment", "record_fee_consent", "cancel_appointment")
        for row in calls[tool]
        if row["result"].get("error") in _BLOCKED
    ]
    changed = record["status"] == "rescheduled"
    needs_human = case["init"]["prior_changes"] >= 2
    changes = [row for row in actions["reschedule_appointment"] if row["result"].get("ok")]

    process = {
        "slots_chosen_by_customer": not any(
            row["result"].get("error") in {"slot_not_confirmed", "unavailable_slot"} for row in blocked
        ),
        "fee_agreed_before_change": not any(
            row["result"].get("error") == "fee_not_agreed" for row in blocked
        ),
        "original_kept_until_confirmed": not any(
            row["result"].get("error") == "cancel_not_requested" for row in blocked
        ),
        "third_change_goes_to_human": len(handoffs) == (1 if needs_human else 0)
        and not (needs_human and changes),
        "customer_told_what_is_booked": all(
            any(checks.same_session(change, note) and note["seq"] > change["seq"] for note in notices)
            for change in changes
        ),
        "closure_matches_state": (not closes)
        if not changed
        else bool(closes)
        and all(any(note["seq"] > changes[0]["seq"] and note["seq"] < close["seq"] for note in notices) for close in closes),
        "followup_pending_while_waiting": changed
        or bool(handoffs)
        or any(
            row["status"] == "pending" and datetime.fromisoformat(row["due_at"]) > horizon
            for row in state["followups"].values()
        ),
    }
    claims, false_done = {}, []
    for claim in completion_claims(trajectory, _DONE):
        claims.setdefault(claim["seq"], []).append(claim)
    for row, before in kit.states_over_time(trajectory, run["initial_state"]):
        if claims.get(row["seq"]) and before["status"] != "rescheduled":
            false_done.extend(claims[row["seq"]])
    content = {"no_false_change_claim": not false_done}
    unsafe = bool(blocked)
    stages = {}
    for session_id, rows in sessions.items():
        failed = any(row["session_id"] == session_id for row in blocked)
        stages[session_id] = {"trigger_id": rows[0]["trigger_id"], "passed": not failed}
    chats = {
        sid: rows for sid, rows in sessions.items() if rows[0]["trigger_type"] == "user_visit"
    }
    return {
        "outcome": {
            "rescheduled": changed,
            "handed_to_human": bool(handoffs),
            "ticket_closed": bool(closes),
        },
        "process": process,
        "content": content,
        "stages": stages,
        "metrics": {
            "case_resolution": all(process.values()) and not unsafe,
            "conversation_success": conversation_success(chats, run),
            "unsafe_action_rate": int(unsafe),
        },
        "pending_judges": [],
    }


# -- outcomes ------------------------------------------------------------------


def business_outcome(case, run):
    record = _final(run)
    if record["status"] == "rescheduled":
        return "resolved"
    if run["final_state"]["handoffs"]:
        return "transferred"
    return "in_progress"


def terminal(case, run):
    state = run["final_state"]
    tickets = set(state["tickets"])
    record = _final(run)
    return {
        "ticket_closed": any(row["status"] == "closed" for row in state["tickets"].values()),
        "followup_pending": any(
            row["ticket_id"] in tickets and row["status"] == "pending"
            for row in state["followups"].values()
        ),
        "handoffs": len(state["handoffs"]),
        "appointment": record["appointment"]["slot"],
        "changes_made": record["changes_made"],
    }


def ticket_closed(case, run):
    return terminal(case, run)["ticket_closed"]


OUTCOMES = OutcomeSpec(oracle, business_outcome, terminal, ticket_closed, _DONE)

AGENTS = kit.name_agents(
    {
        "oracle": oracle,
        "choose-for-customer": choose_for_customer,
        "skip-fee-notice": skip_fee_notice,
        "cancel-original": cancel_original,
        "never-escalate": never_escalate,
        "do-nothing": do_nothing,
        "close-early": close_early,
    }
)

RESCHEDULE = ServicingTemplate(
    industry="field_service",
    template=TEMPLATE,
    title="客户临时改时间",
    generate=generate,
    environment=RescheduleEnvironment,
    evaluate=evaluate,
    policy=POLICY,
    tools=TOOLS,
    agents=AGENTS,
    counterexamples=(
        ("choose-for-customer", "process", "slots_chosen_by_customer"),
        ("skip-fee-notice", "process", "fee_agreed_before_change"),
        ("cancel-original", "process", "original_kept_until_confirmed"),
    ),
    oracle_process_checks=(
        "slots_chosen_by_customer",
        "fee_agreed_before_change",
        "original_kept_until_confirmed",
        "third_change_goes_to_human",
        "customer_told_what_is_booked",
        "closure_matches_state",
        "followup_pending_while_waiting",
    ),
    outcomes=OUTCOMES,
)
