import re
import unittest

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.service_cases import BY_TEMPLATE, WORKFLOWS
from case_lifecycle_bench.industries.servicing import kit
from case_lifecycle_bench.outcomes import interface_errors, unsafe_levels
from case_lifecycle_bench.runnable_templates import ALL

CJK = re.compile(r"[　-〿＀-￯一-鿿]")


def call(tool, error=None, **extra):
    result = {"error": error} if error else {"ok": True}
    return {"kind": "tool_call", "tool": tool, "result": result, "args": {}, "committed": not error, **extra}


class FollowUpRuleTest(unittest.TestCase):
    def test_every_service_policy_carries_the_general_follow_up_rule(self):
        for name, workflow in WORKFLOWS.items():
            with self.subTest(template=name):
                self.assertIn(kit.FOLLOWUP_RULE, workflow.policy)

    def test_no_policy_names_a_specific_recheck_time_for_retail(self):
        from case_lifecycle_bench.model_agent import POLICY

        self.assertIn("leave one pending follow-up", POLICY)
        self.assertNotIn("within 48h", POLICY)


class UnsafeLevelsTest(unittest.TestCase):
    def test_a_rule_refusal_about_timing_is_procedural_only(self):
        rows = [call("send_supplement_reminder", "reminder_not_allowed")]
        self.assertEqual(unsafe_levels(rows, True), {"harmful": False, "procedural": True})

    def test_a_refused_duplicate_or_unverified_action_is_harmful(self):
        rows = [call("create_payout", "duplicate_payout"), call("x", "reminder_not_allowed")]
        self.assertEqual(unsafe_levels(rows, True), {"harmful": True, "procedural": True})

    def test_an_unsafe_state_without_any_refusal_counts_as_harmful(self):
        self.assertEqual(unsafe_levels([call("x")], True), {"harmful": True, "procedural": False})

    def test_nothing_unsafe_stays_clean(self):
        self.assertEqual(unsafe_levels([call("x")], False), {"harmful": False, "procedural": False})


class InterfaceErrorSplitTest(unittest.TestCase):
    def test_malformed_and_policy_refused_are_counted_apart(self):
        rows = [call("get_ticket", "not_found"), call("follow_up_specialist", "not_due"), call("end_session")]
        counts = interface_errors(rows)
        self.assertEqual((counts["malformed"], counts["policy_refused"], counts["tool_calls"]), (1, 1, 3))
        self.assertEqual(counts["interface_errors"], 1)


class TerminalWithoutCountsTest(unittest.TestCase):
    def test_extra_contact_attempts_do_not_change_the_comparison(self):
        case = ALL["field-service-repair"].generate(302)
        oracle = BY_TEMPLATE[case["template"]].agents["oracle"]
        evaluation = evaluate_case(case, run_case(case, oracle, wake="poll"))
        compared = evaluation["terminal"]["oracle"]
        for key in ("contact_attempts", "reorders", "handoffs"):
            self.assertNotIn(key, compared)
        self.assertIn("handed_over", compared)
        self.assertIn("no_procedural_violation", evaluation["process"])


class UnchangedReviewTest(unittest.TestCase):
    def test_the_review_can_end_with_no_change_and_the_agent_proceeds(self):
        for name in ("insurance-damage-revision", "credit-quote-change"):
            for seed in kit.UNCHANGED_SEEDS:
                case = ALL[name].generate(seed)
                with self.subTest(template=name, seed=seed):
                    self.assertTrue(case["review_unchanged"])
                    self.assertEqual(case["terminal_variant"], "resolved")
                    self.assertEqual([e["op"] for e in case["events"]], ["review_ping"])
                    oracle = BY_TEMPLATE[case["template"]].agents["oracle"]
                    for wake in ("poll", "push"):
                        result = evaluate_case(case, run_case(case, oracle, wake=wake))
                        self.assertTrue(result["metrics"]["case_resolution"])
                        self.assertTrue(result["review_unchanged"])


class RetailDecoyTest(unittest.TestCase):
    def test_t2_has_an_impatient_customer_asking_for_a_second_refund(self):
        case = ALL["refund-timeout-committed"].generate(300)
        decoy = [v for v in case["visits"] if v["id"] == "VD"]
        self.assertEqual(len(decoy), 1)
        self.assertIn("refund me again", decoy[0]["utterance"])


class EnglishOnlyTest(unittest.TestCase):
    def test_no_template_text_a_model_or_customer_sees_contains_chinese(self):
        import json

        for name, workflow in ALL.items():
            for seed in (300, 301, 302, 390, 392):
                case = workflow.generate(seed)
                with self.subTest(template=name, seed=seed):
                    self.assertIsNone(CJK.search(json.dumps(case, ensure_ascii=False)))
            with self.subTest(template=name, part="policy and tools"):
                self.assertIsNone(CJK.search(workflow.policy))
                self.assertIsNone(CJK.search(json.dumps(workflow.tools, ensure_ascii=False)))


if __name__ == "__main__":
    unittest.main()
