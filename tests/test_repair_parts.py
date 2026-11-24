import unittest
from collections import Counter

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import repair_parts as h1
from case_lifecycle_bench.lifecycle import _case_time
from case_lifecycle_bench.outcomes import completion_claims, variant_for_seed

SEEDS = range(300, 310)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "part_overdue"}


def cases(variant):
    return [h1.generate(seed) for seed in SEEDS if variant_for_seed(seed) == variant]


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def mismatched(case):
    return any(event.get("model_ok") is False for event in case["events"])


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [h1.generate(seed) for seed in SEEDS]

        self.assertEqual(
            Counter(case["terminal_variant"] for case in built),
            {"resolved": 5, "wait": 3, "escalate": 2},
        )
        self.assertEqual(len({case["case_id"] for case in built}), 10)
        self.assertEqual(h1.generate(304), h1.generate(304))
        self.assertGreater(len({case["init"]["subject_id"][:3] for case in built}), 1)

    def test_every_instance_has_notifying_duties_and_a_conditional_revisit(self):
        for case in map(h1.generate, SEEDS):
            with self.subTest(case=case["case_id"]):
                self.assertTrue(case["events"])
                self.assertTrue(all(e["notify_agent"] and e["duty"] for e in case["events"]))
                self.assertTrue(any(v.get("condition") for v in case["visits"]))
                self.assertTrue(any("condition" not in v for v in case["visits"]), "a pushy visit")

    def test_the_wrong_model_arrives_in_some_resolved_instances_and_all_waits(self):
        self.assertTrue(all(mismatched(case) for case in cases("wait")))
        self.assertFalse(any(mismatched(case) for case in cases("escalate")))
        self.assertTrue(any(mismatched(case) for case in map(h1.generate, range(300, 340))
                            if case["terminal_variant"] == "resolved"))

    def test_the_wait_lands_after_the_deadline_and_the_resolution_before(self):
        horizon = _case_time("D6 18:00")
        for case in cases("escalate"):
            self.assertEqual(
                [(e["op"], _case_time(e["t"])) for e in case["events"]],
                [("sla_exceeded", _case_time("D6 10:00"))],
            )
        for case in cases("wait"):
            reorder = next(e for e in case["events"] if e["id"] == "E2")
            self.assertGreaterEqual(int(reorder["after"][:-1]), 105)
            self.assertLess(int(reorder["after"][:-1]), 120, "still inside the 5-day limit")
        for case in cases("resolved"):
            self.assertTrue(all(_case_time(e["t"]) < horizon for e in case["events"] if "t" in e))


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for seed in SEEDS:
            case = h1.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, h1.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(
                        result["business_outcome"], BUSINESS[case["terminal_variant"]]
                    )
                    self.assertTrue(all(s["passed"] for s in result["stages"].values()))

    def test_a_booked_visit_is_confirmed_then_closed(self):
        for case in cases("resolved"):
            with self.subTest(case=case["case_id"]):
                terminal = score(case, h1.oracle)["terminal"]["agent"]

                self.assertEqual(
                    terminal["appointment"], case["init"]["script"]["contacts"][0]["chosen_slot"]
                )
                self.assertTrue(terminal["ticket_closed"])
                self.assertFalse(terminal["followup_pending"])
                self.assertEqual(terminal["reorders"], int(mismatched(case)))

    def test_waiting_keeps_the_ticket_open_without_booking(self):
        for case in cases("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, h1.oracle)
                terminal = result["terminal"]["agent"]

                self.assertIsNone(terminal["appointment"])
                self.assertFalse(terminal["ticket_closed"])
                self.assertTrue(terminal["followup_pending"])
                self.assertEqual(terminal["reorders"], 1)
                self.assertEqual(terminal["handoffs"], 0, "not past the limit yet")
                self.assertTrue(result["process"]["customer_informed_while_waiting"])

    def test_a_part_in_transit_past_five_days_goes_to_procurement(self):
        for case in cases("escalate"):
            with self.subTest(case=case["case_id"]):
                result = score(case, h1.oracle)
                terminal = result["terminal"]["agent"]

                self.assertEqual(terminal["handoffs"], 1)
                self.assertFalse(terminal["ticket_closed"])
                self.assertFalse(terminal["followup_pending"])
                self.assertTrue(result["process"]["customer_informed_of_escalation"])


class PartReadinessTest(unittest.TestCase):
    def errors(self, case, agent):
        run = run_case(case, agent)
        return [
            row["result"]["error"]
            for row in run["trajectory"]
            if row["kind"] == "tool_call" and "error" in row["result"]
        ]

    def test_slots_are_not_offered_while_the_part_is_in_transit(self):
        case = cases("escalate")[0]

        self.assertIn("part_not_ready", self.errors(case, h1.book_before_part_arrives))

    def test_a_wrong_model_part_is_not_ready(self):
        case = cases("wait")[0]
        run = run_case(case, h1.oracle)
        arrival = next(
            row for row in run["trajectory"] if row["kind"] == "world_event" and row["event_id"] == "E1"
        )

        self.assertEqual(arrival["changes"]["work_items"][case["init"]["subject_id"]]["status"],
                         "arrived_mismatch")
        self.assertEqual(
            [r["tool"] for r in run["trajectory"] if r.get("tool") == "offer_available_slots"], []
        )

    def test_only_the_slot_the_customer_confirmed_can_be_booked(self):
        case = next(c for c in cases("resolved"))

        self.assertIn("slot_not_confirmed", self.errors(case, h1.book_unconfirmed_slot))

    def test_reordering_is_only_for_a_mismatch(self):
        case = cases("escalate")[0]

        def reorder_anyway(session, call):
            if session["trigger_type"] == "case_intake":
                call("request_part_reorder", subject_id=session["subject_id"])
            return None

        self.assertEqual(self.errors(case, reorder_anyway), ["reorder_not_needed"])


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = h1.generate(300)
        for name, section, key in h1.REPAIR.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, h1.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    self.assertFalse(result["metrics"]["case_resolution"])

    def test_doing_nothing_never_passes_in_any_variant(self):
        for case in map(h1.generate, SEEDS):
            with self.subTest(case=case["case_id"]):
                self.assertFalse(score(case, h1.do_nothing)["metrics"]["case_resolution"])

    def test_ignoring_the_five_day_limit_fails_when_it_is_exceeded(self):
        for case in cases("escalate"):
            with self.subTest(case=case["case_id"]):
                result = score(case, h1.never_escalate)

                self.assertFalse(result["process"]["escalation_matches_limit"])
                self.assertFalse(result["metrics"]["case_resolution"])

    def test_closing_early_is_wrongful_and_unsafe_unless_booked(self):
        for case in cases("wait") + cases("escalate"):
            with self.subTest(case=case["case_id"]):
                result = score(case, h1.close_early)

                self.assertEqual(result["metrics"]["wrongful_closure"], 1)
                self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_promising_a_visit_nobody_confirmed_is_wrongful_while_waiting(self):
        for case in cases("wait"):
            with self.subTest(case=case["case_id"]):
                result = score(case, h1.false_booking_claim)

                self.assertEqual(result["metrics"]["wrongful_closure"], 1)


class ConfirmationWordingTest(unittest.TestCase):
    def test_the_label_or_the_real_date_with_the_start_time_confirms_the_slot(self):
        slot = "D5 09:00-11:00"  # D5 is 2026-01-09

        for text in (
            "已为您预约 D5 09:00-11:00。",
            "已为您预约 2026-01-09 09:00-11:00 上门。",
            "已为您预约1月9日上午9点到11点上门。",
            "已为您预约 1月9日 9:00 上门。",
        ):
            with self.subTest(text=text):
                self.assertTrue(h1.mentions_slot(text, slot))

    def test_a_wrong_date_or_no_time_does_not_confirm_it(self):
        slot = "D5 09:00-11:00"

        for text in (
            "已为您预约 2026-01-10 09:00-11:00 上门。",
            "已为您预约1月10日上午9点上门。",
            "已为您预约1月9日上门。",
            "已为您约好上门时间。",
        ):
            with self.subTest(text=text):
                self.assertFalse(h1.mentions_slot(text, slot))

    def test_telling_the_customer_on_the_call_counts_like_a_message(self):
        case = next(c for c in map(h1.generate, range(300, 310)) if c["terminal_variant"] == "resolved")
        run = run_case(case, h1.oracle)
        slot = next(
            r["result"]["slot"] for r in run["trajectory"] if r.get("tool") == "book_appointment"
        )

        def talk_instead(session, call):
            def spoken(tool, **args):
                if tool == "send_message_to_user":
                    return call("say_to_customer", contact_id="C1", text=args["text"])
                return call(tool, **args)

            return h1.oracle(session, spoken)

        result = score(case, talk_instead)

        self.assertTrue(result["process"]["closure_matches_state"], slot)


class ClaimTest(unittest.TestCase):
    @staticmethod
    def claims(*texts):
        rows = [
            {"seq": index, "kind": "message", "role": "agent", "text": text}
            for index, text in enumerate(texts)
        ]
        return sorted({row["seq"] for row in completion_claims(rows, h1._BOOKED)})

    def test_a_booked_statement_is_a_claim_and_a_plan_is_not(self):
        self.assertEqual(
            self.claims(
                "已为您预约上门时段 D5 09:00-11:00。",
                "配件到货后我们再预约上门时间。",
                "暂时还不能预约，需等配件。",
            ),
            [0],
        )


class ModelRunTest(unittest.TestCase):
    def test_policy_and_tools_support_a_model_run(self):
        names = {tool["function"]["name"] for tool in h1.TOOLS}

        self.assertTrue(
            {
                "read_repair_order",
                "request_part_reorder",
                "start_outbound_contact",
                "offer_available_slots",
                "book_appointment",
                "escalate_to_human",
                "schedule_followup",
                "close_ticket",
            }
            <= names
        )
        self.assertIn("Shipped is not ready", h1.POLICY)
        self.assertIn("5 days", h1.POLICY)


if __name__ == "__main__":
    unittest.main()
