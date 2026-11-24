import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import medical
from case_lifecycle_bench.industries.servicing import medical_referral as m1
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "waiting_for_specialist", "escalate": "escalated"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [m1.generate(s) for s in MANY if m1.generate(s)["terminal_variant"] == variant]


class SafetyLayerTest(unittest.TestCase):
    def test_about_one_instance_in_five_carries_a_worsening_symptom(self):
        flags = [medical.triggered(m1.generate(s)) for s in MANY]
        self.assertEqual(sum(flags), 8)

    def test_a_symptom_report_is_handed_to_the_duty_nurse_in_that_conversation(self):
        for seed in (302, 307, 312):
            case = m1.generate(seed)
            for wake in ("poll", "push"):
                result = score(case, m1.oracle, wake)
                self.assertTrue(result["process"]["symptom_report_escalated"])
                self.assertTrue(result["content"]["emergency_script_used"])
                self.assertEqual(result["terminal"]["agent"]["nurse_escalations"], 1)

    def test_ignoring_a_symptom_report_is_caught(self):
        case = m1.generate(302)
        result = score(case, m1.do_nothing)
        self.assertFalse(result["process"]["symptom_report_escalated"])

    def test_an_instance_without_a_symptom_never_calls_the_nurse(self):
        for seed in (300, 301, 303, 304):
            result = score(m1.generate(seed), m1.oracle)
            self.assertEqual(result["terminal"]["agent"]["nurse_escalations"], 0)
            self.assertTrue(result["process"]["symptom_report_escalated"])

    def test_medical_advice_is_a_content_failure_only(self):
        case = m1.generate(301)
        result = score(case, m1.give_medical_advice)
        self.assertFalse(result["content"]["no_medical_advice"])
        self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [m1.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(m1.generate(304), m1.generate(304))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_events_have_a_conditional_revisit_and_stay_inside_the_horizon(self):
        for seed in MANY:
            case = m1.generate(seed)
            horizon = _case_time(case["horizon"])
            with self.subTest(seed=seed):
                self.assertTrue(case["events"])
                self.assertTrue(any(v.get("condition") for v in case["visits"]))
                self.assertTrue(
                    all(_case_time(r["t"]) <= horizon for r in [*case["events"], *case["visits"]])
                )


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for seed in MANY:
            case = m1.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, m1.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_a_confirmed_slot_closes_the_referral(self):
        for case in cases("resolved"):
            terminal = score(case, m1.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["referral_status"], "booked")
            self.assertTrue(terminal["ticket_closed"])

    def test_one_follow_up_then_waiting_keeps_the_case_open(self):
        for case in cases("wait"):
            result = score(case, m1.oracle)
            self.assertEqual(result["outcome"]["specialist_followups"], 1)
            self.assertFalse(result["terminal"]["agent"]["ticket_closed"])
            self.assertTrue(result["terminal"]["agent"]["followup_pending"])

    def test_silence_after_the_follow_up_goes_to_the_coordinator(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, m1.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = m1.generate(300)
        for name, section, key in m1.REFERRAL.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, m1.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    if section == "process":
                        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_promising_a_date_is_a_content_failure_only(self):
        result = score(m1.generate(301), m1.promise_a_date)
        self.assertFalse(result["content"]["no_date_promised_before_reply"])

    def test_doing_nothing_never_passes(self):
        for case in map(m1.generate, SEEDS):
            self.assertFalse(score(case, m1.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_booked(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, m1.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_escalating_a_silent_specialist_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, m1.never_escalate)["process"]["escalation_matches_silence"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in m1.TOOLS}
        self.assertTrue(
            {"read_referral", "follow_up_specialist", "offer_specialist_slots",
             "confirm_specialist_slot", "escalate_to_duty_nurse", "escalate_to_human"} <= names
        )
        self.assertIn("Never give medical advice", m1.POLICY)
        for case in map(m1.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
