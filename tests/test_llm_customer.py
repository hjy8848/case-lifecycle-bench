import json
import unittest

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import credit_delinquency as l1
from case_lifecycle_bench.industries.servicing import repair_parts as h1
from case_lifecycle_bench.model_agent import _contact_visit, model_agent
from case_lifecycle_bench.outcomes import variant_for_seed


class FakeGateway:
    """Replies in order; records every request so a test can inspect the prompts."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.calls = 0
        self.retries = 0

    def reply(self, model, messages, tools=None):
        self.requests.append({"model": model, "messages": messages, "tools": tools})
        return self.replies.pop(0)


def tool_call(name, **args):
    return {
        "content": None,
        "tool_calls": [
            {"id": f"id-{name}", "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def contact_session(contact_id="C1"):
    return {
        "t": "2026-01-05T10:00:00",
        "trigger_type": "outbound_contact",
        "trigger_id": contact_id,
        "contact_id": contact_id,
        "channel": "voice",
        "message": "你好，我接到了你们的来电。",
        "user_id": "U",
        "subject_id": "S",
    }


class Recorder:
    def __init__(self):
        self.steps = []

    def __call__(self, tool, **args):
        self.steps.append((tool, args))
        return {"ok": True}


def llm(case):
    return {**case, "customer_mode": "llm"}


class IntentStaysPrivateTest(unittest.TestCase):
    def contact_result(self, case, agent):
        run = run_case(case, agent)
        return [row["result"] for row in run["trajectory"] if row.get("tool") == agent_tool[agent]]

    def test_the_repayment_offer_is_only_in_the_result_for_a_scripted_customer(self):
        case = next(l1.generate(s) for s in range(300, 310) if variant_for_seed(s) == "wait")

        def probe(session, call):
            if session["trigger_type"] == "case_intake":
                call("create_ticket", user_id=session["user_id"], subject_id=session["subject_id"], summary="x")
                call("start_outbound_contact", customer_id=session["user_id"], channel="voice")
            elif session["trigger_type"] == "outbound_contact":
                call("verify_customer_identity", contact_id=session["contact_id"])
                call("inform_overdue_status", contact_id=session["contact_id"])
            return "CONFIRMED" if session["trigger_type"] == "outbound_contact" else None

        def informed(candidate):
            run = run_case(candidate, probe)
            return next(
                row["result"] for row in run["trajectory"] if row.get("tool") == "inform_overdue_status"
            )

        self.assertIn("commitment_offered_by", informed(case))
        self.assertNotIn("commitment_offered_by", informed(llm(case)))

    def test_the_slot_choice_is_only_in_the_result_for_a_scripted_customer(self):
        case = next(h1.generate(s) for s in range(300, 310) if variant_for_seed(s) == "resolved")

        def offer(candidate):
            env = h1.RepairEnvironment(candidate, "poll")
            record = env.state["work_items"][env.subject_id]
            env.state["work_items"][env.subject_id] = {**record, "status": "arrived_match"}
            env.contact_scripts["C1"] = candidate["init"]["script"]["contacts"][0]
            contact = {"contact_id": "C1", "customer_id": env.user_id}
            return env._contact_tool("offer_available_slots", contact, {})[0]

        self.assertEqual(
            offer(case)["customer_choice"], case["init"]["script"]["contacts"][0]["chosen_slot"]
        )
        hidden = offer(llm(case))
        self.assertNotIn("customer_choice", hidden)
        self.assertEqual(hidden["slots"], case["init"]["script"]["contacts"][0]["available_slots"])


class CommitmentDateTest(unittest.TestCase):
    def record(self, case, written):
        def agent(session, call):
            if session["trigger_type"] == "case_intake":
                call("create_ticket", user_id=session["user_id"], subject_id=session["subject_id"], summary="x")
                call("start_outbound_contact", customer_id=session["user_id"], channel="voice")
            elif session["trigger_type"] == "outbound_contact":
                call("verify_customer_identity", contact_id=session["contact_id"])
                call("inform_overdue_status", contact_id=session["contact_id"])
                call("record_repayment_commitment", contact_id=session["contact_id"], promised_by=written)
            return "CONFIRMED" if session["trigger_type"] == "outbound_contact" else None

        run = run_case(llm(case), agent)
        return next(r["result"] for r in run["trajectory"] if r.get("tool") == "record_repayment_commitment")

    def test_the_date_may_be_written_with_a_space_and_still_counts(self):
        case = l1.generate(300)
        promised = case["init"]["script"]["contacts"][0]["promise_by"]

        self.assertEqual(self.record(case, promised), {"ok": True})
        self.assertEqual(self.record(case, promised.replace("T", " ")), {"ok": True})

    def test_a_different_moment_or_an_unreadable_date_is_refused(self):
        case = l1.generate(300)

        self.assertEqual(
            self.record(case, "2026-01-09T18:00:00"), {"error": "commitment_not_offered"}
        )
        self.assertEqual(self.record(case, "下周三"), {"error": "invalid_time"})


class PersonaTest(unittest.TestCase):
    def test_each_call_gets_its_own_persona_and_later_calls_use_the_default(self):
        case = next(l1.generate(s) for s in range(300, 310) if variant_for_seed(s) == "wait")
        contacts = case["init"]["script"]["contacts"]

        first = _contact_visit(case, contact_session("C1"))
        second = _contact_visit(case, contact_session("C2"))
        later = _contact_visit(case, contact_session("C7"))

        self.assertEqual(first["goal"], contacts[0]["persona"])
        self.assertEqual(second["goal"], contacts[1]["persona"])
        self.assertEqual(later["goal"], case["init"]["script"]["default_contact"]["persona"])
        self.assertNotEqual(first["goal"], second["goal"])
        self.assertEqual(first["utterance"], "你好，我接到了你们的来电。")

    def test_every_template_instance_defines_personas_for_its_calls(self):
        for module in (l1, h1):
            for seed in range(300, 310):
                script = module.generate(seed)["init"]["script"]
                with self.subTest(module=module.__name__, seed=seed):
                    self.assertTrue(script["default_contact"]["persona"])
                    self.assertTrue(all(row["persona"] for row in script["contacts"]))

    def test_the_repayment_persona_names_the_scripted_date_in_plain_words(self):
        case = l1.generate(300)
        row = case["init"]["script"]["contacts"][0]
        moment = row["promise_by"]
        month, day = int(moment[5:7]), int(moment[8:10])

        self.assertIn(f"{month}月{day}日{moment[11:16]}", row["persona"])


class ModelAgentWiringTest(unittest.TestCase):
    def run_agent(self, case, replies, user_replies):
        agent_gateway = FakeGateway(*replies)
        user_gateway = FakeGateway(*user_replies)
        agent = model_agent(
            agent_gateway,
            "agent-model",
            "user-model",
            case,
            user_gateway=user_gateway,
            policy="policy",
            tools=l1.TOOLS,
        )
        recorder = Recorder()
        signal = agent(contact_session(), recorder)
        return signal, recorder, agent_gateway, user_gateway

    def test_the_simulated_customer_answers_the_agents_words_on_an_llm_case(self):
        case = llm(l1.generate(300))
        signal, recorder, _, user_gateway = self.run_agent(
            case,
            [
                {"content": "您好，请问您什么时候能还款？"},
                tool_call("end_session"),
            ],
            [{"content": "我会在1月7日18:00前还款。"}],
        )

        self.assertEqual(signal, "LEFT")
        self.assertIn(("user_reply", {"text": "我会在1月7日18:00前还款。"}), recorder.steps)
        system = user_gateway.requests[0]["messages"][0]["content"]
        self.assertIn(case["init"]["script"]["contacts"][0]["persona"], system)

    def test_a_terminal_token_from_the_customer_ends_the_call_with_that_signal(self):
        case = llm(l1.generate(300))
        signal, _, _, _ = self.run_agent(
            case,
            [{"content": "已记录您的还款时间，感谢配合。"}],
            [{"content": "CONFIRMED"}],
        )

        self.assertEqual(signal, "CONFIRMED")

    def test_a_scripted_case_never_calls_the_simulator_for_an_outbound_contact(self):
        case = l1.generate(300)
        signal, recorder, _, user_gateway = self.run_agent(
            case, [{"content": "您好，请问您什么时候能还款？"}], []
        )

        self.assertIsNone(signal)
        self.assertEqual(user_gateway.requests, [])
        self.assertNotIn("user_reply", [step[0] for step in recorder.steps])


class OracleReferenceTest(unittest.TestCase):
    def test_an_llm_mode_case_is_still_scored_against_the_scripted_oracle(self):
        # The scripted Oracle cannot hear the customer, so it only runs in script mode.
        # An llm-mode run must still be compared with that scripted end state.
        for module, seed in ((l1, 301), (h1, 302)):
            case = module.generate(seed)
            scripted = evaluate_case(case, run_case(case, module.oracle))
            idle = evaluate_case(llm(case), run_case(llm(case), module.do_nothing))
            with self.subTest(module=module.__name__):
                self.assertEqual(idle["terminal"]["oracle"], scripted["terminal"]["agent"])
                self.assertFalse(idle["metrics"]["case_resolution"])


if __name__ == "__main__":
    unittest.main()
