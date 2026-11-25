import unittest

from case_lifecycle_bench.cases.t2 import t2_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.model_agent import _customer_reply, model_agent


class ScriptedGateway:
    def __init__(self, replies):
        self.replies = iter(replies)

    def reply(self, model, messages, tools=None):
        return next(self.replies)


class CustomerSimulatorTest(unittest.TestCase):
    def test_t2_authorization_is_logged_before_the_refund_call(self):
        case = t2_case()
        gateway = ScriptedGateway(
            [
                {"content": "Do you confirm a full refund of $312.40?"},
                {"content": "###CONFIRMED###"},
                {
                    "tool_calls": [
                        {
                            "id": "refund",
                            "function": {
                                "name": "issue_refund",
                                "arguments": '{"order_id":"O1","amount":312.40}',
                            },
                        }
                    ]
                },
                {"content": "Refund request submitted, waiting for the payment gateway."},
                {"content": "CONFIRMED"},
            ]
        )
        run = run_case(case, model_agent(gateway, "agent", "customer", case))
        trace = run["trajectory"]
        authorization = next(
            row
            for row in trace
            if row.get("role") == "user" and row["text"] == "Confirmed, I agree to process this refund."
        )
        refund = next(row for row in trace if row.get("tool") == "issue_refund")
        self.assertLess(authorization["seq"], refund["seq"])
        self.assertEqual(refund["result"], {"error": "timeout"})
        end = next(row for row in trace if row["kind"] == "session_end")
        self.assertEqual(end["user_signal"], "CONFIRMED")

    def test_confirmation_recovery_is_limited_to_direct_refund_authorization(self):
        visit = t2_case()["visits"][0]
        for agent_text, expected in (
            ("Please confirm the refund.", "Confirmed, I agree to process this refund."),
            ("Please confirm whether the refund has arrived.", "CONFIRMED"),
            ("The refund request was submitted. Please confirm whether the return can be arranged first.", "CONFIRMED"),
            ("Please confirm the refund and pay the fee.", "CONFIRMED"),
        ):
            with self.subTest(agent_text=agent_text):
                gateway = ScriptedGateway([{"content": "CONFIRMED"}])
                reply = _customer_reply(
                    gateway, "customer", visit, [{"agent": agent_text}]
                )
                self.assertEqual(reply, expected)

        gateway = ScriptedGateway([{"content": "UNRESOLVED"}])
        reply = _customer_reply(gateway, "customer", visit, [{"agent": "Please confirm the refund."}])
        self.assertEqual(reply, "UNRESOLVED")
