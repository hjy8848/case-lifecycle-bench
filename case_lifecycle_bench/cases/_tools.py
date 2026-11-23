"""Shared operations for the deterministic case agents."""

import json
from datetime import datetime, timedelta
from pathlib import Path


def load_fixture(name):
    return json.loads(
        Path(__file__).with_name(f"{name}.yaml").read_text(encoding="utf-8")
    )


def schedule_followup(session, call, ticket_id, hours, note):
    due_at = datetime.fromisoformat(session["t"]) + timedelta(hours=hours)
    call("schedule_followup", ticket_id=ticket_id, due_at=due_at.isoformat(), note=note)


def get_trigger_ticket(session, call):
    ticket_id = session.get("ticket_id")
    if ticket_id is None:
        order_id = session["order_id"]
        ticket_id = call("search_tickets", order_id=order_id)[0]["ticket_id"]
    return call("get_ticket", ticket_id=ticket_id)


def cancel_pending_followups(call, ticket_id, note):
    for task in call("list_followups", ticket_id=ticket_id):
        if task["status"] == "pending" and task["note"] == note:
            call("cancel_followup", task_id=task["task_id"])
