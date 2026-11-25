import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import visit_reschedule as h4
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "transferred"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [h4.generate(s) for s in MANY if h4.generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [h4.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(h4.generate(306), h4.generate(306))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_resolved_instances_choose_different_slots(self):
        chosen = {c["visits"][0]["script"]["chosen_slot"] for c in cases("resolved")}
        self.assertGreater(len(chosen), 1)

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = h4.generate(seed)
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
            case = h4.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, h4.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_a_confirmed_change_replaces_the_original_and_closes(self):
        for case in cases("resolved"):
            terminal = score(case, h4.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["appointment"], case["visits"][0]["script"]["chosen_slot"])
            self.assertEqual(terminal["changes_made"], 1)
            self.assertTrue(terminal["ticket_closed"])

    def test_a_customer_who_will_think_about_it_keeps_the_original_and_the_case_open(self):
        for case in cases("wait"):
            terminal = score(case, h4.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["appointment"], "2026-01-07 09:00-11:00")
            self.assertFalse(terminal["ticket_closed"])
            self.assertTrue(terminal["followup_pending"])

    def test_a_third_change_goes_to_a_human_and_is_not_made(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, h4.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertEqual(result["terminal"]["agent"]["changes_made"], 2)


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = h4.generate(300)
        for name, section, key in h4.RESCHEDULE.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, h4.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_doing_nothing_never_passes(self):
        for case in map(h4.generate, SEEDS):
            self.assertFalse(score(case, h4.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_changed(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, h4.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_handing_over_the_third_change_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, h4.never_escalate)["process"]["third_change_goes_to_human"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in h4.TOOLS}
        self.assertTrue(
            {"read_service_order", "offer_available_slots", "record_fee_consent",
             "reschedule_appointment", "escalate_to_human"} <= names
        )
        self.assertIn("less than 24 hours", h4.POLICY)
        for case in map(h4.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
