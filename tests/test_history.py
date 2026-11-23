import unittest

from case_lifecycle_bench.cases.t2 import oracle, t2_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.history import FullHistory


class FullHistoryTest(unittest.TestCase):
    def test_history_exposes_only_observed_transcript_without_changing_the_run(self):
        case = t2_case()
        seen = {}

        def agent(session, call):
            seen[session["trigger_id"]] = session["full_history"]
            return oracle(session, call)

        run = run_case(case, FullHistory().wrap(agent), wake="push")
        self.assertEqual(run, run_case(case, oracle, wake="push"))
        self.assertEqual(seen["V1"], [])
        self.assertEqual(len(seen["E1"]), 1)
        previous = seen["E1"][0]
        self.assertEqual(previous["trigger_id"], "V1")
        self.assertEqual(
            previous["records"][0],
            {
                "role": "user",
                "text": case["visits"][0]["utterance"],
            },
        )
        issue = next(
            row for row in previous["records"] if row.get("tool") == "issue_refund"
        )
        self.assertEqual(set(issue), {"tool", "args", "result"})
        self.assertEqual(issue["result"], {"error": "timeout"})
        query = next(
            row for row in previous["records"] if row.get("tool") == "list_refunds"
        )
        self.assertEqual(query["result"][0]["status"], "processing")
        self.assertEqual(previous["records"][-1]["role"], "agent")

    def test_history_captures_followup_dialogue_and_isolated_snapshots(self):
        wrap = FullHistory().wrap
        seen = []

        def agent(session, call):
            seen.append(session["full_history"])
            if session["trigger_type"] == "user_visit":
                call("say", text="Checking the order.")
                call("user_reply", text="Please check the refund too.")
                call("end_session")
                call(
                    "get_current_time"
                )  # Ends at the boundary without executing this call.

        case = t2_case()
        case["visits"] += [
            {**case["visits"][0], "id": "V2", "t": "D2 09:00"},
            {**case["visits"][0], "id": "V3", "t": "D3 09:00"},
        ]
        run_case(case, wrap(agent))
        self.assertEqual(
            seen[1][0]["records"][1:],
            [
                {"role": "agent", "text": "Checking the order."},
                {"role": "user", "text": "Please check the refund too."},
                {"tool": "end_session", "args": {}, "result": {"ok": True}},
            ],
        )
        seen[1][0]["records"].clear()
        self.assertTrue(seen[2][0]["records"])
        run_case(case, wrap(agent))
        self.assertEqual(seen[3], [])
