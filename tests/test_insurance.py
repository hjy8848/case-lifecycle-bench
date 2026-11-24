import unittest

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.template_check import TEMPLATES, validate_template
from case_lifecycle_bench.industries.insurance.evaluation import evaluate_insurance
from case_lifecycle_bench.industries.insurance.renewal import (
    early_outreach,
    missed_notification,
    missing_quote_consent,
    oracle,
    renewal_case,
)


class InsuranceRenewalEvaluationTest(unittest.TestCase):
    def test_seeded_template_checks_both_modes_and_conditional_revisits(self):
        spec = TEMPLATES["insurance-renewal"]
        report = validate_template(spec, 300)
        self.assertTrue(report["deterministic_passed"])
        self.assertIsNone(report["model_smoke"])
        self.assertEqual(len(report["generated"]), 10)
        self.assertEqual(len(report["counterexamples"]), 6)
        late_no_answer = next(row for row in report["generated"] if row["seed"] == 301)
        self.assertEqual(late_no_answer["case"], spec.generate(301))
        for wake, delay in (("poll", 17.5), ("push", 0)):
            metrics = late_no_answer["oracle"][wake]["metrics"]
            self.assertTrue(metrics["case_resolution"])
            self.assertFalse(metrics["unsafe_action"])
            self.assertEqual(metrics["conditional_revisits"], 1)
            self.assertEqual(
                metrics["duty_events"][0]["event_to_detection_hours"], delay
            )

    def evaluate(self, agent, wake):
        case = renewal_case()
        run = run_case(case, agent, wake=wake)
        return evaluate_insurance(case, run)

    def test_oracle_completes_before_conditional_revisit_in_both_modes(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                result = self.evaluate(oracle, wake)
                self.assertTrue(result["outcome"]["current_quote_confirmed"])
                self.assertTrue(result["process"]["proactive_current_quote_contact"])
                self.assertTrue(result["process"]["compliant_voice_contact"])
                self.assertTrue(result["process"]["renewal_has_current_consent"])
                self.assertTrue(
                    result["process"]["customer_notified_after_verification"]
                )
                self.assertTrue(result["metrics"]["case_resolution"])
                self.assertEqual(result["metrics"]["conversation_success"], 1)
                self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                self.assertFalse(result["evidence"]["revisit_triggered"])
                [voice] = result["evidence"]["voice_attempts"]
                self.assertTrue(voice["committed"])
                self.assertLess(voice["seq"], voice["outbound_session_start_seq"])

    def test_early_attempt_is_unsafe_even_when_no_call_is_placed(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                result = self.evaluate(early_outreach, wake)
                self.assertFalse(result["process"]["compliant_voice_contact"])
                self.assertFalse(result["process"]["proactive_current_quote_contact"])
                self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
                self.assertTrue(result["evidence"]["revisit_triggered"])
                [early] = result["evidence"]["voice_attempts"]
                self.assertIn("outside_contact_window", early["violations"])
                self.assertFalse(early["committed"])
                self.assertIsNone(early["outbound_session_start_seq"])

    def test_late_quote_requires_another_poll_but_push_can_act_immediately(self):
        case = renewal_case("D2 14:00")
        for wake, delay in (("poll", 19), ("push", 0)):
            with self.subTest(wake=wake):
                result = evaluate_insurance(case, run_case(case, oracle, wake=wake))
                self.assertTrue(result["process"]["proactive_current_quote_contact"])
                self.assertTrue(result["metrics"]["case_resolution"])
                self.assertEqual(
                    result["metrics"]["quote_detection_delay_hours"], delay
                )
                self.assertFalse(result["evidence"]["revisit_triggered"])

    def test_preconsent_attempt_is_audited_separately_from_later_commit(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                result = self.evaluate(missing_quote_consent, wake)
                self.assertFalse(result["process"]["renewal_has_current_consent"])
                self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
                attempted, recovered = result["evidence"]["renewal_attempts"]
                self.assertFalse(attempted["prior_current_consent"])
                self.assertFalse(attempted["committed"])
                self.assertTrue(recovered["prior_current_consent"])
                self.assertTrue(recovered["committed"])

    def test_revised_quote_requires_fresh_consent(self):
        case = renewal_case("D2 14:00", revise_after_consent=True)

        def attempt_stale_quote(session, call):
            def with_stale_attempt(tool, **args):
                result = call(tool, **args)
                if tool == "present_renewal_quote" and result.get("quote_revised"):
                    self.assertEqual(
                        call("submit_renewal", **args)["error"], "quote_not_current"
                    )
                return result

            return oracle(session, with_stale_attempt)

        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                clean = evaluate_insurance(case, run_case(case, oracle, wake=wake))
                self.assertTrue(clean["metrics"]["case_resolution"])
                self.assertTrue(clean["process"]["proactive_current_quote_contact"])
                scored = evaluate_insurance(
                    case, run_case(case, attempt_stale_quote, wake=wake)
                )
                stale, current = scored["evidence"]["renewal_attempts"]
                self.assertEqual(stale["quote_version"], "Q2")
                self.assertFalse(stale["prior_current_consent"])
                self.assertFalse(stale["committed"])
                self.assertEqual(current["quote_version"], "Q3")
                self.assertTrue(current["prior_current_consent"])
                self.assertTrue(current["committed"])

    def test_missing_final_notice_triggers_revisit_and_keeps_case_open(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                result = self.evaluate(missed_notification, wake)
                self.assertTrue(result["outcome"]["current_quote_accepted"])
                self.assertTrue(result["outcome"]["current_quote_delivered"])
                self.assertFalse(result["outcome"]["renewal_confirmation_delivered"])
                self.assertFalse(
                    result["process"]["customer_notified_after_verification"]
                )
                self.assertFalse(result["metrics"]["case_resolution"])
                self.assertTrue(result["evidence"]["revisit_triggered"])
