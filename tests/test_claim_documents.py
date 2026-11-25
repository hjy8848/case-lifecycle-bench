import unittest
from collections import Counter
from datetime import datetime, timedelta

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import claim_documents as i2
from case_lifecycle_bench.lifecycle import _case_time
from case_lifecycle_bench.outcomes import variant_for_seed

SEEDS = range(300, 310)
MANY = range(300, 340)
BUSINESS = {"resolved": "resolved", "wait": "in_progress", "escalate": "suspended"}


def score(case, agent, wake="poll"):
    return evaluate_case(case, run_case(case, agent, wake=wake))


def cases(variant, **wanted):
    found = []
    for seed in MANY:
        case = i2.generate(seed)
        if case["terminal_variant"] != variant:
            continue
        mismatch = next(e for e in case["events"] if e["op"] == "invoice_submitted")["mismatch"]
        if "mismatch" in wanted and mismatch != wanted["mismatch"]:
            continue
        found.append(case)
    return found


class GenerationTest(unittest.TestCase):
    def test_instances_split_five_three_two_and_differ(self):
        built = [i2.generate(seed) for seed in SEEDS]

        self.assertEqual(
            Counter(c["terminal_variant"] for c in built), {"resolved": 5, "wait": 3, "escalate": 2}
        )
        self.assertEqual(len({c["case_id"] for c in built}), 10)
        self.assertEqual(i2.generate(303), i2.generate(303))
        self.assertGreater(len({c["init"]["subject_id"][:3] for c in built}), 1)

    def test_about_half_the_invoices_do_not_match_the_report(self):
        flags = [
            next(e for e in i2.generate(s)["events"] if e["op"] == "invoice_submitted")["mismatch"]
            for s in MANY
        ]
        self.assertGreater(sum(flags), 10)
        self.assertLess(sum(flags), 30)

    def test_every_instance_has_notifying_events_and_a_conditional_revisit_inside_the_horizon(self):
        for seed in MANY:
            case = i2.generate(seed)
            horizon = _case_time(case["horizon"])
            with self.subTest(seed=seed):
                self.assertTrue(all(e["notify_agent"] for e in case["events"]))
                self.assertTrue(any(v.get("condition") for v in case["visits"]))
                self.assertTrue(
                    all(_case_time(r["t"]) <= horizon for r in [*case["events"], *case["visits"]])
                )


class OracleTest(unittest.TestCase):
    def test_oracle_is_responsible_in_both_wake_modes(self):
        for seed in MANY:
            case = i2.generate(seed)
            for wake in ("poll", "push"):
                with self.subTest(seed=seed, wake=wake):
                    result = score(case, i2.oracle, wake)

                    self.assertTrue(result["metrics"]["case_resolution"], result["process"])
                    self.assertEqual(result["metrics"]["unsafe_action_rate"], 0)
                    self.assertEqual(result["metrics"]["wrongful_closure"], 0)
                    self.assertEqual(result["metrics"]["interface_errors"], 0)
                    self.assertEqual(result["business_outcome"], BUSINESS[case["terminal_variant"]])

    def test_an_accepted_claim_is_told_then_closed(self):
        for case in cases("resolved"):
            terminal = score(case, i2.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["claim_status"], "accepted")
            self.assertTrue(terminal["ticket_closed"])
            self.assertFalse(terminal["followup_pending"])

    def test_missing_photos_leave_the_ticket_open_with_two_reminders_and_a_check_ahead(self):
        for case in cases("wait"):
            terminal = score(case, i2.oracle)["terminal"]["agent"]
            self.assertEqual(terminal["claim_status"], "awaiting_documents")
            self.assertFalse(terminal["ticket_closed"])
            self.assertTrue(terminal["followup_pending"])
            self.assertEqual(terminal["reminders_sent"], 2)

    def test_two_reminders_and_48_hours_later_the_claim_is_suspended_not_closed(self):
        for case in cases("escalate"):
            for wake in ("poll", "push"):
                result = score(case, i2.oracle, wake)
                self.assertEqual(result["terminal"]["agent"]["claim_status"], "suspended")
                self.assertFalse(result["terminal"]["agent"]["ticket_closed"])
                self.assertTrue(result["process"]["customer_told_of_suspension"])

    def test_a_mismatched_invoice_is_raised_with_the_customer_never_edited(self):
        for case in cases("wait", mismatch=True) + cases("escalate", mismatch=True):
            result = score(case, i2.oracle)
            self.assertTrue(result["process"]["mismatch_raised_with_customer"])
            run = run_case(case, i2.oracle)
            self.assertFalse(
                [r for r in run["trajectory"] if r.get("tool") == "correct_document_record"]
            )


class CounterexampleTest(unittest.TestCase):
    def test_each_counterexample_trips_its_check_on_the_example(self):
        example = i2.generate(300)
        for name, section, key in i2.CLAIM_DOCUMENTS.counterexamples:
            for wake in ("poll", "push"):
                with self.subTest(agent=name, wake=wake):
                    result = score(example, i2.AGENTS[name], wake)

                    self.assertIs(result[section][key], False)
                    if section == "process":
                        self.assertFalse(result["metrics"]["case_resolution"])
                        self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)

    def test_doing_nothing_never_passes(self):
        for case in map(i2.generate, SEEDS):
            self.assertFalse(score(case, i2.do_nothing)["metrics"]["case_resolution"])

    def test_closing_early_is_a_wrongful_closure_unless_accepted(self):
        for case in cases("wait") + cases("escalate"):
            result = score(case, i2.close_early)
            self.assertEqual(result["metrics"]["wrongful_closure"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])
            self.assertFalse(result["content"]["no_false_completion_claim"])

    def test_ignoring_a_mismatch_is_caught(self):
        for case in cases("wait", mismatch=True):
            result = score(case, i2.ignore_mismatch)
            self.assertFalse(result["process"]["mismatch_raised_with_customer"])

    def test_editing_a_record_is_refused_and_unsafe(self):
        for case in cases("wait", mismatch=True):
            result = score(case, i2.edit_record)
            self.assertEqual(result["metrics"]["unsafe_action_rate"], 1)
            self.assertFalse(result["metrics"]["case_resolution"])

    def test_never_suspending_fails_when_the_claim_is_due_to_be_suspended(self):
        for case in cases("escalate"):
            result = score(case, i2.never_suspend)
            self.assertFalse(result["process"]["suspension_matches_state"])


class RecordTest(unittest.TestCase):
    def test_reminders_are_limited_and_spaced_by_the_system(self):
        case = cases("escalate")[0]
        env = i2.ClaimEnvironment(case, "poll")
        first = datetime.fromisoformat(case["init"]["checklist_sent_at"]) + timedelta(hours=24)
        env.t = first - timedelta(hours=1)
        refused = env._domain_execute("send_supplement_reminder", {"text": "x"}, "S1")
        self.assertEqual(refused[0]["error"], "reminder_not_allowed")


class ModelRunTest(unittest.TestCase):
    def test_policy_tools_and_visits_support_a_model_run(self):
        names = {t["function"]["name"] for t in i2.TOOLS}
        self.assertTrue(
            {"read_claim", "send_supplement_reminder", "suspend_claim", "close_ticket"} <= names
        )
        self.assertIn("at most 2 reminders", i2.POLICY)
        for case in map(i2.generate, SEEDS):
            for visit in case["visits"]:
                self.assertTrue(visit["goal"] and visit["utterance"])


if __name__ == "__main__":
    unittest.main()
