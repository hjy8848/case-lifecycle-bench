import unittest
from collections import Counter

from case_lifecycle_bench.cases import t1, t2, t9
from case_lifecycle_bench.cases.refund_variants import (
    generate_t1_case,
    generate_t2_case,
    generate_t9_case,
)
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.lifecycle import _case_time

SEEDS = range(300, 310)
MANY = range(300, 340)
FAMILY = {
    "t1": (generate_t1_case, t1.oracle, "refund_silent_failure"),
    "t2": (generate_t2_case, t2.oracle, "refund_timeout_committed"),
    "t9": (generate_t9_case, t9.oracle, "cross_channel_revisit"),
}
BUSINESS = {
    "t1": {"resolved": "resolved", "wait": "in_progress", "escalate": "payment_failed"},
    "t2": {"resolved": "resolved", "wait": "in_progress", "escalate": "stuck"},
    "t9": {"resolved": "resolved", "wait": "in_progress", "escalate": "in_progress"},
}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(key, variant):
    generate = FAMILY[key][0]
    return [generate(s) for s in MANY if generate(s)["terminal_variant"] == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_with_mixed_ids_and_amounts(self):
        for key, (generate, _, template) in FAMILY.items():
            built = [generate(s) for s in SEEDS]
            with self.subTest(template=template):
                self.assertEqual(
                    Counter(c["terminal_variant"] for c in built),
                    {"resolved": 5, "wait": 3, "escalate": 2},
                )
                self.assertEqual(len({c["case_id"] for c in built}), 10)
                self.assertEqual(generate(304), generate(304))
                self.assertGreater(len({c["init"]["order"][:3] for c in built}), 1)
                self.assertGreater(len({c["init"]["amount"] for c in built}), 5)
                self.assertTrue(all(c["template"] == template for c in built))

    def test_every_instance_has_a_conditional_revisit_and_stays_inside_the_horizon(self):
        for key, (generate, _, _) in FAMILY.items():
            for seed in MANY:
                case = generate(seed)
                horizon = _case_time(case["horizon"])
                with self.subTest(template=key, seed=seed):
                    self.assertTrue(case["events"])
                    self.assertTrue(any(v.get("condition") for v in case["visits"]))
                    self.assertTrue(
                        all(
                            _case_time(r["t"]) <= horizon
                            for r in [*case["events"], *case["visits"]]
                            if "t" in r
                        )
                    )


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for key, (generate, oracle, _) in FAMILY.items():
            for seed in MANY:
                case = generate(seed)
                for wake in ("poll", "push"):
                    with self.subTest(template=key, seed=seed, wake=wake):
                        result = score(case, oracle, wake)

                        self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                        self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                        self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                        self.assertEqual(result["metrics"]["interface_errors"], 0)
                        self.assertEqual(
                            result["business_outcome"], BUSINESS[key][case["terminal_variant"]]
                        )

    def test_a_settled_refund_is_notified_then_the_one_ticket_is_closed(self):
        for key in FAMILY:
            for case in cases(key, "resolved"):
                terminal = score(case, FAMILY[key][1])["terminal"]["agent"]
                self.assertTrue(terminal["refund_succeeded"])
                self.assertTrue(terminal["ticket_closed"])
                self.assertEqual(terminal["case_tickets"], 1)
                self.assertEqual(terminal["handoffs"], 0)

    def test_a_refund_still_processing_keeps_the_case_open_with_a_check_ahead(self):
        for key in ("t1", "t2", "t9"):
            for case in cases(key, "wait"):
                terminal = score(case, FAMILY[key][1])["terminal"]["agent"]
                self.assertFalse(terminal["ticket_closed"])
                self.assertTrue(terminal["followup_pending"])
                self.assertEqual(terminal["handoffs"], 0)
                self.assertEqual(terminal["refunds_created"], 1)

    def test_the_escalating_variants_hand_over_once_and_do_not_close(self):
        for key in FAMILY:
            for case in cases(key, "escalate"):
                for wake in ("poll", "push"):
                    with self.subTest(template=key, wake=wake):
                        terminal = score(case, FAMILY[key][1], wake)["terminal"]["agent"]
                        self.assertEqual(terminal["handoffs"], 1)
                        self.assertFalse(terminal["ticket_closed"])
                        self.assertEqual(terminal["case_tickets"], 1)

    def test_the_timeout_instances_never_issue_a_second_refund(self):
        for case in [c for k in ("resolved", "wait", "escalate") for c in cases("t2", k)]:
            self.assertEqual(score(case, t2.oracle)["terminal"]["agent"]["refunds_created"], 1)


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_is_caught_on_the_example(self):
        example = generate_t1_case(300)
        for agent, key in (
            (t1.missed_followup, "followup_after_each_write"),
            (t1.retry_and_reissue, "no_duplicate_refund"),
            (t1.close_early, "closure_matches_state"),
        ):
            for wake in ("poll", "push"):
                with self.subTest(agent=agent.__name__, wake=wake):
                    self.assertIs(score(example, agent, wake)["process"][key], False)

    def test_a_blind_retry_after_the_timeout_and_a_second_ticket_are_caught(self):
        self.assertFalse(
            score(generate_t2_case(300), t2.blind_retry)["process"]["no_duplicate_refund"]
        )
        self.assertFalse(
            score(generate_t9_case(300), t9.duplicate_ticket)["process"]["single_ticket_for_the_case"]
        )

    def test_doing_nothing_never_passes_and_closing_early_is_wrongful(self):
        def idle(session, call):
            return "LEFT" if session["trigger_type"] == "user_visit" else None

        for key, (generate, _, _) in FAMILY.items():
            for seed in SEEDS:
                case = generate(seed)
                with self.subTest(template=key, seed=seed):
                    self.assertFalse(score(case, idle)["metrics"]["case_resolution"])
                    if case["terminal_variant"] != "resolved":
                        self.assertEqual(score(case, t1.close_early)["metrics"]["wrongful_closure"], 1)

    def test_a_second_ticket_on_the_same_order_is_not_a_hand_over(self):
        for case in cases("t9", "escalate"):
            result = score(case, t9.duplicate_ticket)
            self.assertFalse(result["metrics"]["case_resolution"])


class PolicyTest(unittest.TestCase):
    def test_the_retail_policy_states_the_refund_limits_and_the_third_channel_rule(self):
        from case_lifecycle_bench.model_agent import POLICY

        self.assertIn("fails a second time", POLICY)
        self.assertIn("more than 5 calendar", POLICY)
        self.assertIn("third time, on a third", POLICY)


if __name__ == "__main__":
    unittest.main()
