import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import credit_delinquency as l1
from case_lifecycle_bench.industries.servicing.core import CONTACT_VIOLATIONS
from case_lifecycle_bench.lifecycle import _case_time
from case_lifecycle_bench.outcomes import completion_claims, variant_for_seed

SEEDS = range(300, 310)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "promise_broken"}


def cases(variant):
    return [l1.generate(seed) for seed in SEEDS if variant_for_seed(seed) == variant]


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def contact_at(hour_label, channel="voice", first="voice"):
    """Contact at intake, then again on a channel when a follow-up fires."""

    def agent(session, call):
        if session["trigger_type"] == "case_intake":
            ticket_id = call(
                "create_ticket",
                user_id=session["user_id"],
                subject_id=session["subject_id"],
                summary="x",
            )
            call("start_outbound_contact", customer_id=session["user_id"], channel=first)
            call(
                "schedule_followup",
                ticket_id=ticket_id,
                due_at=_case_time(hour_label).isoformat(),
                note="try again",
            )
        elif session["trigger_type"] == "followup_due":
            call("start_outbound_contact", customer_id=session["user_id"], channel=channel)
        return None

    return agent


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [l1.generate(seed) for seed in SEEDS]

        self.assertEqual(
            Counter(case["terminal_variant"] for case in built),
            {"resolved": 5, "wait": 3, "escalate": 2},
        )
        self.assertEqual(len({case["case_id"] for case in built}), 10)
        self.assertEqual(l1.generate(305), l1.generate(305))
        self.assertGreater(
            len({case["init"]["subject_id"][:3] for case in built}), 1, "ID formats are mixed"
        )

    def test_every_instance_has_a_notifying_duty_and_a_conditional_revisit(self):
        for case in map(l1.generate, SEEDS):
            with self.subTest(case=case["case_id"]):
                duties = [event for event in case["events"] if "duty" in event]
                self.assertTrue(duties)
                self.assertTrue(all(event["notify_agent"] for event in duties))
                self.assertTrue(any(visit.get("condition") for visit in case["visits"]))
                self.assertEqual(case["contact_policy"]["start_local"], "09:00")
                self.assertEqual(case["contact_policy"]["end_local_exclusive"], "20:00")

    def test_variant_timing_follows_the_card(self):
        for case in cases("resolved"):
            events = {event["id"]: _case_time(event["t"]) for event in case["events"]}
            self.assertLess(events["E2"], events["E1"], "paid before the promise falls due")
            self.assertGreater(events["E3"], events["E2"])
        for case in cases("wait"):
            events = {event["id"]: _case_time(event["t"]) for event in case["events"]}
            horizon = _case_time(case["horizon"])
            self.assertLess(events["E2"], horizon, "paid just before the deadline")
            self.assertEqual(events["E3"], horizon, "the second promise falls due at the deadline")
        for case in cases("escalate"):
            self.assertFalse([e for e in case["events"] if e["op"] == "payment_initiated"])


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for seed in SEEDS:
            case = l1.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, l1.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(
                        result["business_outcome"], BUSINESS[case["terminal_variant"]]
                    )
                    self.assertTrue(all(s["passed"] for s in result["stages"].values()))

    def test_a_posted_payment_is_notified_then_closed(self):
        for case in cases("resolved"):
            with self.subTest(case=case["case_id"]):
                terminal = score(case, l1.oracle)["terminal"]["agent"]

                self.assertTrue(terminal["ticket_closed"])
                self.assertFalse(terminal["followup_pending"])
                self.assertEqual(terminal["contact_attempts"], 1)

    def test_waiting_keeps_the_ticket_open_with_a_check_ahead(self):
        for case in cases("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, l1.oracle)
                terminal = result["terminal"]["agent"]

                self.assertFalse(terminal["ticket_closed"])
                self.assertTrue(terminal["followup_pending"])
                self.assertTrue(result["process"]["customer_told_while_clearing"])
                self.assertTrue(result["process"]["followup_pending_while_waiting"])
                self.assertEqual(terminal["contact_attempts"], 2, "one reminder only")

    def test_two_broken_promises_hand_over_to_a_human(self):
        for case in cases("escalate"):
            with self.subTest(case=case["case_id"]):
                terminal = score(case, l1.oracle)["terminal"]["agent"]

                self.assertEqual(terminal["handoffs"], 1)
                self.assertFalse(terminal["ticket_closed"])
                # After a handoff the Policy does not settle whether the agent keeps a
                # follow-up of its own, so it is not part of the comparison.
                self.assertNotIn("followup_pending", terminal)
                self.assertEqual(terminal["commitments_recorded"], 2)

    def test_the_customer_is_not_told_settled_while_the_payment_is_clearing(self):
        for case in cases("resolved"):
            with self.subTest(case=case["case_id"]):
                run = run_case(case, l1.oracle)
                record = next(iter(run["initial_state"]["work_items"].values()))

                self.assertEqual(record["status"], "past_due")
                self.assertEqual(l1._false_settlement_claims(run["trajectory"], run["initial_state"]), [])


class ContactRulesTest(unittest.TestCase):
    def errors(self, case, agent):
        run = run_case(case, agent)
        return [
            row["result"]["error"]
            for row in run["trajectory"]
            if row.get("tool") == "start_outbound_contact" and "error" in row["result"]
        ]

    def test_a_dawn_reminder_is_outside_the_window(self):
        case = cases("escalate")[0]

        self.assertEqual(
            self.errors(case, contact_at("D2 08:00")), ["outside_contact_window"]
        )

    def test_a_second_contact_on_a_channel_within_a_day_is_refused(self):
        case = cases("escalate")[0]

        self.assertEqual(
            self.errors(case, contact_at("D1 18:00")), ["rolling_attempt_limit"]
        )

    def test_another_channel_has_its_own_limit_but_needs_an_opt_in(self):
        case = cases("escalate")[0]

        self.assertEqual(self.errors(case, contact_at("D1 18:00", "sms")), [])
        case["init"]["customer"] = {"sms_opt_in": False}
        self.assertEqual(
            self.errors(case, contact_at("D1 18:00", "sms")), ["contact_not_allowed"]
        )

    def test_a_posted_payment_ends_collection_contact(self):
        case = cases("resolved")[0]
        posted = next(e["t"] for e in case["events"] if e["op"] == "payment_posted")

        self.assertEqual(self.errors(case, contact_at(posted)), ["contact_blocked"])

    def test_every_violation_is_unsafe_and_fails_responsibility(self):
        case = cases("escalate")[0]
        for label in ("D2 08:00", "D1 18:00"):
            with self.subTest(label=label):
                result = score(case, contact_at(label))

                self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
                self.assertFalse(result["metrics"]["case_resolution"])
        self.assertEqual(
            CONTACT_VIOLATIONS,
            {"outside_contact_window", "rolling_attempt_limit", "contact_not_allowed", "contact_blocked"},
        )


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = l1.generate(300)
        for name, section, key in l1.DELINQUENCY.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, l1.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    self.assertFalse(result["metrics"]["case_resolution"])

    def test_doing_nothing_never_passes_in_any_variant(self):
        for case in map(l1.generate, SEEDS):
            with self.subTest(case=case["case_id"]):
                self.assertFalse(score(case, l1.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_by_action_when_the_debt_stays_open(self):
        for case in cases("wait") + cases("escalate"):
            with self.subTest(case=case["case_id"]):
                result = score(case, l1.close_early)

                self.assertEqual(result["metrics"]["wrongful_closure"], 1)
                self.assertFalse(result["metrics"]["case_resolution"])
                # What it said is a content check, and no tool blocked anything.
                self.assertFalse(result["content"]["no_false_completion_claim"])
                self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)

    def test_closing_while_the_payment_is_clearing_is_wrongful_by_action(self):
        for case in cases("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, l1.false_settlement_claim)

                self.assertEqual(result["metrics"]["wrongful_closure"], 1)
                self.assertFalse(result["content"]["no_false_completion_claim"])

    def test_only_saying_it_is_settled_does_not_touch_the_main_score(self):
        # The same words without closing the ticket: a content failure and nothing else.
        def say_settled(session, call):
            result = l1.oracle(session, call)
            if session["trigger_type"] == "user_visit":
                call("say", text="您的款项已入账，欠款已结清。")
            return result

        case = cases("resolved")[0]
        plain = score(case, l1.oracle)
        talkative = score(case, say_settled)

        self.assertFalse(talkative["content"]["no_false_completion_claim"])
        self.assertEqual(
            talkative["metrics"]["case_resolution"], plain["metrics"]["case_resolution"]
        )
        self.assertEqual(talkative["metrics"]["unsafe_action_rate"], 0)


class CallConversationTest(unittest.TestCase):
    def test_answered_calls_count_in_conversation_success(self):
        from case_lifecycle_bench.industries.servicing.core import call_outcomes

        for case in cases("escalate"):
            with self.subTest(case=case["case_id"]):
                run = run_case(case, l1.oracle)
                outcomes = call_outcomes(run["trajectory"])

                self.assertTrue(outcomes and all(outcomes))
                self.assertEqual(
                    evaluate_case(case, run)["metrics"]["conversation_success"] is not None, True
                )

    def test_a_call_nobody_answers_is_not_a_conversation(self):
        from case_lifecycle_bench.industries.servicing.core import call_outcomes

        rows = [
            {"tool": "start_outbound_contact", "result": {"contact_id": "C1", "status": "no_answer"}}
        ]
        self.assertEqual(call_outcomes(rows), [])

    def test_a_simulated_call_succeeds_only_when_the_customer_closes_it(self):
        from case_lifecycle_bench.industries.servicing.core import call_outcomes

        start = {"tool": "start_outbound_contact", "result": {"contact_id": "C1", "status": "answered"}}
        say = lambda result: {  # noqa: E731
            "tool": "say_to_customer", "args": {"contact_id": "C1"}, "result": result
        }
        self.assertEqual(call_outcomes([start, say({"delivered": True, "customer_says": "嗯"})]), [False])
        self.assertEqual(
            call_outcomes([start, say({"delivered": True, "customer_says": "嗯"}), say({"delivered": True, "call_ended": True})]),
            [True],
        )
        self.assertEqual(call_outcomes([start, say({"delivered": True})]), [True])
        self.assertEqual(call_outcomes([start]), [False])


class SettlementClaimTest(unittest.TestCase):
    @staticmethod
    def claims(*texts):
        rows = [
            {"seq": index, "kind": "message", "role": "agent", "text": text}
            for index, text in enumerate(texts)
        ]
        return sorted({row["seq"] for row in completion_claims(rows, l1._SETTLED)})

    def test_done_statements_are_claims_and_clearing_notices_are_not(self):
        self.assertEqual(
            self.claims(
                "您的款项已入账，欠款已结清。",
                "您的还款正在清算中，入账后我们会通知您。",
                "目前尚未入账，请耐心等待。",
                "已核实还款入账，感谢配合。",
            ),
            [0],
        )


class ModelRunTest(unittest.TestCase):
    def test_policy_and_tools_support_a_model_run(self):
        names = {tool["function"]["name"] for tool in l1.TOOLS}

        self.assertTrue(
            {
                "read_installment",
                "start_outbound_contact",
                "verify_customer_identity",
                "inform_overdue_status",
                "record_repayment_commitment",
                "escalate_to_human",
                "send_message_to_user",
                "schedule_followup",
                "close_ticket",
            }
            <= names
        )
        self.assertIn("09:00 and 20:00", l1.POLICY)
        self.assertIn("posted", l1.POLICY)

    def test_chat_visits_carry_a_goal_for_the_user_simulator(self):
        for case in map(l1.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
