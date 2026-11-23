import unittest

from case_lifecycle_bench.cases.t9 import duplicate_ticket, oracle, t9_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case


class CrossChannelRevisitTest(unittest.TestCase):
    def test_oracle_reuses_chat_ticket_on_email_revisit(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                case = t9_case()
                run = run_case(case, oracle, wake=wake)
                result = evaluate_case(case, run)

                self.assertTrue(result["process"]["reused_original_ticket_on_revisit"])
                self.assertTrue(result["process"]["no_duplicate_ticket_on_revisit"])
                self.assertEqual(result["metrics"]["duplicate_ticket_rate"], 0)
                self.assertEqual(result["metrics"]["session_correctness"], 1)
                self.assertEqual(result["metrics"]["conversation_success"], 1)

    def test_opening_second_ticket_fails_the_revisit_stage(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                case = t9_case()
                result = evaluate_case(
                    case, run_case(case, duplicate_ticket, wake=wake)
                )

                self.assertFalse(result["process"]["reused_original_ticket_on_revisit"])
                self.assertFalse(result["process"]["no_duplicate_ticket_on_revisit"])
                self.assertEqual(result["metrics"]["duplicate_ticket_rate"], 1)
                revisit = next(
                    stage
                    for stage in result["stages"].values()
                    if stage["trigger_id"] == "V2"
                )
                self.assertFalse(revisit["passed"])

    def test_single_visit_has_no_duplicate_ticket_rate(self):
        case = t9_case()
        case["visits"] = case["visits"][:1]
        result = evaluate_case(case, run_case(case, oracle))

        self.assertIsNone(result["process"]["reused_original_ticket_on_revisit"])
        self.assertIsNone(result["process"]["no_duplicate_ticket_on_revisit"])
        self.assertIsNone(result["metrics"]["duplicate_ticket_rate"])
