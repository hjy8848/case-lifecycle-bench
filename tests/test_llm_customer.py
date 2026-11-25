import unittest

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.service_cases import WORKFLOWS
from case_lifecycle_bench.industries.servicing import credit_delinquency as l1
from case_lifecycle_bench.industries.servicing import repair_parts as h1
from case_lifecycle_bench.outcomes import INTERFACE_ERRORS, interface_errors, variant_for_seed


def llm(case):
    return {**case, "customer_mode": "llm"}


def first(module, variant):
    return next(
        module.generate(seed)
        for seed in range(300, 340)
        if variant_for_seed(seed) == variant
    )


class Customer:
    """A stand-in for the simulated customer that records what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked = []

    def __call__(self, visit, dialogue):
        self.asked.append((visit, [dict(turn) for turn in dialogue]))
        return self.replies.pop(0)


def call_agent(replies_to_say, *, then=()):
    """Intake agent: ticket, call, verify, say each line, then run extra steps."""
    spoken = []

    def agent(session, call):
        if session["trigger_type"] != "case_intake":
            return None
        user_id, subject = session["user_id"], session["subject_id"]
        call("create_ticket", user_id=user_id, subject_id=subject, summary="x")
        started = call("start_outbound_contact", customer_id=user_id, channel="voice")
        contact_id = started["contact_id"]
        spoken.append(("start", started))
        spoken.append(("verify", call("verify_customer_identity", contact_id=contact_id)))
        for text in replies_to_say:
            spoken.append(("say", call("say_to_customer", contact_id=contact_id, text=text)))
        for step in then:
            spoken.append(step(call, contact_id, user_id, subject))
        return None

    return agent, spoken


class SynchronousCallTest(unittest.TestCase):
    def test_a_call_is_live_in_the_session_that_starts_it(self):
        case = first(l1, "wait")
        agent, spoken = call_agent([])

        run_case(case, agent)

        names = dict(spoken)
        self.assertEqual(names["start"]["status"], "answered")
        self.assertEqual(names["start"]["customer_says"], "你好，我接到了你们的来电。")
        self.assertEqual(names["verify"], {"verified": True})

    def test_the_call_cannot_be_used_from_another_session(self):
        case = first(l1, "wait")
        seen = {}

        def agent(session, call):
            if session["trigger_type"] == "case_intake":
                call("create_ticket", user_id=session["user_id"], subject_id=session["subject_id"], summary="x")
                seen["id"] = call("start_outbound_contact", customer_id=session["user_id"], channel="voice")["contact_id"]
                call(
                    "schedule_followup",
                    ticket_id="T1",
                    due_at="2026-01-06T10:00:00",
                    note="later",
                )
            elif session["trigger_type"] == "followup_due":
                seen["late"] = call("verify_customer_identity", contact_id=seen["id"])
            return None

        run_case(case, agent)

        self.assertEqual(seen["late"], {"error": "contact_not_active"})

    def test_the_scripted_customer_just_hears_the_agent(self):
        case = first(l1, "wait")
        agent, spoken = call_agent(["您好，请问什么时候能还款？"])

        run = run_case(case, agent, customer=Customer("不会被问到"))

        self.assertEqual(dict(spoken)["say"], {"delivered": True})
        said = [
            r
            for r in run["trajectory"]
            if r["kind"] == "message" and r["role"] == "user" and r["session_id"] == "S1"
        ]
        self.assertEqual(said, [], "a scripted customer says nothing back")


class ChatContactSayTest(unittest.TestCase):
    def test_say_to_customer_on_a_chat_contact_is_delivered_not_a_crash(self):
        from case_lifecycle_bench.industries.servicing import medical_clinic_cancel as m3

        case = {**m3.generate(301), "customer_mode": "llm"}
        said = []

        def agent(session, call):
            if session["trigger_type"] == "user_visit" and session.get("contact_id"):
                said.append(call("say_to_customer", contact_id=session["contact_id"], text="您好"))
                said.append(call("say_to_customer", contact_id="C999", text="x"))
            return None

        run = run_case(case, agent, customer=lambda visit, dialogue: "好的")

        self.assertEqual(said[0], {"delivered": True})
        self.assertEqual(said[1], {"error": "contact_not_active"})
        self.assertTrue(
            any(
                r["kind"] == "message" and r["role"] == "agent" and r["text"] == "您好"
                for r in run["trajectory"]
            )
        )


class SimulatedCustomerTest(unittest.TestCase):
    def test_the_customer_answers_what_the_agent_says_with_the_instance_persona(self):
        case = llm(first(l1, "wait"))
        customer = Customer("我会在1月7日16:00前还款。")
        agent, spoken = call_agent(["您好，请问您什么时候能还款？"])

        run = run_case(case, agent, customer=customer)

        self.assertEqual(
            dict(spoken)["say"], {"delivered": True, "customer_says": "我会在1月7日16:00前还款。"}
        )
        visit, dialogue = customer.asked[0]
        self.assertEqual(visit["goal"], case["init"]["script"]["contacts"][0]["persona"])
        self.assertEqual(
            dialogue,
            [{"customer": "你好，我接到了你们的来电。"}, {"agent": "您好，请问您什么时候能还款？"}],
        )
        spoken_rows = [
            (r["role"], r["text"])
            for r in run["trajectory"]
            if r["kind"] == "message" and r["session_id"] == "S1"
        ]
        self.assertEqual(
            spoken_rows,
            [("agent", "您好，请问您什么时候能还款？"), ("user", "我会在1月7日16:00前还款。")],
        )

    def test_a_terminal_token_hangs_up_the_call(self):
        case = llm(first(l1, "wait"))
        agent, spoken = call_agent(
            ["已记录您的还款时间。"],
            then=[
                lambda call, contact_id, user_id, subject: (
                    "again",
                    call("say_to_customer", contact_id=contact_id, text="还在吗？"),
                )
            ],
        )

        run_case(case, agent, customer=Customer("CONFIRMED"))

        self.assertEqual(dict(spoken)["say"], {"delivered": True, "call_ended": True})
        self.assertEqual(dict(spoken)["again"], {"error": "call_ended"})

    def test_the_second_call_uses_the_second_persona_and_later_calls_the_default(self):
        case = llm(first(l1, "wait"))
        script = case["init"]["script"]
        customer = Customer("好的", "好的", "好的")

        def agent(session, call):
            if session["trigger_type"] != "case_intake":
                return None
            call("create_ticket", user_id=session["user_id"], subject_id=session["subject_id"], summary="x")
            for channel in ("voice", "sms"):
                opened = call("start_outbound_contact", customer_id=session["user_id"], channel=channel)
                call("say_to_customer", contact_id=opened["contact_id"], text="您好")
            return None

        run_case(case, agent, customer=customer)

        goals = [visit["goal"] for visit, _ in customer.asked]
        self.assertEqual(goals[0], script["contacts"][0]["persona"])
        self.assertEqual(goals[1], script["contacts"][1]["persona"])

    def test_every_instance_defines_personas_for_its_calls(self):
        for module in (l1, h1):
            for seed in range(300, 310):
                script = module.generate(seed)["init"]["script"]
                with self.subTest(module=module.__name__, seed=seed):
                    self.assertTrue(script["default_contact"]["persona"])
                    self.assertTrue(all(row["persona"] for row in script["contacts"]))

    def test_the_repayment_persona_names_the_scripted_date_in_plain_words(self):
        row = l1.generate(300)["init"]["script"]["contacts"][0]
        moment = row["promise_by"]

        self.assertIn(f"{int(moment[5:7])}月{int(moment[8:10])}日{moment[11:16]}", row["persona"])


class IntentStaysPrivateTest(unittest.TestCase):
    def test_the_repayment_offer_is_only_in_the_result_for_a_scripted_customer(self):
        case = first(l1, "wait")
        seen = {}

        def agent(session, call):
            if session["trigger_type"] != "case_intake":
                return None
            call("create_ticket", user_id=session["user_id"], subject_id=session["subject_id"], summary="x")
            opened = call("start_outbound_contact", customer_id=session["user_id"], channel="voice")
            call("verify_customer_identity", contact_id=opened["contact_id"])
            seen["result"] = call("inform_overdue_status", contact_id=opened["contact_id"])
            return None

        run_case(case, agent)
        self.assertIn("commitment_offered_by", seen["result"])
        run_case(llm(case), agent)
        self.assertNotIn("commitment_offered_by", seen["result"])

    def test_the_slot_choice_is_only_in_the_result_for_a_scripted_customer(self):
        case = first(h1, "resolved")

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
            if session["trigger_type"] != "case_intake":
                return None
            call("create_ticket", user_id=session["user_id"], subject_id=session["subject_id"], summary="x")
            opened = call("start_outbound_contact", customer_id=session["user_id"], channel="voice")
            call("verify_customer_identity", contact_id=opened["contact_id"])
            call("inform_overdue_status", contact_id=opened["contact_id"])
            call("record_repayment_commitment", contact_id=opened["contact_id"], promised_by=written)
            return None

        run = run_case(llm(case), agent)
        return next(r["result"] for r in run["trajectory"] if r.get("tool") == "record_repayment_commitment")

    def test_the_date_may_be_written_with_a_space_and_still_counts(self):
        case = l1.generate(300)
        promised = case["init"]["script"]["contacts"][0]["promise_by"]

        self.assertEqual(self.record(case, promised), {"ok": True})
        self.assertEqual(self.record(case, promised.replace("T", " ")), {"ok": True})

    def test_a_different_moment_or_an_unreadable_date_is_refused(self):
        case = l1.generate(300)

        self.assertEqual(self.record(case, "2026-01-09T18:00:00"), {"error": "commitment_not_offered"})
        self.assertEqual(self.record(case, "下周三"), {"error": "invalid_time"})


class OracleReferenceTest(unittest.TestCase):
    def test_an_llm_mode_case_is_still_scored_against_the_scripted_oracle(self):
        # The scripted Oracle cannot hear the customer, so it only runs in script mode.
        for module, seed in ((l1, 301), (h1, 302)):
            case = module.generate(seed)
            scripted = evaluate_case(case, run_case(case, module.oracle))
            idle = evaluate_case(llm(case), run_case(llm(case), module.do_nothing))
            with self.subTest(module=module.__name__):
                self.assertEqual(idle["terminal"]["oracle"], {k: scripted["terminal"]["agent"][k] for k in idle["terminal"]["oracle"]})
                self.assertFalse(idle["metrics"]["case_resolution"])


class InterfaceErrorTest(unittest.TestCase):
    def test_only_misuse_counts_not_a_business_violation(self):
        trace = [
            {"kind": "tool_call", "result": {"error": "contact_not_active"}},
            {"kind": "tool_call", "result": {"error": "rolling_attempt_limit"}},
            {"kind": "tool_call", "result": {"ok": True}},
            {"kind": "message", "result": None},
            {"kind": "tool_call", "result": "T1"},
        ]

        self.assertEqual(interface_errors(trace), {"tool_calls": 4, "interface_errors": 1, "malformed": 1, "policy_refused": 1})
        self.assertNotIn("rolling_attempt_limit", INTERFACE_ERRORS)
        self.assertNotIn("part_not_ready", INTERFACE_ERRORS)

    def test_the_oracle_never_misuses_a_tool_in_any_template_or_instance(self):
        for name, workflow in WORKFLOWS.items():
            for seed in range(300, 320):
                case = workflow.generate(seed)
                for wake in ("poll", "push"):
                    with self.subTest(template=name, seed=seed, wake=wake):
                        evaluation = evaluate_case(
                            case, run_case(case, workflow.agents["oracle"], wake=wake)
                        )

                        self.assertEqual(evaluation["metrics"]["interface_errors"], 0)
                        self.assertGreater(evaluation["metrics"]["tool_calls"], 0)


if __name__ == "__main__":
    unittest.main()
