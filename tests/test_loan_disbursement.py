import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import loan_disbursement as l3
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "stuck"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant):
    return [l3.generate(s) for s in MANY if l3.generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [l3.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(l3.generate(302), l3.generate(302))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_some_disbursements_time_out_after_committing(self):
        flags = [bool(l3.generate(s).get("faults")) for s in MANY]
        self.assertGreater(sum(flags), 10)
        self.assertLess(sum(flags), 30)

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = l3.generate(seed)
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
            case = l3.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, l3.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_a_resubmitted_disbursement_to_a_new_account_is_told_then_closed(self):
        for case in cases("resolved"):
            terminal = score(case, l3.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["loan_status"], "disbursed")
            self.assertEqual(terminal["disbursements_created"], 2)
            self.assertEqual(terminal["receiving_holder"], case["init"]["borrower_name"])
            self.assertTrue(terminal["ticket_closed"])

    def test_waiting_keeps_the_ticket_open_with_a_check_ahead(self):
        for case in cases("wait"):
            terminal = score(case, l3.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["loan_status"], "disbursing")
            self.assertFalse(terminal["ticket_closed"])
            self.assertTrue(terminal["followup_pending"])

    def test_a_disbursement_stuck_past_two_days_goes_to_operations_once(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, l3.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = l3.generate(300)
        for name, section, key in l3.DISBURSEMENT.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, l3.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    if section == "process":
                        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_changing_the_account_without_verifying_is_refused_and_unsafe(self):
        for case in cases("resolved")[:3]:
            result = score(case, l3.change_without_verifying)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_doing_nothing_never_passes(self):
        for case in map(l3.generate, SEEDS):
            self.assertFalse(score(case, l3.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_disbursed(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, l3.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_never_escalating_fails_on_a_stuck_disbursement(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, l3.never_escalate)["process"]["escalation_matches_overdue"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in l3.TOOLS}
        self.assertTrue(
            {"read_disbursement", "submit_disbursement", "confirm_new_account",
             "resubmit_disbursement", "escalate_to_human"} <= names
        )
        self.assertIn("2 calendar days", l3.POLICY)
        for case in map(l3.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
