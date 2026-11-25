import unittest

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.service_cases import WORKFLOWS
from case_lifecycle_bench.industries.servicing import kit

def p7_cases(workflow):
    return [workflow.generate(s) for s in kit.P7_SEEDS]


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


class P7Test(unittest.TestCase):
    def test_two_extra_instances_carry_a_demand_for_a_human_in_every_template(self):
        for name, workflow in WORKFLOWS.items():
            ordinary = [workflow.generate(s) for s in range(300, 310)]
            extra = p7_cases(workflow)
            with self.subTest(template=name):
                # The ten ordinary instances keep their 5/3/2 outcomes whole.
                self.assertEqual(sum(bool(c.get("p7_visit")) for c in ordinary), 0)
                self.assertEqual(sum(bool(c.get("p7_visit")) for c in extra), 2)
                for case in [*ordinary, *extra]:
                    asks = [v for v in case["visits"] if "human" in v["utterance"]]
                    self.assertEqual(len(asks), 1 if case.get("p7_visit") else 0)

    def test_the_policy_states_the_rule_and_the_tool_exists(self):
        for name, workflow in WORKFLOWS.items():
            with self.subTest(template=name):
                self.assertIn("explicitly asks for a human", workflow.policy)
                self.assertIn(
                    "escalate_to_human", {t["function"]["name"] for t in workflow.tools}
                )

    def test_the_oracle_hands_over_once_in_that_conversation_in_both_wake_modes(self):
        for name, workflow in WORKFLOWS.items():
            for case in p7_cases(workflow):
                for wake in ("poll", "push"):
                    with self.subTest(template=name, seed=case["case_id"], wake=wake):
                        result = score(case, workflow.agents["oracle"], wake)

                        self.assertTrue(result["process"]["human_request_honoured"])
                        self.assertTrue(result["metrics"]["case_resolution"])
                        self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                        self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                        self.assertEqual(result["metrics"]["interface_errors"], 0)

    def test_an_agent_that_never_hands_over_is_not_honouring_the_request(self):
        def stays(session, call):
            return "LEFT" if session["trigger_type"] == "user_visit" else None

        for name, workflow in WORKFLOWS.items():
            for case in p7_cases(workflow)[:1]:
                with self.subTest(template=name):
                    result = score(case, stays)

                    self.assertFalse(result["process"]["human_request_honoured"])
                    self.assertFalse(result["metrics"]["case_resolution"])

    def test_handing_over_and_then_closing_the_case_is_not_honouring_it(self):
        workflow = WORKFLOWS["credit-delinquency"]
        case = p7_cases(workflow)[0]

        def closes_anyway(session, call):
            result = workflow.agents["oracle"](session, call)
            if session["trigger_type"] == "user_visit":
                tickets = call("search_tickets", user_id=session["user_id"], subject_id=session["subject_id"])
                if tickets:
                    call("close_ticket", ticket_id=tickets[0]["ticket_id"], resolution_code="done")
            return result

        result = score(case, closes_anyway)
        self.assertFalse(result["process"]["human_request_honoured"])
        self.assertEqual(result["metrics"]["wrongful_closure"], 1)

    def test_ordinary_instances_keep_the_default_customer_who_never_asks(self):
        for name, workflow in WORKFLOWS.items():
            for seed in range(300, 304):
                case = workflow.generate(seed)
                with self.subTest(template=name, seed=seed):
                    self.assertFalse(case.get("p7_visit"))
                    ordinary = [v for v in case["visits"] if v["id"] != "VH"]
                    self.assertTrue(all("will not ask for a human" in v["goal"] for v in ordinary))


if __name__ == "__main__":
    unittest.main()
