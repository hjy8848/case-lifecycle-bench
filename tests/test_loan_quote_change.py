import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import loan_quote_change as l4
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "complaint"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant, response=None):
    found = []
    for seed in MANY:
        case = l4.generate(seed)
        if case["terminal_variant"] != variant:
            continue
        if response and case["init"]["script"]["contacts"][0]["response"] != response:
            continue
        found.append(case)
    return found


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [l4.generate(s) for s in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(l4.generate(304), l4.generate(304))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_resolved_instances_cover_agreement_and_rejection(self):
        responses = {c["init"]["script"]["contacts"][0]["response"] for c in cases("resolved")}
        self.assertEqual(responses, {"accept", "reject"})

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = l4.generate(seed)
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
            case = l4.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, l4.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_agreement_signs_the_new_version_and_rejection_withdraws(self):
        for case in cases("resolved", "accept"):
            terminal = score(case, l4.oracle)["terminal"]["agent"]
            self.assertEqual((terminal["application_status"], terminal["signed_version"]), ("signed", "Q2"))
            self.assertTrue(terminal["ticket_closed"])
        for case in cases("resolved", "reject"):
            terminal = score(case, l4.oracle)["terminal"]["agent"]
            self.assertEqual((terminal["application_status"], terminal["signed_version"]), ("withdrawn", None))
            self.assertTrue(terminal["ticket_closed"])

    def test_a_customer_who_needs_time_is_followed_up_at_their_time(self):
        for case in cases("wait"):
            result = score(case, l4.oracle)
            self.assertTrue(result["process"]["followup_at_agreed_time"])
            self.assertTrue(result["process"]["not_pressing_a_customer_who_needs_time"])
            self.assertFalse(result["terminal"]["agent"]["ticket_closed"])

    def test_a_misled_complaint_goes_to_a_specialist_once(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, l4.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])


class SigningRulesTest(unittest.TestCase):
    def test_signing_needs_agreement_to_the_new_version_and_withdrawal_needs_a_rejection(self):
        case = cases("wait")[0]
        env = l4.QuoteEnvironment(case, "poll")
        env._apply(env._event_changes(case["events"][0]))
        env.t = max(env.t, __import__("datetime").datetime.fromisoformat(env.state["work_items"][env.subject_id]["review_ends_at"]))

        self.assertEqual(env._domain_execute("sign_contract", {"version": "Q2"}, "S1")[0], {"error": "missing_agreement"})
        self.assertEqual(env._domain_execute("sign_contract", {"version": "Q1"}, "S1")[0], {"error": "stale_version"})
        self.assertEqual(env._domain_execute("withdraw_application", {"note": "x"}, "S1")[0], {"error": "no_rejection"})


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = l4.generate(300)
        for name, section, key in l4.QUOTE_CHANGE.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, l4.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    self.assertFalse(result["metrics"]["case_resolution"])

    def test_doing_nothing_never_passes(self):
        for case in map(l4.generate, SEEDS):
            self.assertFalse(score(case, l4.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_final(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, l4.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_not_transferring_a_complaint_is_caught(self):
        for case in cases("escalate"):
            self.assertFalse(score(case, l4.never_transfer)["process"]["complaint_matches_state"])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in l4.TOOLS}
        self.assertTrue(
            {"read_quote", "inform_quote_change", "record_customer_agreement", "sign_contract",
             "withdraw_application", "escalate_to_human"} <= names
        )
        self.assertIn("Never sign on an older version", l4.POLICY)
        for case in map(l4.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
