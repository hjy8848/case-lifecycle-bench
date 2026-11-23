import unittest

from case_lifecycle_bench.cases import t1, t2, t3
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.model_agent import model_agent


class SmokeTest(unittest.TestCase):
    def test_oracles_pass_in_both_wake_modes(self):
        for fixture, agent in (
            (t1.t1_case, t1.oracle),
            (t2.t2_case, t2.oracle),
            (t3.t3_case, t3.oracle),
        ):
            for wake in ("poll", "push"):
                with self.subTest(case=fixture.__name__, wake=wake):
                    case = fixture()
                    run = run_case(case, agent, wake=wake)
                    metrics = evaluate_case(case, run)["metrics"]
                    self.assertTrue(metrics["case_resolution"])
                    self.assertEqual(metrics["session_correctness"], 1)
                    self.assertEqual(metrics["unsafe_action_rate"], 0)

    def test_t2_records_committed_timeout_and_detects_duplicate(self):
        case = t2.t2_case()
        run = run_case(case, t2.blind_retry)
        timeout = next(row for row in run["trajectory"] if row.get("fault"))
        self.assertEqual(timeout["fault"], "timeout_after_commit")
        self.assertEqual(timeout["result"], {"error": "timeout"})
        self.assertTrue(timeout["committed"])
        evaluation = evaluate_case(case, run)
        self.assertFalse(evaluation["process"]["checked_before_repeat_issue"])
        self.assertEqual(evaluation["metrics"]["unsafe_action_rate"], 1)

    def test_t3_distinguishes_missed_recheck_and_premature_refund(self):
        case = t3.t3_case()
        missed = evaluate_case(case, run_case(case, t3.missed_recheck))
        early = evaluate_case(case, run_case(case, t3.premature_refund))
        self.assertFalse(missed["process"]["pending_intercept_rechecks_scheduled"])
        self.assertFalse(early["process"]["no_refund_before_interception"])
        self.assertEqual(early["metrics"]["unsafe_action_rate"], 1)

    def test_model_customer_replies_enter_trace_without_exposing_fixture(self):
        class Gateway:
            def __init__(self):
                self.messages = []
                self.replies = iter(
                    [
                        {"content": "请确认是否取消。"},
                        {"content": "确认取消。"},
                        {"content": "我会处理。"},
                        {"content": "LEFT"},
                    ]
                )

            def reply(self, model, messages, tools=None):
                self.messages.append((model, list(messages), tools))
                return next(self.replies)

        case = t3.t3_case()
        gateway = Gateway()
        agent = model_agent(gateway, "agent", "customer", case)
        run = run_case(case, agent)
        self.assertEqual(
            [row[0] for row in gateway.messages], ["agent", "customer"] * 2
        )
        agent_messages = gateway.messages[0][1]
        self.assertNotIn(case["visits"][0]["goal"], str(agent_messages))
        self.assertTrue(
            any(
                row.get("role") == "user" and row.get("text") == "确认取消。"
                for row in run["trajectory"]
            )
        )
        self.assertEqual(run["trajectory"][-1]["user_signal"], "LEFT")


if __name__ == "__main__":
    unittest.main()
