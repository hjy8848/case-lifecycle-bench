import unittest

from case_lifecycle_bench.cases.t1 import duplicate_refund, oracle, t1_case
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
                    summary="退款",
                )
                call("close_ticket", ticket_id=ticket_id, resolution_code="refunded")
            return None

        run = run_case(case, ledger.wrap(agent))
        self.assertEqual(run["final_state"]["tickets"]["T1"]["status"], "open")
        self.assertTrue(ledger.guard_events)


if __name__ == "__main__":
    unittest.main()
