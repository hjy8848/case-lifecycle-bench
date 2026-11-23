import unittest

from case_lifecycle_bench.cases import t1, t2, t9
from case_lifecycle_bench.compare import SETTINGS, summarize_runs
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.history import FullHistory
from case_lifecycle_bench.ledger import CaseLedger


def _saved_run(case, agent, setting="crm"):
    ledger = None
    if setting == "ledger":
        ledger = CaseLedger()
        agent = ledger.wrap(agent)
    elif setting == "full-history":
        agent = FullHistory().wrap(agent)
    run = run_case(case, agent)
    result = {
        "case_id": case["case_id"],
        "wake": "poll",
        "memory": setting,
        "evaluation": evaluate_case(case, run),
        "run": run,
    }
    if ledger is not None:
        result["ledger_guard_events"] = ledger.guard_events
    return result


class ComparisonTest(unittest.TestCase):
    def test_guarded_duplicate_remains_an_attempt(self):
        case = t1.t1_case()
        results = [
            _saved_run(case, t1.duplicate_refund, setting) for setting in SETTINGS
        ]
        summary = summarize_runs(results, expected_cases=[case["case_id"]])
        self.assertTrue(summary["complete"])
        for setting in SETTINGS:
            with self.subTest(setting=setting):
                counts = summary["settings"][setting]["counts"]
                self.assertEqual(
                    counts["duplicate_refund_attempted"],
                    {"numerator": 1, "denominator": 1},
                )
                self.assertEqual(
                    counts["duplicate_refund_committed"],
                    {"numerator": int(setting != "ledger"), "denominator": 1},
                )
        self.assertEqual(
            summary["settings"]["ledger"]["guard_events_by_tool"], {"issue_refund": 1}
        )

    def test_p8_checks_committed_notice_during_the_failure_observation(self):
        def suppress_failure_notice(session, call):
            def filtered(tool, **args):
                if tool == "send_message_to_user" and "失败" in args["text"]:
                    return {"error": "test_not_committed"}
                return call(tool, **args)

            return t9.oracle(session, filtered)

        result = _saved_run(t9.t9_case(), suppress_failure_notice)
        stats = summarize_runs([result])["settings"]["crm"]["counts"]
        self.assertEqual(
            stats["refund_failure_observed"], {"numerator": 1, "denominator": 1}
        )
        self.assertEqual(
            stats["failure_notified_in_detection_session"],
            {"numerator": 0, "denominator": 1},
        )
        self.assertEqual(
            stats["conversation_success"], {"numerator": 1, "denominator": 1}
        )
        self.assertEqual(stats["queried_after_committed_timeout"]["denominator"], 0)

    def test_incomplete_panel_and_timeout_eligibility(self):
        case = t2.t2_case()
        row = _saved_run(case, t2.blind_retry)
        summary = summarize_runs([row], expected_cases=[case["case_id"]])
        self.assertFalse(summary["complete"])
        self.assertEqual(len(summary["missing"]), 2)
        self.assertEqual(
            summary["settings"]["crm"]["counts"]["queried_after_committed_timeout"],
            {"numerator": 0, "denominator": 1},
        )
        summary = summarize_runs([row, row], expected_cases=[case["case_id"]])
        self.assertEqual(summary["duplicates"][0]["count"], 2)
        self.assertEqual(summary["settings"]["crm"]["runs"], 0)

    def test_different_case_hashes_do_not_form_a_pair(self):
        case = t1.t1_case()
        rows = [_saved_run(case, t1.oracle, setting) for setting in SETTINGS]
        for row in rows:
            row["meta"] = {
                "case_sha256": "other" if row["memory"] == "ledger" else "same"
            }
        summary = summarize_runs(rows, expected_cases=[case["case_id"]])
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["paired_cases"], [])
        self.assertEqual(summary["pairing_mismatches"][0]["field"], "case_sha256")
