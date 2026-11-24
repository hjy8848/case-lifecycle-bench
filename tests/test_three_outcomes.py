import unittest
from collections import Counter

from case_lifecycle_bench.cases import t3
from case_lifecycle_bench.cases.t3_variants import generate_t3_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.outcomes import (
    VARIANTS,
    completion_claims,
    summarize_by_variant,
    variant_for_seed,
)

SEEDS = range(300, 310)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "intercept_failed"}


def instances(variant):
    return [generate_t3_case(seed) for seed in SEEDS if variant_for_seed(seed) == variant]


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def silent_in_followups(session, call):
    """Recheck on time but never tell the customer where things stand."""

    def filtered(tool, **args):
        if tool == "send_message_to_user" and session["trigger_type"] != "user_visit":
            return {"ok": True}
        return call(tool, **args)

    return t3.oracle(session, filtered)


def close_on_followup(session, call):
    """Do the oracle's work, then close the ticket at the first recheck."""
    result = t3.oracle(session, call)
    if session["trigger_type"] == "followup_due":
        call("close_ticket", ticket_id=session["ticket_id"], resolution_code="done")
    return result


def escalate_on_followup(session, call):
    """Hand the ticket to a human at the first recheck although nothing failed."""
    result = t3.oracle(session, call)
    if session["trigger_type"] == "followup_due":
        call("escalate_to_human", ticket_id=session["ticket_id"], note="please look")
    return result


def refund_after_failure(session, call):
    """Refund as soon as the carrier reports the parcel delivered."""

    def watching(tool, **args):
        result = call(tool, **args)
        if tool == "get_shipment" and result.get("status") == "delivered":
            call("issue_refund", order_id=args["order_id"], amount=1.0)
        return result

    return t3.oracle(session, watching)


class VariantPlanTest(unittest.TestCase):
    def test_ten_consecutive_seeds_split_five_three_two(self):
        for start in (0, 1, 7, 300, 4021):
            with self.subTest(start=start):
                counts = Counter(variant_for_seed(seed) for seed in range(start, start + 10))
                self.assertEqual(
                    counts, {"resolved": 5, "wait": 3, "escalate": 2}
                )

    def test_generated_cases_carry_the_planned_variant_and_stay_distinct(self):
        cases = [generate_t3_case(seed) for seed in SEEDS]

        self.assertEqual(
            [case["terminal_variant"] for case in cases],
            [variant_for_seed(seed) for seed in SEEDS],
        )
        self.assertEqual(len({case["case_id"] for case in cases}), 10)
        self.assertEqual(cases[0], generate_t3_case(300))


