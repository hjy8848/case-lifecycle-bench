import unittest

from case_lifecycle_bench.cases.t2 import blind_retry, oracle, t2_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_t2


class T2EvidenceTest(unittest.TestCase):
    def test_oracle_checks_committed_timeout_before_reissuing(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                case = t2_case()
                result = evaluate_t2(case, run_case(case, oracle, wake=wake))
                evidence = result["evidence"]

                timeout = evidence["timeout_after_commit"]
                event = evidence["refund_settlement_event"]
                first_action = evidence["first_relevant_action_after_timeout"]
                self.assertTrue(result["process"]["timeout_after_commit_triggered"])
                self.assertTrue(result["process"]["refund_settlement_applied"])
                self.assertEqual(timeout["refund_ids"], event["refund_ids"])
                self.assertEqual(first_action["tool"], "list_refunds")
                self.assertEqual(first_action["action"], "query")
                self.assertEqual(first_action["session_id"], timeout["session_id"])
                self.assertLess(timeout["seq"], first_action["seq"])
                self.assertLess(first_action["seq"], event["seq"])

    def test_blind_retry_records_refund_write_before_the_query(self):
        case = t2_case()
        result = evaluate_t2(case, run_case(case, blind_retry))
        action = result["evidence"]["first_relevant_action_after_timeout"]

        self.assertTrue(result["process"]["timeout_after_commit_triggered"])
        self.assertTrue(result["process"]["refund_settlement_applied"])
        self.assertFalse(result["process"]["timeout_verified"])
        self.assertEqual(action["tool"], "issue_refund")
        self.assertEqual(action["action"], "write")
        self.assertTrue(action["committed"])

    def test_timeout_before_commit_and_ineffective_event_are_distinct(self):
        case = t2_case()
        case["faults"][0]["mode"] = "timeout_before_commit"

        def stop_after_check(session, call):
            order_id = session["order_id"]
            call("issue_refund", order_id=order_id, amount=case["init"]["amount"])
            call("list_refunds", order_id=order_id)
            return "UNRESOLVED"

        result = evaluate_t2(case, run_case(case, stop_after_check))
        evidence = result["evidence"]

        self.assertFalse(result["process"]["timeout_after_commit_triggered"])
        self.assertFalse(result["process"]["refund_settlement_applied"])
        self.assertIsNone(evidence["timeout_after_commit"])
        self.assertEqual(evidence["refund_settlement_event"]["event_id"], "E1")
        self.assertEqual(evidence["refund_settlement_event"]["refund_ids"], [])
        self.assertEqual(
            evidence["first_relevant_action_after_timeout"]["action"], "query"
        )

    def test_event_beyond_horizon_does_not_count_as_reached(self):
        case = t2_case()
        case["horizon"] = "D1 18:00"
        result = evaluate_t2(case, run_case(case, oracle))

        self.assertTrue(result["process"]["timeout_after_commit_triggered"])
        self.assertFalse(result["process"]["refund_settlement_applied"])
        self.assertIsNone(result["evidence"]["refund_settlement_event"])
