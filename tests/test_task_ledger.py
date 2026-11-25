from case_lifecycle_bench.ledger import TaskLedger


def test_generic_ledger_tracks_observed_followup_without_extra_tool_calls():
    captures = []
    calls = []

    def tool(name, **args):
        calls.append(name)
        return {"create_ticket": "T1", "schedule_followup": "F1"}.get(
            name, {"ok": True}
        )

    def agent(session, call):
        captures.append(session["case_ledger"])
        if session["trigger_type"] == "case_intake":
            ticket = call(
                "create_ticket", user_id="C1", subject_id="job-1", summary="job"
            )
            call(
                "add_ticket_note",
                ticket_id=ticket,
                text="Commitment: check the part tomorrow",
            )
            call(
                "schedule_followup",
                ticket_id=ticket,
                due_at="2026-01-06T10:00:00",
                note="check part",
            )

    wrapped = TaskLedger().wrap(agent)
    wrapped(
        {"trigger_type": "case_intake", "trigger_id": "I1", "t": "2026-01-05T10:00:00"},
        tool,
    )
    wrapped(
        {
            "trigger_type": "followup_due",
            "trigger_id": "F1",
            "task_id": "F1",
            "ticket_id": "T1",
            "t": "2026-01-06T10:00:00",
        },
        tool,
    )
    assert calls == ["create_ticket", "add_ticket_note", "schedule_followup"]
    assert captures[1]["followups"][0]["status"] == "fired"
    assert (
        captures[1]["commitments"][0]["text"] == "Commitment: check the part tomorrow"
    )


import unittest  # noqa: E402


class LedgerTest(unittest.TestCase):
    """unittest discovery skips plain test functions, so run this one through a TestCase."""

    def test_ledger(self):
        test_generic_ledger_tracks_observed_followup_without_extra_tool_calls()
