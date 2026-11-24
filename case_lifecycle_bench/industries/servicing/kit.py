"""Small helpers shared by the card-based templates written after L1, L2 and H1."""

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
