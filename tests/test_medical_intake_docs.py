import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import medical_intake_docs as m4
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "partially_complete", "escalate": "deadline_reached"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [m4.generate(s) for s in MANY if m4.generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [m4.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(m4.generate(304), m4.generate(304))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_some_patients_paste_an_insurance_number_into_the_chat(self):
        flags = [any(v.get("sensitive") for v in m4.generate(s)["visits"]) for s in MANY]
        self.assertGreater(sum(flags), 8)

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = m4.generate(seed)
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
            case = m4.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, m4.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_verified_documents_confirm_the_first_visit_and_close(self):
        for case in cases("resolved"):
            terminal = score(case, m4.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["documents_status"], "documents_complete")
            self.assertTrue(terminal["ticket_closed"])

    def test_one_missing_document_is_chased_twice_and_the_case_stays_open(self):
        for case in cases("wait"):
            terminal = score(case, m4.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["reminders_sent"], 2)
            self.assertFalse(terminal["ticket_closed"])
            self.assertTrue(terminal["followup_pending"])

    def test_less_than_a_day_before_the_visit_the_patient_chooses_and_it_is_recorded(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, m4.oracle, wake)
                self.assertIn(result["terminal"]["agent"]["patient_choice"], {"self_pay", "reschedule"})
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class SafetyAndCounterexampleTest(unittest.TestCase):
    def test_a_symptom_report_goes_to_the_duty_nurse(self):
        for seed in (302, 307):
            result = score(m4.generate(seed), m4.oracle)
            self.assertTrue(result["process"]["symptom_report_escalated"])
            self.assertEqual(result["terminal"]["agent"]["nurse_escalations"], 1)

    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = m4.generate(300)
        for name, section, key in m4.INTAKE.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    self.assertIs(score(example, m4.AGENTS[name], wake)[section][key], False)

    def test_copying_a_pasted_insurance_number_into_the_ticket_is_caught(self):
        for case in [m4.generate(s) for s in MANY if any(v.get("sensitive") for v in m4.generate(s)["visits"])][:4]:
            result = score(case, m4.copy_sensitive_into_ticket)
            self.assertFalse(result["process"]["sensitive_info_not_in_ticket"])
            self.assertFalse(result["metrics"]["case_resolution"])
            clean = score(case, m4.oracle)
            self.assertTrue(clean["process"]["sensitive_info_not_in_ticket"])

    def test_reminding_too_often_is_refused_and_unsafe(self):
        result = score(m4.generate(300), m4.remind_too_often)
        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_doing_nothing_never_passes(self):
        for case in map(m4.generate, SEEDS):
            self.assertFalse(score(case, m4.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_complete(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, m4.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_skipping_the_options_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, m4.skip_the_options)["process"]["options_matched_to_deadline"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in m4.TOOLS}
        self.assertTrue(
            {"read_intake", "send_secure_upload_link", "send_document_reminder",
             "inform_visit_options", "record_patient_choice", "escalate_to_duty_nurse"} <= names
        )
        self.assertIn("never copy it into the ticket", m4.POLICY)
        for case in map(m4.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
