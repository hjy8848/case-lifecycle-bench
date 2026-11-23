import unittest

from case_lifecycle_bench.cases import t1, t3
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.eval_checks import duty_detection_times
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.smoke_summary import _metrics


class SmokeDelayTest(unittest.TestCase):
    def test_carrier_event_delay_includes_time_until_next_poll(self):
        case = t3.t3_case()
        poll = run_case(case, t3.oracle, wake="poll")
        push = run_case(case, t3.oracle, wake="push")
        poll_duty = duty_detection_times(poll["trajectory"])[0]
        push_duty = duty_detection_times(push["trajectory"])[0]
        self.assertEqual(poll_duty["event_to_detection_hours"], 18)
        self.assertEqual(poll_duty["opportunity_to_detection_hours"], 0)
        self.assertEqual(push_duty["event_to_detection_hours"], 0)

    def test_refund_without_a_matching_status_read_stays_undetected(self):
        run = run_case(t1.t1_case(), t1.oracle, wake="poll")
        trajectory = [
            row
            for row in run["trajectory"]
            if row.get("tool") not in {"list_refunds", "get_refund"}
        ]
        duty = duty_detection_times(trajectory)[0]
        self.assertIsNone(duty["query_seq"])
        self.assertIsNone(duty["event_to_detection_hours"])

    def test_user_revisit_count_excludes_initial_contact(self):
        case = t1.t1_case()
        for wake, expected in (("poll", 1), ("push", 0)):
            with self.subTest(wake=wake):
                run = run_case(case, t1.oracle, wake=wake)
                metrics = _metrics({"run": run, "evaluation": evaluate_case(case, run)})
                self.assertEqual(metrics["user_revisits"], expected)
