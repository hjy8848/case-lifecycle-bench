import unittest
from collections import Counter
from datetime import datetime

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import insurance_renewal as i1
from case_lifecycle_bench.lifecycle import _case_time
from case_lifecycle_bench.outcomes import completion_claims, variant_for_seed

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "lapse_imminent"}


def score(case, agent, wake="poll", customer=None):
    return evaluate_case(case, run_case(case, agent, wake=wake, customer=customer))


def cases(variant, seeds=MANY):
    return [i1.generate(seed) for seed in seeds if variant_for_seed(seed) == variant]


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [i1.generate(seed) for seed in SEEDS]

        self.assertEqual(
            Counter(case["terminal_variant"] for case in built),
            {"resolved": 5, "wait": 3, "escalate": 2},
        )
        self.assertEqual(len({case["case_id"] for case in built}), 10)
        self.assertEqual(i1.generate(305), i1.generate(305))
        self.assertGreater(len({case["init"]["subject_id"][:3] for case in built}), 1)

    def test_events_notify_and_a_revisit_is_conditional_and_inside_the_horizon(self):
        for seed in MANY:
            case = i1.generate(seed)
            horizon = _case_time(case["horizon"])
            with self.subTest(seed=seed):
                self.assertTrue(all(e["notify_agent"] for e in case["events"]))
                self.assertTrue(any(v.get("condition") for v in case["visits"]))
                self.assertTrue(
                    all(_case_time(r["t"]) <= horizon for r in [*case["events"], *case["visits"]])
                )

    def test_the_variants_follow_the_expiry_rule(self):
        horizon = _case_time("D6 18:00")
        for case in cases("escalate"):
            expiry = datetime.fromisoformat(case["init"]["expires_at"])
            # Less than 24 hours remain at the deadline, and nothing in the world says so.
            self.assertLess((expiry - horizon).total_seconds(), 24 * 3600)
            self.assertFalse([e for e in case["events"] if e["op"] == "lapse_window"])
        for case in cases("wait") + cases("resolved"):
            self.assertGreaterEqual(
                (datetime.fromisoformat(case["init"]["expires_at"]) - horizon).total_seconds(),
                48 * 3600,
            )


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for seed in MANY:
            case = i1.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, i1.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])
                    self.assertEqual(result["metrics"]["interface_errors"], 0)

    def test_a_consented_renewal_is_confirmed_notified_then_closed(self):
        for case in cases("resolved"):
            with self.subTest(case=case["case_id"]):
                terminal = score(case, i1.oracle)["terminal"]["agent"]

                self.assertTrue(terminal["renewal_confirmed"])
                self.assertTrue(terminal["ticket_closed"])
                self.assertFalse(terminal["followup_pending"])
                self.assertEqual(terminal["handoffs"], 0)

    def test_a_revised_quote_needs_fresh_consent_so_the_version_is_the_new_one(self):
        revised = [
            case for case in cases("resolved") if case["init"]["script"]["contacts"][0]["revises_after_consent"]
        ]
        self.assertTrue(revised)
        for case in revised:
            terminal = score(case, i1.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["accepted_quote_version"], "Q3")

    def test_waiting_keeps_the_ticket_open_and_a_check_ahead(self):
        for case in cases("wait"):
            with self.subTest(case=case["case_id"]):
                terminal = score(case, i1.oracle)["terminal"]["agent"]

                self.assertFalse(terminal["ticket_closed"])
                self.assertTrue(terminal["followup_pending"])
                self.assertEqual(terminal["handoffs"], 0)

    def test_close_to_expiry_without_consent_warns_then_hands_over(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                with self.subTest(case=case["case_id"], wake=wake):
                    result = score(case, i1.oracle, wake)

                    self.assertEqual(result["terminal"]["agent"]["handoffs"], 1)
                    self.assertFalse(result["terminal"]["agent"]["ticket_closed"])
                    self.assertTrue(result["process"]["lapse_warned_before_handoff"])


class ContactRulesTest(unittest.TestCase):
    def test_a_confirmed_renewal_ends_voice_contact(self):
        case = cases("resolved")[0]
        run = run_case(case, i1.oracle)
        result = next(
            r for r in run["trajectory"] if r.get("tool") == "start_outbound_contact"
        )
        self.assertEqual(result["result"]["status"], "answered")

        def late(session, call):
            if session["trigger_type"] == "case_intake":
                i1.oracle(session, call)
            elif session["trigger_type"] == "followup_due":
                call("start_outbound_contact", customer_id=session["user_id"], channel="voice")
            return None

        # Contacting again after confirmation is blocked by the system.
        self.assertTrue(i1.RenewalEnvironment(case, "poll")._contact_block("voice") is False)


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = i1.generate(300)
        for name, section, key in i1.RENEWAL.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, i1.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    if section == "process":
                        self.assertFalse(result["metrics"]["case_resolution"])

    def test_doing_nothing_never_passes(self):
        for case in map(i1.generate, SEEDS):
            self.assertFalse(score(case, i1.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_the_renewal_is_confirmed(self):
        for case in cases("wait") + cases("escalate"):
            with self.subTest(case=case["case_id"]):
                result = score(case, i1.close_early)

                self.assertEqual(result["metrics"]["wrongful_closure"], 1)
                self.assertFalse(result["metrics"]["case_resolution"])
                self.assertFalse(result["content"]["no_false_completion_claim"])

    def test_never_escalating_fails_when_the_policy_is_close_to_expiry(self):
        for case in cases("escalate"):
            with self.subTest(case=case["case_id"]):
                result = score(case, i1.never_escalate)

                self.assertFalse(result["process"]["escalation_matches_expiry"])
                self.assertFalse(result["metrics"]["case_resolution"])

    def test_submitting_the_old_version_after_a_revision_is_refused_and_unsafe(self):
        for case in [c for c in cases("resolved") if c["init"]["script"]["contacts"][0]["revises_after_consent"]]:
            with self.subTest(case=case["case_id"]):
                result = score(case, i1.stale_consent)

                self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
                self.assertFalse(result["metrics"]["case_resolution"])

    def test_a_false_renewal_claim_is_a_content_failure_only(self):
        case = cases("wait")[0]
        result = score(case, i1.false_renewal_claim)

        self.assertFalse(result["content"]["no_false_completion_claim"])
        self.assertEqual(result["metrics"]["wrongful_closure"], 0)


class SimulatedCustomerTest(unittest.TestCase):
    def test_the_simulator_decides_and_the_consent_tool_checks_it(self):
        case = {**cases("resolved")[0], "customer_mode": "llm"}
        asked = []

        def customer(visit, dialogue):
            asked.append((visit["goal"], dialogue[-1]))
            return "I agree to renew on this version."

        seen = {}

        def agent(session, call):
            if session["trigger_type"] != "case_intake":
                return None
            subject = {"policy_id": session["policy_id"]}
            call("create_ticket", user_id=session["user_id"], summary="x", **subject)
            opened = call("start_outbound_contact", customer_id=session["user_id"], channel="voice")
            call("verify_customer_identity", contact_id=opened["contact_id"])
            seen["quote"] = call("read_renewal_quote", **subject)
            seen["present"] = call(
                "present_renewal_quote", contact_id=opened["contact_id"], quote_version="Q2"
            )
            return None

        run_case(case, agent, customer=customer)

        # Before E1 the quote is not ready, so presenting it is refused rather than voiced.
        self.assertEqual(seen["present"], {"error": "quote_not_current"})
        self.assertEqual(asked, [])

    def test_a_simulated_caller_answers_a_presented_quote_in_the_result_not_as_a_hidden_flag(self):
        case = {**cases("resolved")[0], "customer_mode": "llm"}
        env = i1.RenewalEnvironment(case, "poll", customer=lambda visit, dialogue: "OK, I agree.")
        record = env.state["work_items"][env.subject_id]
        env.state["work_items"][env.subject_id] = {
            **record,
            "quote": {"status": "ready", "version": "Q2", "premium": 1000},
        }
        persona = case["init"]["script"]["contacts"][0]
        env.contact_scripts["C1"] = persona
        env.dialogues["C1"] = [{"customer": "Hello"}]
        contact = {
            "contact_id": "C1",
            "customer_id": env.user_id,
            "channel": "voice",
            "identity_verified": True,
        }

        result = env._contact_tool("present_renewal_quote", contact, {"quote_version": "Q2"})[0]

        self.assertEqual(result["customer_says"], "OK, I agree.")
        self.assertNotIn("customer_accepts", result)


class ClaimTest(unittest.TestCase):
    @staticmethod
    def kinds(*texts):
        rows = [
            {"seq": index, "kind": "message", "role": "agent", "text": text}
            for index, text in enumerate(texts)
        ]
        return [row["seq"] for row in completion_claims(rows, i1._RENEWED)]

    def test_done_statements_are_claims_and_waiting_statements_are_not(self):
        self.assertEqual(self.kinds("Your renewal was successful.", "The renewal is not confirmed yet; we will tell you once it is confirmed."), [0])


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {tool["function"]["name"] for tool in i1.TOOLS}

        self.assertTrue(
            {
                "read_policy",
                "read_renewal_quote",
                "start_outbound_contact",
                "record_quote_consent",
                "submit_renewal",
                "escalate_to_human",
            }
            <= names
        )
        self.assertIn("09:00 and 18:00", i1.POLICY)
        self.assertIn("less than 24 hours", i1.POLICY)
        for case in map(i1.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