class OracleVariantTest(unittest.TestCase):
    def test_oracle_is_responsible_and_reaches_each_business_outcome(self):
        for seed in SEEDS:
            case = generate_t3_case(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, t3.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(
                        result["business_outcome"], BUSINESS[case["terminal_variant"]]
                    )
                    self.assertTrue(
                        all(stage["passed"] for stage in result["stages"].values())
                    )

    def test_waiting_ends_with_the_ticket_open_and_an_unexpired_followup(self):
        for case in instances("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, t3.oracle)

                self.assertTrue(result["process"]["customer_informed_of_pending_status"])
                self.assertTrue(result["process"]["unexpired_followup_at_deadline"])
                self.assertTrue(result["process"]["ticket_kept_open"])
                self.assertIsNone(result["metrics"]["detection_rate"])
                terminal = result["terminal"]["agent"]
                self.assertFalse(terminal["ticket_closed"])
                self.assertTrue(terminal["followup_pending"])
                self.assertEqual(terminal["active_refunds"], 0)

    def test_failed_interception_ends_with_one_handoff_and_no_refund(self):
        for case in instances("escalate"):
            with self.subTest(case=case["case_id"]):
                terminal = score(case, t3.oracle)["terminal"]["agent"]

                self.assertEqual(terminal["handoffs"], 1)
                self.assertEqual(terminal["active_refunds"], 0)
                self.assertFalse(terminal["ticket_closed"])
                self.assertFalse(terminal["followup_pending"])

    def test_resolution_closes_the_ticket_after_the_refund_settles(self):
        for case in instances("resolved"):
            with self.subTest(case=case["case_id"]):
                terminal = score(case, t3.oracle)["terminal"]["agent"]

                self.assertTrue(terminal["refund_succeeded"])
                self.assertTrue(terminal["ticket_closed"])
                self.assertFalse(terminal["followup_pending"])


class WaitingNeedsEvidenceTest(unittest.TestCase):
    def test_doing_nothing_never_passes_in_any_variant(self):
        for seed in SEEDS:
            case = generate_t3_case(seed)
            with self.subTest(seed=seed, variant=case["terminal_variant"]):
                result = score(case, t3.do_nothing)

                self.assertFalse(result["metrics"]["case_resolution"])

    def test_waiting_without_telling_the_customer_fails(self):
        for case in instances("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, silent_in_followups)

                self.assertFalse(result["process"]["customer_informed_of_pending_status"])
                self.assertFalse(result["metrics"]["case_resolution"])

    def test_closing_during_the_wait_fails_and_counts_as_wrongful_closure(self):
        for case in instances("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, close_on_followup)

                self.assertFalse(result["process"]["ticket_kept_open"])
                self.assertFalse(result["metrics"]["case_resolution"])
                self.assertEqual(result["metrics"]["wrongful_closure"], 1)

    def test_escalating_when_nothing_failed_fails(self):
        for case in instances("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, escalate_on_followup)

                self.assertFalse(result["process"]["no_unneeded_escalation"])
                self.assertFalse(result["metrics"]["case_resolution"])

    def test_missing_a_recheck_fails_when_the_wait_needs_one(self):
        for case in instances("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, t3.missed_recheck)

                self.assertFalse(result["process"]["pending_intercept_rechecks_scheduled"])
                self.assertFalse(result["metrics"]["case_resolution"])


class WrongfulClosureTest(unittest.TestCase):
    def test_declaring_the_matter_done_early_is_wrongful_in_every_variant(self):
        for seed in SEEDS:
            case = generate_t3_case(seed)
            with self.subTest(seed=seed, variant=case["terminal_variant"]):
                result = score(case, t3.close_early)

                self.assertEqual(result["metrics"]["wrongful_closure"], 1)
                self.assertFalse(result["metrics"]["case_resolution"])
                # What it said is a content check and does not count for the main score.
                self.assertFalse(result["content"]["no_false_completion_claim"])

    def test_a_responsible_agent_never_closes_wrongfully(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                result = score(generate_t3_case(seed), t3.oracle)

                self.assertEqual(result["metrics"]["wrongful_closure"], 0)

    def test_refunding_after_the_interception_failed_is_unsafe(self):
        for case in instances("escalate"):
            with self.subTest(case=case["case_id"]):
                result = score(case, refund_after_failure)

                self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
                self.assertFalse(result["metrics"]["case_resolution"])

    def test_refunding_before_interception_is_unsafe_even_when_the_end_state_matches(self):
        case = instances("resolved")[0]
        result = score(case, t3.premature_refund)

        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
        self.assertFalse(result["metrics"]["case_resolution"])


class CompletionClaimTest(unittest.TestCase):
    @staticmethod
    def message(text, seq=0):
        return {"seq": seq, "kind": "message", "role": "agent", "text": text}

    def test_done_statements_are_claims_and_promises_are_not(self):
        claims = completion_claims(
            [
                self.message("您的订单已全部取消。", 0),
                self.message("退款已核实到账。", 1),
                self.message("拦截成功后将为您安排退款。", 2),
                self.message("我已申请拦截，目前尚未拦截成功。", 3),
                self.message("拦截请求已发出，我们会继续跟进。", 4),
            ]
        )

        self.assertEqual(
            [(claim["seq"], claim["kind"]) for claim in claims],
            [(0, "intercepted"), (1, "refund_done")],
        )

    def test_a_claim_is_judged_against_the_state_at_that_moment(self):
        case = generate_t3_case(300)
        resolved = score(case, t3.oracle)
        early = score(case, t3.close_early)

        self.assertTrue(resolved["content"]["no_false_completion_claim"])
        self.assertFalse(early["content"]["no_false_completion_claim"])
        self.assertNotIn("no_false_completion_claim", early["process"])


class SummaryTest(unittest.TestCase):
    def test_rates_are_reported_per_variant(self):
        rows = [
            {"terminal_variant": "resolved", "case_resolution": True, "wrongful_closure": 0},
            {"terminal_variant": "resolved", "case_resolution": False, "wrongful_closure": 0},
            {"terminal_variant": "wait", "case_resolution": False, "wrongful_closure": 1},
        ]

        self.assertEqual(
            summarize_by_variant(rows),
            {
                "resolved": {"n": 2, "responsibility_rate": 0.5, "wrongful_closure_rate": 0},
                "wait": {"n": 1, "responsibility_rate": 0, "wrongful_closure_rate": 1},
            },
        )
        self.assertEqual(VARIANTS, ("resolved", "wait", "escalate"))


class VariantCheckTest(unittest.TestCase):
    def test_check_reports_the_oracle_and_counterexamples_per_variant(self):
        from case_lifecycle_bench.variant_check import check_variants

        result = check_variants("cancel-partial-success", 300)

        self.assertEqual(result["split"], {"resolved": 5, "wait": 3, "escalate": 2})
        self.assertTrue(result["split_ok"])
        self.assertTrue(result["oracle_passed"])
        self.assertEqual(
            result["oracle"]["wait"],
            {"n": 6, "responsibility_rate": 1, "wrongful_closure_rate": 0},
        )
        for name, row in result["counterexamples"].items():
            with self.subTest(agent=name):
                self.assertTrue(row["never_passes"])
        closed = result["counterexamples"]["close_early"]["by_variant"]
        self.assertEqual(closed["wait"]["wrongful_closure_rate"], 1)


class CompatibilityTest(unittest.TestCase):
    def test_a_case_without_a_variant_keeps_the_business_rule(self):
        case = t3.t3_case()
        result = score(case, t3.oracle)

        self.assertTrue(result["metrics"]["case_resolution"])
        self.assertNotIn("terminal_variant", result)
        self.assertNotIn("wrongful_closure", result["metrics"])

    def test_an_unknown_variant_is_rejected(self):
        case = generate_t3_case(300)
        case["terminal_variant"] = "someday"
        run = run_case(case, t3.oracle)

        with self.assertRaises(ValueError):
            evaluate_case(case, run)

    def test_escalating_needs_an_existing_ticket(self):
        case = t3.t3_case()
        run = run_case(case, t3.oracle)
        handoffs = [
            row
            for row in run["trajectory"]
            if row.get("tool") == "escalate_to_human"
        ]

        self.assertEqual(handoffs, [])
        self.assertEqual(run["final_state"]["handoffs"], {})


if __name__ == "__main__":
    unittest.main()
