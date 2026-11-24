import unittest

from case_lifecycle_bench.cases import t3
from case_lifecycle_bench.cases.t3_variants import generate_t3_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case

SEEDS = range(300, 310)


def visits(run):
    return [r["trigger_id"] for r in run["trajectory"] if r["kind"] == "session_start" and r["trigger_type"] == "user_visit"]


class RevisitTest(unittest.TestCase):
    def test_every_instance_has_a_conditional_revisit(self):
        for seed in SEEDS:
            case = generate_t3_case(seed)
            self.assertTrue(any(v.get("condition") for v in case["visits"]))

    def test_an_agent_that_keeps_the_customer_informed_is_not_asked_again(self):
        for seed in SEEDS:
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    case = generate_t3_case(seed)
                    run = run_case(case, t3.oracle, wake=wake)
                    self.assertEqual(visits(run), ["V1"])

    def test_a_silent_agent_brings_the_customer_back(self):
        for seed in SEEDS:
            case = generate_t3_case(seed)
            run = run_case(case, t3.do_nothing)
            self.assertEqual(visits(run), ["V1", "V2"])
            self.assertFalse(evaluate_case(case, run)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_in_every_variant(self):
        for seed in SEEDS:
            case = generate_t3_case(seed)
            result = evaluate_case(case, run_case(case, t3.close_early))
            if case["terminal_variant"] != "resolved":
                self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_a_revisit_answered_from_the_carrier_passes_its_stage(self):
        # An agent that does the V1 work but never messages the customer is asked again.
        def quiet(session, call):
            if session["trigger_type"] == "user_visit" and session["trigger_id"] == "V1":
                return t3.oracle(session, call)
            if session["trigger_type"] == "user_visit":
                return t3._revisit(session, call)
            return None

        case = generate_t3_case(300)
        run = run_case(case, quiet)
        result = evaluate_case(case, run)
        self.assertIn("V2", visits(run))
        v2 = [s for s in result["stages"].values() if s["trigger_id"] == "V2"]
        self.assertTrue(v2 and all(s["passed"] for s in v2))


if __name__ == "__main__":
    unittest.main()
