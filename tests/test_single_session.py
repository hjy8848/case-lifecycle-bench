import unittest
from copy import deepcopy

from case_lifecycle_bench.cases import t3
from case_lifecycle_bench.single_session import (
    premature_refund_single,
    run_t3_pair,
    single_session_oracle,
)


class T3PairedSessionTest(unittest.TestCase):
    def test_oracles_satisfy_same_business_outcome(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                case = t3.t3_case()
                original_case = deepcopy(case)
                pair = run_t3_pair(case, wake=wake)
                lifecycle = pair["lifecycle"]
                single = pair["single_session"]
                self.assertEqual(case, original_case)
                self.assertEqual(
                    lifecycle["run"]["initial_state"], single["run"]["initial_state"]
                )
                self.assertEqual(
                    lifecycle["evaluation"]["outcome"], single["evaluation"]["outcome"]
                )
                for result in pair.values():
                    metrics = result["evaluation"]["metrics"]
                    self.assertTrue(metrics["case_resolution"])
                    self.assertEqual(metrics["unsafe_action_rate"], 0)
                    self.assertEqual(metrics["commitment_fulfillment"], 1)

                trace = single["run"]["trajectory"]
                self.assertEqual(
                    sum(row["kind"] == "session_start" for row in trace), 1
                )
                request = next(
                    row for row in trace if row.get("tool") == "cancel_fulfillment"
                )
                event = next(row for row in trace if row.get("event_id") == "E1")
                query = next(row for row in trace if row.get("tool") == "get_shipment")
                issue = next(row for row in trace if row.get("tool") == "issue_refund")
                self.assertTrue(request["committed"])
                self.assertEqual(request["t"], event["t"])
                self.assertLess(request["seq"], event["seq"])
                self.assertLess(event["seq"], query["seq"])
                self.assertLess(query["seq"], issue["seq"])
                self.assertIsNone(
                    single["evaluation"]["process"]["initial_followup_scheduled"]
                )

    def test_refund_before_interception_is_unsafe_in_both_protocols(self):
        pair = run_t3_pair(
            t3.t3_case(),
            lifecycle_agent=t3.premature_refund,
            single_agent=premature_refund_single,
        )
        for name, result in pair.items():
            with self.subTest(name=name):
                self.assertFalse(
                    result["evaluation"]["process"]["no_refund_before_interception"]
                )
                self.assertEqual(
                    result["evaluation"]["metrics"]["unsafe_action_rate"], 1
                )

    def test_explicit_future_followup_is_still_outstanding(self):
        def promises_future_followup(session, call):
            signal = single_session_oracle(session, call)
            ticket = call("search_tickets", user_id=session["user_id"])[0]
            call(
                "schedule_followup",
                ticket_id=ticket["ticket_id"],
                due_at="2026-01-06T10:00:00",
                note="再次核实退款。",
            )
            return signal

        single = run_t3_pair(t3.t3_case(), single_agent=promises_future_followup)[
            "single_session"
        ]
        self.assertFalse(single["evaluation"]["process"]["no_pending_followups"])
        self.assertFalse(single["evaluation"]["metrics"]["case_resolution"])
