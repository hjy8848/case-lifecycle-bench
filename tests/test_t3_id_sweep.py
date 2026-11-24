import tempfile
import unittest
from pathlib import Path

from case_lifecycle_bench.cases import t3
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.t3_id_sweep import (
    ID_STYLES,
    premature_refund_location,
    run_one,
    summarize,
    sweep_case,
    synthetic_identity,
)


class T3IdSweepTest(unittest.TestCase):
    def test_identities_are_reproducible_and_distinct(self):
        for style in ID_STYLES:
            self.assertEqual(synthetic_identity(style, 7), synthetic_identity(style, 7))
            self.assertNotEqual(
                synthetic_identity(style, 7)["order_id"],
                synthetic_identity(style, 8)["order_id"],
            )

    def test_only_identity_and_amount_change(self):
        base = t3.t3_case()
        case = sweep_case("w7", 3)
        identity = synthetic_identity("w7", 3)
        self.assertEqual(case["init"]["order"], identity["order_id"])
        self.assertEqual(case["init"]["user"], identity["user_id"])
        self.assertEqual(case["init"]["amount"], identity["amount"])
        self.assertEqual(
            case["events"][0]["op"].replace(identity["order_id"], "X"),
            base["events"][0]["op"].replace(base["init"]["order"], "X"),
        )
        self.assertEqual(len(case["visits"]), len(base["visits"]))
        numeric = sweep_case("numeric", 3)
        self.assertEqual(numeric["init"]["user"], case["init"]["user"])
        self.assertEqual(numeric["init"]["amount"], case["init"]["amount"])

    def test_oracle_is_safe_and_premature_refund_is_located(self):
        case = sweep_case("numeric", 1)
        for wake in ("poll", "push"):
            oracle = run_case(case, t3.oracle, wake=wake)
            self.assertTrue(evaluate_case(case, oracle)["metrics"]["case_resolution"])
            self.assertIsNone(premature_refund_location(oracle))
            bad = run_case(case, t3.premature_refund, wake=wake)
            self.assertEqual(premature_refund_location(bad), "first_session")
            self.assertEqual(
                evaluate_case(case, bad)["metrics"]["unsafe_action_rate"], 1
            )
            for row in bad["trajectory"]:
                if row.get("tool") == "issue_refund":
                    row["committed"] = False
            self.assertIsNone(premature_refund_location(bad))

    def test_runs_resume_and_summarize(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = run_one("clb", 1, "poll", None, None, output)
            again = run_one("clb", 1, "poll", None, None, output)
            self.assertEqual(first["case_id"], again["case_id"])
            with self.assertRaisesRegex(ValueError, "different configuration"):
                run_one("clb", 1, "poll", "some-model", None, output)
            summary = summarize([first])
            self.assertEqual(summary["clb/poll"]["n"], 1)
            self.assertEqual(summary["clb/poll"]["premature_refund"], 0)


if __name__ == "__main__":
    unittest.main()
