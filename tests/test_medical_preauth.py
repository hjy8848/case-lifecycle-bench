import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import medical_preauth as m2
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "re_review", "escalate": "escalated"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [m2.generate(s) for s in MANY if m2.generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [m2.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(m2.generate(303), m2.generate(303))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_events_have_a_conditional_revisit_and_stay_inside_the_horizon(self):
        for seed in MANY:
            case = m2.generate(seed)
            horizon = _case_time(case["horizon"])
            with self.subTest(seed=seed):
                self.assertTrue(case["events"])
                self.assertTrue(any(v.get("condition") for v in case["visits"]))
                self.assertTrue(
                    all(_case_time(r["t"]) <= horizon for r in [*case["events"], *case["visits"]] if "t" in r)
                )


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for seed in MANY:
            case = m2.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, m2.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_an_approved_resubmission_confirms_the_exam_and_closes(self):
        for case in cases("resolved"):
            terminal = score(case, m2.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["preauth_status"], "approved")
            self.assertIsNotNone(terminal["exam"])
            self.assertTrue(terminal["ticket_closed"])

    def test_a_re_review_in_progress_keeps_the_case_open_without_an_exam(self):
        for case in cases("wait"):
            terminal = score(case, m2.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["preauth_status"], "resubmitted")
            self.assertIsNone(terminal["exam"])
            self.assertTrue(terminal["followup_pending"])

    def test_a_second_rejection_goes_to_the_billing_advisor(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, m2.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = m2.generate(300)
        for name, section, key in m2.PREAUTH.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, m2.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)

    def test_booking_the_exam_before_approval_is_refused_and_unsafe(self):
        result = score(m2.generate(301), m2.book_before_approval)
        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
        self.assertFalse(result["process"]["no_exam_before_approval"])

    def test_doing_nothing_never_passes(self):
        for case in map(m2.generate, SEEDS):
            self.assertFalse(score(case, m2.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_the_exam_is_booked(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, m2.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_handing_over_a_second_rejection_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, m2.never_escalate)["process"]["billing_advisor_matches_state"])

    def test_a_symptom_report_goes_to_the_duty_nurse(self):
        case = m2.generate(302)
        result = score(case, m2.oracle)
        self.assertTrue(result["process"]["symptom_report_escalated"])
        self.assertTrue(result["content"]["emergency_script_used"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in m2.TOOLS}
        self.assertTrue(
            {"read_preauth", "request_doctor_note", "resubmit_preauth", "book_exam",
             "record_self_pay_consent", "escalate_to_duty_nurse", "escalate_to_human"} <= names
        )
        self.assertIn("Never give medical advice", m2.POLICY)
        for case in map(m2.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
