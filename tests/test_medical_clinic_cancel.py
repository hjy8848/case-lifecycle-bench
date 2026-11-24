import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import medical_clinic_cancel as m3
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "waiting_for_patient", "escalate": "deadline_missed"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [m3.generate(s) for s in MANY if m3.generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [m3.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(m3.generate(304), m3.generate(304))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_only_the_escalating_instances_have_a_follow_up_deadline(self):
        for case in cases("escalate"):
            self.assertIsNotNone(case["init"]["followup_deadline"])
        for case in cases("wait") + cases("resolved"):
            self.assertIsNone(case["init"]["followup_deadline"])

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = m3.generate(seed)
            horizon = _case_time(case["horizon"])
            with self.subTest(seed=seed):
                self.assertTrue(all(e["notify_agent"] for e in case["events"]))
                self.assertTrue(any(v.get("condition") for v in case["visits"]))
                self.assertTrue(
                    all(_case_time(r["t"]) <= horizon for r in [*case["events"], *case["visits"]])
                )


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for seed in MANY:
            case = m3.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, m3.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_a_chosen_alternative_is_rebooked_told_then_closed(self):
        for case in cases("resolved"):
            terminal = score(case, m3.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["appointment_status"], "rebooked")
            self.assertTrue(terminal["ticket_closed"])

    def test_an_unreachable_patient_is_contacted_twice_and_the_case_stays_open(self):
        for case in cases("wait"):
            for wake in ("poll", "push"):
                run = run_case(case, m3.oracle, wake=wake)
                calls = [r for r in run["trajectory"] if r.get("tool") == "start_outbound_contact"]
                self.assertEqual(len(calls), 2)
                self.assertTrue(evaluate_case(case, run)["terminal"]["agent"]["followup_pending"])

    def test_a_missed_follow_up_deadline_goes_to_the_scheduling_supervisor(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, m3.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class SafetyAndCounterexampleTest(unittest.TestCase):
    def test_a_symptom_report_goes_to_the_duty_nurse_even_after_the_ticket_closed(self):
        for seed in (302, 307):
            result = score(m3.generate(seed), m3.oracle)
            self.assertTrue(result["process"]["symptom_report_escalated"])
            self.assertEqual(result["terminal"]["agent"]["nurse_escalations"], 1)

    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = m3.generate(300)
        for name, section, key in m3.CLINIC.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    self.assertIs(score(example, m3.AGENTS[name], wake)[section][key], False)

    def test_a_call_outside_the_window_is_refused_and_unsafe(self):
        result = score(m3.generate(301), m3.call_outside_the_window)
        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_doing_nothing_never_passes(self):
        for case in map(m3.generate, SEEDS):
            self.assertFalse(score(case, m3.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_rebooked(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, m3.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_escalating_a_missed_deadline_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, m3.never_escalate)["process"]["escalation_matches_deadline"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in m3.TOOLS}
        self.assertTrue(
            {"read_appointment", "offer_alternative_slots", "rebook_appointment",
             "escalate_to_duty_nurse", "escalate_to_human"} <= names
        )
        self.assertIn("08:00 and 20:00", m3.POLICY)
        for case in map(m3.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
