import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import claim_payout as i3
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "stuck"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant, **wanted):
    found = []
    for seed in MANY:
        case = i3.generate(seed)
        if case["terminal_variant"] != variant:
            continue
        letter = case["init"]["letter"]
        if wanted.get("third") is not None and bool(letter) != wanted["third"]:
            continue
        if wanted.get("letter_missing") and not (letter and letter["state"] == "missing"):
            continue
        found.append(case)
    return found


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [i3.generate(seed) for seed in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(i3.generate(304), i3.generate(304))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_some_payouts_time_out_after_committing(self):
        flags = [bool(i3.generate(s).get("faults")) for s in MANY]
        self.assertGreater(sum(flags), 10)
        self.assertLess(sum(flags), 30)

    def test_events_notify_and_a_revisit_is_conditional_inside_the_horizon(self):
        for seed in MANY:
            case = i3.generate(seed)
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
            case = i3.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, i3.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_a_repaid_payout_to_a_new_account_is_told_then_closed(self):
        for case in cases("resolved"):
            terminal = score(case, i3.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["payout_status"], "paid")
            self.assertEqual(terminal["payments_created"], 2)
            self.assertTrue(terminal["ticket_closed"])

    def test_a_third_party_needs_a_letter_on_file_so_without_one_the_agent_waits(self):
        for case in cases("wait", letter_missing=True):
            terminal = score(case, i3.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["payout_status"], "payout_failed")
            self.assertEqual(terminal["payments_created"], 1)
            self.assertFalse(terminal["ticket_closed"])
            self.assertTrue(terminal["followup_pending"])

    def test_a_payout_stuck_past_three_days_goes_to_finance_once(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, i3.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])
                self.assertTrue(result["process"]["customer_told_of_escalation"])


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = i3.generate(300)
        for name, section, key in i3.CLAIM_PAYOUT.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, i3.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    if section == "process":
                        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
                        self.assertFalse(result["metrics"]["case_resolution"])

    def test_doing_nothing_never_passes(self):
        for case in map(i3.generate, SEEDS):
            self.assertFalse(score(case, i3.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_paid(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, i3.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_never_escalating_fails_on_a_stuck_payout(self):
        for case in cases("escalate"):
            result = score(case, i3.never_escalate)
            self.assertFalse(result["process"]["escalation_matches_overdue"])


class PaymentRulesTest(unittest.TestCase):
    def test_an_authorisation_letter_must_name_the_payee(self):
        case = cases("resolved", third=True)[0]
        env = i3.PayoutEnvironment(case, "poll")
        script = case["visits"][0]["script"]
        env.contact_scripts["C1"] = script
        contact = {"contact_id": "C1", "customer_id": env.user_id, "identity_verified": True}
        record = env._record()
        env.state["work_items"][env.subject_id] = {
            **record,
            "letter": {"payee_name": "别家修理厂", "state": "on_file"},
        }

        refused = env._contact_tool("register_payout_account", contact, {
            "account_number": script["account_number"], "holder_name": script["holder_name"],
        })[0]

        self.assertEqual(refused, {"error": "authorization_missing"})


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in i3.TOOLS}
        self.assertTrue(
            {"read_payout", "pay_claim", "register_payout_account", "retry_payout", "escalate_to_human"}
            <= names
        )
        self.assertIn("3 calendar days", i3.POLICY)
        for case in map(i3.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
