import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import visit_noshow as h2
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "second_miss"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [h2.generate(s) for s in MANY if h2.generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [h2.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(h2.generate(303), h2.generate(303))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_the_first_miss_is_a_leave_or_a_missing_check_in_at_random(self):
        ops = {h2.generate(s)["events"][0]["op"] for s in MANY}
        self.assertEqual(ops, {"tech_unavailable", "no_show"})

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = h2.generate(seed)
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
            case = h2.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, h2.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_a_completed_visit_is_verified_by_check_in_told_then_closed(self):
        for case in cases("resolved"):
            terminal = score(case, h2.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["visit_status"], "completed")
            self.assertTrue(terminal["ticket_closed"])
            self.assertEqual(terminal["voucher_total"], 0)

    def test_a_slot_after_the_deadline_leaves_the_case_open_with_a_check_ahead(self):
        for case in cases("wait"):
            terminal = score(case, h2.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["visit_status"], "rescheduled")
            self.assertFalse(terminal["ticket_closed"])
            self.assertTrue(terminal["followup_pending"])

    def test_a_second_miss_goes_to_the_supervisor_with_the_standard_voucher(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, h2.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertEqual(result["terminal"]["agent"]["voucher_total"], 50)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = h2.generate(300)
        for name, section, key in h2.NOSHOW.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, h2.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    if section == "process":
                        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_doing_nothing_never_passes(self):
        for case in map(h2.generate, SEEDS):
            self.assertFalse(score(case, h2.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_completed(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, h2.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_escalating_the_second_miss_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, h2.never_escalate)["process"]["escalation_matches_second_miss"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in h2.TOOLS}
        self.assertTrue(
            {"read_service_order", "offer_available_slots", "reschedule_appointment",
             "issue_compensation_voucher", "escalate_to_human"} <= names
        )
        self.assertIn("check-in record", h2.POLICY)
        for case in map(h2.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
