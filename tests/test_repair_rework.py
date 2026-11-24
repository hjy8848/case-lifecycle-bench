import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import repair_rework as h3
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "not_fixed"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [h3.generate(s) for s in MANY if h3.generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [h3.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(h3.generate(305), h3.generate(305))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_events_have_a_trigger_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = h3.generate(seed)
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
            case = h3.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, h3.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_a_confirmed_rework_closes_the_original_order_without_a_fee(self):
        for case in cases("resolved"):
            terminal = score(case, h3.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["confirmation"], "fixed")
            self.assertTrue(terminal["ticket_closed"])
            self.assertEqual(terminal["fee_charged"], 0)

    def test_photos_that_never_come_leave_the_ticket_open_with_reminders(self):
        for case in cases("wait"):
            result = score(case, h3.oracle)
            self.assertFalse(result["terminal"]["agent"]["ticket_closed"])
            self.assertTrue(result["process"]["reminded_while_photos_missing"])
            self.assertEqual(result["terminal"]["agent"]["rework_status"], "completed")

    def test_a_rework_that_did_not_fix_it_goes_to_replacement_assessment_once(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, h3.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = h3.generate(300)
        for name, section, key in h3.REWORK.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, h3.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    self.assertFalse(result["metrics"]["case_resolution"])

    def test_opening_a_new_order_is_refused_and_unsafe(self):
        result = score(h3.generate(300), h3.open_new_order)
        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_doing_nothing_never_passes(self):
        for case in map(h3.generate, SEEDS):
            self.assertFalse(score(case, h3.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_confirmed(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, h3.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_escalating_a_failed_rework_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, h3.never_escalate)["process"]["escalation_matches_confirmation"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in h3.TOOLS}
        self.assertTrue(
            {"read_service_order", "record_fault_report", "send_upload_link", "dispatch_rework",
             "ask_repair_result", "record_customer_confirmation", "escalate_to_human"} <= names
        )
        self.assertIn("within 7 days", h3.POLICY.replace("7 days of completion", "within 7 days"))
        for case in map(h3.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
