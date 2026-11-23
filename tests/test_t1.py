import unittest

from case_lifecycle_bench.cases.t1 import (
    duplicate_refund,
    missed_followup,
    oracle,
    t1_case,
)
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_t1


class T1SmokeTest(unittest.TestCase):
    def test_oracle_resolves_case(self):
        case = t1_case()
        run = run_case(case, oracle)
        evaluation = evaluate_t1(case, run)
        self.assertTrue(evaluation["metrics"]["case_resolution"])
        self.assertEqual(evaluation["metrics"]["detection_rate"], 1)
        self.assertEqual(evaluation["metrics"]["unsafe_action_rate"], 0)
        self.assertTrue(evaluation["process"]["notified_after_success"])
        self.assertTrue(evaluation["process"]["closed_after_success"])

    def test_missed_followup_is_caught(self):
        case = t1_case()
        evaluation = evaluate_t1(case, run_case(case, missed_followup))
        self.assertFalse(evaluation["metrics"]["case_resolution"])
        self.assertFalse(evaluation["process"]["initial_followup_scheduled"])

    def test_duplicate_refund_is_caught_before_it_settles(self):
        case = t1_case()
        run = run_case(case, duplicate_refund)
        evaluation = evaluate_t1(case, run)
        self.assertFalse(evaluation["process"]["no_duplicate_refund"])
        self.assertEqual(evaluation["metrics"]["unsafe_action_rate"], 1)

    def test_push_wakes_only_for_notifying_events(self):
        case = t1_case()
        self.assertEqual(run_case(case, oracle), run_case(case, oracle, wake="push"))

        case["events"][0]["notify_agent"] = True
        for mode, expected in (("poll", []), ("push", ["E1"])):
            with self.subTest(mode=mode):
                run = run_case(case, oracle, wake=mode)
                event_wakes = [
                    record["trigger_id"]
                    for record in run["trajectory"]
                    if record["kind"] == "session_start"
                    and record["trigger_type"] == "system_event"
                ]
                self.assertEqual(event_wakes, expected)


if __name__ == "__main__":
    unittest.main()
