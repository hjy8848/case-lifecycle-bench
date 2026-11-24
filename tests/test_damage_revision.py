import unittest
from collections import Counter
from datetime import datetime

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import damage_revision as i4
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "appeal"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant, direction=None):
    found = []
    for seed in MANY:
        case = i4.generate(seed)
        if case["terminal_variant"] != variant:
            continue
        if direction and case["events"][0]["direction"] != direction:
            continue
        found.append(case)
    return found


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [i4.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(i4.generate(301), i4.generate(301))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_resolved_instances_revise_both_ways_and_the_others_revise_down(self):
        self.assertEqual(
            {c["events"][0]["direction"] for c in cases("resolved")}, {"up", "down"}
        )
        for variant in ("wait", "escalate"):
            self.assertEqual({c["events"][0]["direction"] for c in cases(variant)}, {"down"})

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = i4.generate(seed)
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
            case = i4.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, i4.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_the_claim_proceeds_on_the_latest_amount_and_closes(self):
        for case in cases("resolved"):
            terminal = score(case, i4.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["claim_status"], "proceeding")
            self.assertEqual(terminal["settled_amount"], case["events"][0]["amount"])
            self.assertTrue(terminal["ticket_closed"])

    def test_a_customer_who_needs_time_is_followed_up_at_their_time_not_pressed(self):
        for case in cases("wait"):
            result = score(case, i4.oracle)
            self.assertTrue(result["process"]["followup_at_agreed_time"])
            self.assertTrue(result["process"]["not_pressing_a_customer_who_needs_time"])
            self.assertFalse(result["terminal"]["agent"]["ticket_closed"])

    def test_an_appeal_goes_to_the_appeals_team_once(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, i4.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class SettlementRulesTest(unittest.TestCase):
    def test_a_downward_revision_cannot_proceed_without_agreement_to_the_new_version(self):
        case = cases("wait")[0]
        env = i4.RevisionEnvironment(case, "poll")
        env._apply(env._event_changes(case["events"][0]))
        amount = case["events"][0]["amount"]

        refused = env._execute("proceed_with_assessment", {"version": "A2", "amount": amount}, "S1")[0]
        stale = env._execute("proceed_with_assessment", {"version": "A2", "amount": 1}, "S1")[0]

        self.assertEqual(refused, {"error": "missing_consent"})
        self.assertEqual(stale, {"error": "stale_amount"})

    def test_an_upward_revision_needs_no_new_agreement(self):
        case = cases("resolved", "up")[0]
        env = i4.RevisionEnvironment(case, "poll")
        env._apply(env._event_changes(case["events"][0]))

        accepted = env._execute(
            "proceed_with_assessment", {"version": "A2", "amount": case["events"][0]["amount"]}, "S1"
        )[0]

        self.assertEqual(accepted, {"ok": True})


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = i4.generate(300)
        for name, section, key in i4.DAMAGE_REVISION.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, i4.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    self.assertFalse(result["metrics"]["case_resolution"])

    def test_doing_nothing_never_passes(self):
        for case in map(i4.generate, SEEDS):
            self.assertFalse(score(case, i4.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_proceeding(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, i4.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_transferring_an_appeal_is_caught(self):
        for case in cases("escalate"):
            result = score(case, i4.never_transfer)
            self.assertFalse(result["process"]["appeal_matches_state"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in i4.TOOLS}
        self.assertTrue(
            {"read_assessment", "inform_assessment_revision", "record_customer_consent",
             "proceed_with_assessment", "escalate_to_human"} <= names
        )
        self.assertIn("7-day appeal channel", i4.POLICY)
        for case in map(i4.generate, SEEDS):
            self.assertTrue(case["init"]["script"]["default_contact"] is not None)
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
