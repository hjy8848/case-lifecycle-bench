import unittest

from case_lifecycle_bench.cases.t1 import duplicate_refund, oracle, t1_case
from case_lifecycle_bench.cases.t2 import oracle as t2_oracle, t2_case
from case_lifecycle_bench.cases.t9 import t9_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.ledger import CaseLedger


class T1LedgerTest(unittest.TestCase):
    def test_oracle_can_resolve_with_ledger(self):
        case = t1_case()
        ledger = CaseLedger()
        seen = {}

        def agent(session, call):
            seen[session["trigger_id"]] = session["case_ledger"]
            return oracle(session, call)

        run = run_case(case, ledger.wrap(agent))
        self.assertTrue(evaluate_case(case, run)["metrics"]["case_resolution"])
        self.assertIn("R1", str(seen["F1"]))
        self.assertIn("failed", str(seen["F1"]))
        self.assertFalse(ledger.guard_events)

    def test_guard_blocks_duplicate_refund_and_records_intervention(self):
        case = t1_case()
        baseline = run_case(case, duplicate_refund)
        ledger = CaseLedger()
        treated = run_case(case, ledger.wrap(duplicate_refund))

        self.assertEqual(len(baseline["final_state"]["refunds"]), 2)
        self.assertEqual(len(treated["final_state"]["refunds"]), 1)
        self.assertEqual(
            evaluate_case(case, treated)["metrics"]["unsafe_action_rate"], 0
        )
        self.assertTrue(ledger.guard_events)

    def test_guard_blocks_closure_before_refund_is_verified(self):
        case = t1_case()
        ledger = CaseLedger()

        def agent(session, call):
            if session["trigger_id"] == "V1":
                ticket_id = call(
                    "create_ticket",
                    user_id=session["user_id"],
                    order_ids=[session["order_id"]],
                    summary="refund",
                )
                call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
            return None

        run = run_case(case, ledger.wrap(agent))
        self.assertEqual(run["final_state"]["tickets"]["T1"]["status"], "open")
        self.assertTrue(ledger.guard_events)


class LedgerObligationTest(unittest.TestCase):
    def test_failure_and_final_delivery_track_different_obligations(self):
        for skip_failure in (False, True):
            with self.subTest(skip_failure=skip_failure):
                case = t1_case()
                case["visits"].append(
                    {**case["visits"][0], "id": "V3", "t": "D5 10:00"}
                )
                seen = {}

                def agent(session, call):
                    trigger_id = session["trigger_id"]
                    seen[trigger_id] = session["case_ledger"]
                    if trigger_id == "V3":
                        return "LEFT"

                    def model_call(tool, **args):
                        if (
                            skip_failure
                            and trigger_id == "F1"
                            and tool == "send_message_to_user"
                        ):
                            return {"ok": True}
                        return call(tool, **args)

                    return oracle(session, model_call)

                ledger = CaseLedger()
                run = run_case(case, ledger.wrap(agent))
                observed = seen["F1"]["failure_notification_obligations"][0]
                self.assertIsNone(observed["notified_at"])
                self.assertLess(observed["failed_at"], observed["observed_at"])
                before_final = seen["F2"]["refunds"][0]
                self.assertIsNone(before_final["final_notified_at"])
                settled = seen["V3"]
                failure = settled["failure_notification_obligations"][0]
                final = settled["refunds"][0]["final_notified_at"]
                self.assertIsNotNone(final)
                if skip_failure:
                    self.assertIsNone(failure["notified_at"])
                else:
                    self.assertLess(failure["notified_at"], final)
                    self.assertEqual(failure["notification_channel"], "chat")
                self.assertEqual(
                    len(run["final_state"]["outbox"]), 1 if skip_failure else 2
                )
                self.assertEqual(
                    run["final_state"]["tickets"]["T1"]["status"], "closed"
                )
                self.assertFalse(ledger.guard_events)

    def test_committed_timeout_recovers_refund_without_inventing_failure(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                case = t2_case()
                seen = []

                def agent(session, call):
                    seen.append(session["case_ledger"])
                    return t2_oracle(session, call)

                ledger = CaseLedger()
                run = run_case(case, ledger.wrap(agent), wake=wake)
                issue = next(
                    row
                    for row in run["trajectory"]
                    if row.get("tool") == "issue_refund"
                )
                self.assertEqual(issue["result"], {"error": "timeout"})
                self.assertTrue(issue["committed"])
                self.assertEqual(len(run["final_state"]["refunds"]), 1)
                self.assertTrue(evaluate_case(case, run)["metrics"]["case_resolution"])
                self.assertEqual(seen[1]["refunds"][0]["refund_id"], "R1")
                self.assertEqual(len(seen[1]["monitoring_obligations"]), 1)
                self.assertTrue(
                    all(
                        not snapshot["failure_notification_obligations"]
                        for snapshot in seen
                    )
                )
                self.assertFalse(ledger.guard_events)

    def test_cross_channel_revisit_reuses_original_ticket_and_logs_block(self):
        case = t9_case()
        ledger = CaseLedger()

        def agent(session, call):
            if session["trigger_id"] == "V2":
                self.assertEqual(session["channel"], "email")
                (ticket,) = session["case_ledger"]["tickets"]
                self.assertEqual(ticket["ticket_id"], "T1")
                result = call(
                    "create_ticket",
                    user_id=session["user_id"],
                    order_ids=ticket["order_ids"],
                    summary="Email follow-up",
                )
                self.assertEqual(result["error"], "case_ledger_blocked")
            return oracle(session, call)

        run = run_case(case, ledger.wrap(agent))
        self.assertEqual(list(run["final_state"]["tickets"]), ["T1"])
        self.assertEqual(
            evaluate_case(case, run)["metrics"]["duplicate_ticket_rate"], 0
        )
        self.assertEqual(
            [(event["tool"], event["trigger_id"]) for event in ledger.guard_events],
            [("create_ticket", "V2")],
        )


if __name__ == "__main__":
    unittest.main()
