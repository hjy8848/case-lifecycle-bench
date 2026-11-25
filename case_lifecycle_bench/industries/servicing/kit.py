"""Small helpers shared by the card-based templates written after L1, L2 and H1."""

import re
from datetime import datetime, timedelta

from ... import eval_checks as checks
from ...lifecycle import _EPOCH, _case_time, _iso
from ...model_agent import TOOLS as STANDARD_TOOLS

NO_HUMAN_REQUEST = "你不会要求转人工、主管，也不会提出投诉。"
CRM_NAMES = {
    "get_current_time",
    "get_ticket",
    "add_ticket_note",
    "list_followups",
    "schedule_followup",
    "cancel_followup",
    "close_ticket",
    "end_session",
}


# Tools whose text goes to the customer; a template may add its own.
MESSAGE_TOOLS = {
    "send_message_to_user",
    "send_supplement_reminder",
    "request_document_clarification",
}


# -- spec rule P7: a customer who explicitly asks for a human gets one --------------
#
# By default the simulated customer never asks for a human, so that the "hand over by
# policy" instances measure the Policy's own limits. P7 is tested apart from that: about
# two extra instances (seeds 390 and 391) carry a chat visit in which the customer explicitly demands a human.
# They come on top of the ten ordinary instances, so the 5/3/2 split of the outcomes stays whole.
# For those instances responsibility is only whether the agent handed over in that
# conversation, once, and did not carry on to close the case itself.

P7_RULE = (
    "If the customer explicitly asks for a human, hand the ticket over with escalate_to_human in that conversation "
    "and tell them; do not try to resolve the case yourself afterwards."
)
P7_NOTE = "已按客户要求转人工"
_ASK_HUMAN = re.compile(r"转人工|人工客服|找主管|要人工|真人")
_NOT_SUBJECT = {
    "t",
    "trigger_type",
    "trigger_id",
    "channel",
    "user_id",
    "message",
    "contact_id",
    "note",
    "event_id",
    "task_id",
    "ticket_id",
    "single_session",
}


P7_SEEDS = (390, 391)


def p7_instance(seed: int) -> bool:
    return seed in P7_SEEDS


def with_p7(generate):
    """Wrap a template's generator so the two P7 seeds have the customer demand a human."""

    def wrapped(seed: int) -> dict:
        case = generate(seed)
        if p7_instance(seed):
            case["p7_visit"] = "VH"
            case["visits"] = sorted(
                [
                    *case["visits"],
                    {
                        "id": "VH",
                        "t": "D1 17:00",
                        "channel": "secure_chat",
                        "utterance": "我不想跟机器人说，请直接给我转人工客服。",
                        "goal": "你明确要求转人工客服处理，坚持要真人，不接受机器人继续处理。",
                        "script": {},
                    },
                ],
                key=lambda row: _case_time(row["t"]),
            )
        return case

    wrapped.__name__ = getattr(generate, "__name__", "generate")
    return wrapped


def with_p7_oracle(oracle):
    """The Oracle hands over when asked and then stays out of the case."""

    def wrapped(session, call):
        if session["trigger_type"] == "case_intake":
            return oracle(session, call)
        subject = {
            key: value
            for key, value in session.items()
            if key.endswith("_id") and key not in _NOT_SUBJECT
        }
        visit = session["trigger_type"] == "user_visit"
        user_id = session["user_id"]
        tickets = call("search_tickets", user_id=user_id, **subject)
        ticket_id = tickets[0]["ticket_id"] if tickets else None
        handed = ticket_id is not None and any(
            P7_NOTE in note["text"] for note in call("get_ticket", ticket_id=ticket_id)["notes"]
        )
        if handed:
            if visit:
                call("say", text="已为您转人工，请等待人工同事联系。")
                return "CONFIRMED"
            return None
        if visit and _ASK_HUMAN.search(session.get("message") or ""):
            if ticket_id is None:
                ticket_id = call("create_ticket", user_id=user_id, summary="客户要求转人工", **subject)
            call("say", text="好的，已为您转人工，同事会尽快与您联系。")
            call("escalate_to_human", ticket_id=ticket_id, note="客户明确要求转人工。")
            call("add_ticket_note", ticket_id=ticket_id, text=P7_NOTE)
            return "CONFIRMED"
        return oracle(session, call)

    wrapped.__name__ = getattr(oracle, "__name__", "oracle")
    return wrapped


def crm_tools():
    return [tool for tool in STANDARD_TOOLS if tool["function"]["name"] in CRM_NAMES]


def label(moment: datetime) -> str:
    return f"D{(moment - _EPOCH).days + 1} {moment:%H:%M}"


def shift(text: str, **delta) -> str:
    return label(_case_time(text) + timedelta(**delta))


def iso_at(text: str) -> str:
    return _iso(_case_time(text))


def next_at(session, hour: int, minute: int = 0) -> str:
    """The next time the clock reads hour:minute, strictly after the session time."""
    now = datetime.fromisoformat(session["t"])
    nxt = now.replace(hour=hour, minute=minute, second=0)
    return _iso(nxt if nxt > now else nxt + timedelta(days=1))


def name_agents(agents: dict) -> dict:
    for name, agent in agents.items():
        agent.__name__ = name.replace("-", "_")
    return agents


def agent_notices(trajectory):
    """Everything the agent told the customer: chat or call speech and inbox messages."""
    return [
        row
        for row in trajectory
        if (row["kind"] == "message" and row["role"] == "agent")
        or (row.get("tool") in MESSAGE_TOOLS and row["committed"])
    ]


def text_of(row) -> str:
    return row["text"] if row["kind"] == "message" else row["args"]["text"]


def replay_starts(trajectory, initial_state, summarize):
    """State of the work item, ticket and handoffs at the start of every session."""
    record = next(iter(initial_state["work_items"].values()))
    closed, handoffs = False, 0
    starts = {}
    for row in trajectory:
        if row["kind"] == "session_start":
            starts[row["session_id"]] = {
                **summarize(record),
                "closed": closed,
                "handoffs": handoffs,
            }
        changes = row.get("changes") or {}
        for item in changes.get("work_items", {}).values():
            record = item
        for ticket in changes.get("tickets", {}).values():
            closed = ticket["status"] == "closed"
        handoffs += len(changes.get("handoffs", {}))
    return starts


def states_over_time(trajectory, initial_state):
    """Yield (row, record) where record is the work item as it stood before the row."""
    record = next(iter(initial_state["work_items"].values()))
    for row in trajectory:
        yield row, record
        for item in (row.get("changes") or {}).get("work_items", {}).values():
            record = item


def after_in_session(read, later):
    return checks.same_session(read, later) and read["seq"] < later["seq"]
