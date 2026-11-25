import json
import unittest

from case_lifecycle_bench.case_ledger import LedgerConfig, PromiseBook, ledger_policy, tool_kind
from case_lifecycle_bench.case_ledger import checks, scorekeeper
from case_lifecycle_bench.case_ledger.tools import subject_of, touches
from case_lifecycle_bench.model_agent import _numbered, model_agent


class Env:
    """A fake environment: reads return a dict, writes can time out."""

    def __init__(self, timeouts=()):
        self.timeouts = set(timeouts)
        self.calls = []

    def __call__(self, tool, **args):
        self.calls.append((tool, args))
        if tool in self.timeouts:
            self.timeouts.discard(tool)
            return {"error": "timeout"}
        if tool == "schedule_followup":
            return f"F{sum(1 for t, _ in self.calls if t == 'schedule_followup')}"
        return {"ok": True}


def wake(book, env, script, t="2026-01-05T10:00:00", **session):
    """Run one wake: `script(call)` plays the agent. Returns what it returned."""
    holder = {}

    def agent(shown, call):
        holder["session"] = shown
        return script(call)

    return book.wrap(agent)({"t": t, "trigger_type": "followup_due", "trigger_id": "F0", **session}, env), holder


class ToolKindTest(unittest.TestCase):
    def test_default_rules(self):
        self.assertEqual(tool_kind("read_claim"), "read")
        self.assertEqual(tool_kind("search_tickets"), "read")
        self.assertEqual(tool_kind("escalate_to_human"), "handoff")
        self.assertEqual(tool_kind("say"), "message")
        self.assertEqual(tool_kind("send_message_to_user"), "message")
        self.assertEqual(tool_kind("issue_refund"), "write")
        self.assertEqual(tool_kind("end_session"), "control")

    def test_subject_is_the_first_known_key(self):
        self.assertEqual(subject_of({"order_id": "O1", "ticket_id": "T1"}), "O1")
        self.assertIsNone(subject_of({"text": "x"}))
        self.assertEqual(touches({"order_id": "O1", "ticket_id": "T1"}), {"O1", "T1"})


class CheckATest(unittest.TestCase):
    def test_a_write_after_a_timeout_is_refused_until_the_object_is_read(self):
        book, env = PromiseBook(LedgerConfig.full()), Env(timeouts={"pay_claim"})

        def agent(call):
            call("pay_claim", claim_id="C1")
            refused = call("pay_claim", claim_id="C1")
            self.assertEqual(refused["error"], "reconcile_first")
            self.assertIn("W1.1 pay_claim on C1", refused["uncertain"][0])
            call("read_claim", claim_id="C1")
            self.assertEqual(call("pay_claim", claim_id="C1"), {"ok": True})

        wake(book, env, agent)
        self.assertEqual(sum(1 for t, _ in env.calls if t == "pay_claim"), 2)  # the refused call never reached it

    def test_an_explicit_failure_is_not_uncertain(self):
        book, env = PromiseBook(LedgerConfig.full()), Env()
        env.__class__ = type("E", (Env,), {"__call__": lambda self, tool, **a: {"error": "service_unavailable"}})

        def agent(call):
            call("pay_claim", claim_id="C1")
            self.assertEqual(call("pay_claim", claim_id="C1"), {"error": "service_unavailable"})

        wake(book, env, agent)
        self.assertEqual(book.rejections, [])

    def test_a_read_in_an_earlier_wake_does_not_reconcile(self):
        book, env = PromiseBook(LedgerConfig.full()), Env(timeouts={"pay_claim"})
        wake(book, env, lambda call: (call("read_claim", claim_id="C1"), call("pay_claim", claim_id="C1")))
        seen = {}

        def second(call):
            seen["r"] = call("pay_claim", claim_id="C1")

        wake(book, env, second, t="2026-01-06T10:00:00")
        self.assertEqual(seen["r"]["error"], "reconcile_first")

    def test_record_only_logs_but_never_refuses(self):
        book, env = PromiseBook(LedgerConfig.full().recording()), Env(timeouts={"pay_claim"})

        def agent(call):
            call("pay_claim", claim_id="C1")
            self.assertEqual(call("pay_claim", claim_id="C1"), {"ok": True})

        wake(book, env, agent)
        self.assertEqual([row["check"] for row in book.would_reject], ["A"])
        self.assertEqual(book.rejections, [])


class CheckBTest(unittest.TestCase):
    def setUp(self):
        self.book, self.env = PromiseBook(LedgerConfig.full(), policy="Never promise a payout amount."), Env()

    def promise(self, call, **extra):
        return call("add_promise", to="customer", what="check the claim", done_when="claim accepted", subject="C1", **extra)["promise_id"]

    def test_done_needs_a_fresh_read_of_the_object_after_its_last_write(self):
        def agent(call):
            pid = self.promise(call, due_by="2026-01-06T10:00:00")
            call("pay_claim", claim_id="C1")  # W1.2
            self.assertEqual(call("settle_promise", promise_id=pid, how="done", evidence="W1.2")["error"], "evidence_not_a_read")
            call("read_claim", claim_id="C1")  # W1.4
            self.assertEqual(call("settle_promise", promise_id=pid, how="done", evidence="W1.4"), {"ok": True})

        wake(self.book, self.env, agent)

    def test_a_read_from_before_the_last_write_or_another_object_is_not_fresh(self):
        def agent(call):
            pid = self.promise(call)
            call("read_claim", claim_id="C1")  # W1.2
            call("pay_claim", claim_id="C1")  # W1.3
            self.assertEqual(call("settle_promise", promise_id=pid, how="done", evidence="W1.2")["error"], "evidence_not_fresh")
            call("read_claim", claim_id="C2")  # W1.5
            self.assertEqual(call("settle_promise", promise_id=pid, how="done", evidence="W1.5")["error"], "evidence_not_fresh")

        wake(self.book, self.env, agent)

    def test_a_read_from_an_earlier_wake_is_not_fresh(self):
        wake(self.book, self.env, lambda call: (self.promise(call), call("read_claim", claim_id="C1")))

        def agent(call):
            result = call("settle_promise", promise_id="P1", how="done", evidence="W1.2")
            self.assertEqual(result["error"], "evidence_not_fresh")

        wake(self.book, self.env, agent, t="2026-01-06T10:00:00")

    def test_handed_over_needs_a_successful_handoff_from_any_wake(self):
        def first(call):
            self.promise(call)
            call("escalate_to_human", ticket_id="T1", note="x")  # W1.2

        wake(self.book, self.env, first)

        def second(call):
            self.assertEqual(call("settle_promise", promise_id="P1", how="handed_over", evidence="W1.1")["error"], "evidence_not_a_handoff")
            self.assertEqual(call("settle_promise", promise_id="P1", how="handed_over", evidence="W1.2"), {"ok": True})

        wake(self.book, self.env, second, t="2026-01-06T10:00:00")

    def test_released_needs_a_quote_from_the_customer_or_the_policy(self):
        def agent(call):
            pid = self.promise(call)
            self.assertEqual(call("settle_promise", promise_id=pid, how="released", evidence="please")["error"], "evidence_not_found")
            self.assertEqual(call("settle_promise", promise_id=pid, how="released", evidence="I never asked for that")["error"], "evidence_not_found")
            self.assertEqual(call("settle_promise", promise_id=pid, how="released", evidence="cancel it, I changed my mind"), {"ok": True})

        wake(self.book, self.env, agent, message="Please cancel it, I changed my mind.")

    def test_the_todo_group_settles_without_evidence(self):
        book = PromiseBook(LedgerConfig.todo())

        def agent(call):
            pid = self.promise(call)
            self.assertEqual(call("settle_promise", promise_id=pid, how="done"), {"ok": True})

        wake(book, self.env, agent)


class CheckCTest(unittest.TestCase):
    def test_the_session_cannot_end_with_a_promise_nobody_will_wake_and_gives_in_on_the_third_try(self):
        book, env = PromiseBook(LedgerConfig.full()), Env()
        answers = []

        def agent(call):
            call("add_promise", to="customer", what="call back", done_when="called", due_by="2026-01-06T10:00:00")
            answers.extend(call("end_session") for _ in range(3))

        wake(book, env, agent)
        self.assertEqual([a.get("error") for a in answers], ["ledger_unbalanced", "ledger_unbalanced", None])
        self.assertEqual(book.stats["unbalanced_at_end"], 1)
        self.assertEqual(len(book.rejections), 2)

    def test_a_follow_up_due_in_time_balances_the_book_but_a_late_one_does_not(self):
        book, env = PromiseBook(LedgerConfig.full()), Env()
        seen = []

        def agent(call):
            call("add_promise", to="customer", what="check", done_when="d", due_by="2026-01-06T10:00:00")
            call("schedule_followup", ticket_id="T1", due_at="2026-01-08T10:00:00", note="x")
            seen.append(call("end_session").get("error"))
            call("schedule_followup", ticket_id="T1", due_at="2026-01-06T09:00:00", note="x")
            seen.append(call("end_session").get("error"))

        wake(book, env, agent)
        self.assertEqual(seen, ["ledger_unbalanced", None])

    def test_waiting_for_the_customer_needs_a_message_ref(self):
        book, env = PromiseBook(LedgerConfig.full()), Env()
        seen = []

        def agent(call):
            call("add_promise", to="customer", what="waiting", done_when="reply", wake_by="customer", told_ref="W1.9")
            seen.append(call("end_session").get("error"))
            call("send_message_to_user", user_id="U1", channel="chat", text="Please send the photos.")  # W1.3
            call("settle_promise", promise_id="P1", how="released", evidence="never mind this one")
            call("add_promise", to="customer", what="waiting", done_when="reply", wake_by="customer", told_ref="W1.3")
            seen.append(call("end_session").get("error"))

        wake(book, env, agent, message="Never mind this one, thanks.")
        self.assertEqual(seen, ["ledger_unbalanced", None])

    def test_event_wake_balances_only_in_push_mode(self):
        for push, expected in ((False, "ledger_unbalanced"), (True, None)):
            book, env = PromiseBook(LedgerConfig.full(), push=push), Env()
            seen = []
            wake(book, env, lambda call: (call("add_promise", to="team", what="w", done_when="d", wake_by="event"), seen.append(call("end_session").get("error"))))
            self.assertEqual(seen, [expected])

    def test_closing_the_ticket_is_refused_while_promises_are_open(self):
        book, env = PromiseBook(LedgerConfig.full()), Env()
        seen = {}

        def agent(call):
            call("add_promise", to="customer", what="call back", done_when="called")
            seen["close"] = call("close_ticket", ticket_id="T1", resolution_code="x")

        wake(book, env, agent)
        self.assertEqual(seen["close"]["error"], "ledger_open_promises")
        self.assertNotIn("close_ticket", [t for t, _ in env.calls])

    def test_a_group_without_check_c_ends_freely(self):
        book, env = PromiseBook(LedgerConfig.no_keep()), Env()
        seen = []
        wake(book, env, lambda call: (call("add_promise", to="c", what="w", done_when="d"), seen.append(call("end_session"))))
        self.assertEqual(seen, [{"ok": True}])


class StatementAndRefsTest(unittest.TestCase):
    def test_the_bill_lists_open_promises_conditions_and_unknown_results(self):
        book, env = PromiseBook(LedgerConfig.full()), Env(timeouts={"pay_claim"})
        wake(book, env, lambda call: (call("add_promise", to="customer", what="confirm slot", done_when="slot booked", condition="part is ready"), call("pay_claim", claim_id="C1")))
        _, holder = wake(book, env, lambda call: None, t="2026-01-06T10:00:00")
        bill = holder["session"]["case_ledger"]
        self.assertIn("P1 to customer: confirm slot", bill)
        self.assertIn("only if: part is ready", bill)
        self.assertIn("W1.2 pay_claim on C1: timeout", bill)
        self.assertIn("wake: no follow-up scheduled yet", bill)

    def test_the_todo_bill_shows_promises_only(self):
        book, env = PromiseBook(LedgerConfig.todo()), Env(timeouts={"pay_claim"})
        wake(book, env, lambda call: (call("add_promise", to="c", what="x", done_when="d"), call("pay_claim", claim_id="C1")))
        _, holder = wake(book, env, lambda call: None)
        self.assertNotIn("pay_claim", holder["session"]["case_ledger"])

    def test_refs_are_numbered_per_wake_and_call(self):
        book, env = PromiseBook(LedgerConfig.full()), Env()
        wake(book, env, lambda call: (call("read_claim", claim_id="C1"), call("read_claim", claim_id="C1")))
        refs = {}
        wake(book, env, lambda call: (call("read_claim", claim_id="C1"), refs.update(last=call.last_ref)))
        self.assertEqual(refs["last"], "W2.1")
        self.assertIn("W1.2", book.refs)

    def test_a_group_without_the_ledger_numbers_refs_the_same_way(self):
        seen = []
        call = _numbered(lambda tool, **a: {"ok": True}, 3)
        call("say", text="hi")
        call("read_claim", claim_id="C1")
        seen.append(call.last_ref)
        self.assertEqual(seen, ["W3.2"])

    def test_the_policy_sentences_differ_only_by_evidence_and_wake(self):
        todo, full = ledger_policy(LedgerConfig.todo()), ledger_policy(LedgerConfig.full())
        self.assertTrue(full.startswith(todo))
        self.assertIn("evidence", full)
        self.assertNotIn("evidence", todo)
        self.assertLess(len(full.split()), 120)


class ScorekeeperTest(unittest.TestCase):
    def test_parse_reads_json_even_inside_prose(self):
        self.assertEqual(scorekeeper.parse('{"missing": []}'), [])
        self.assertEqual(len(scorekeeper.parse('Here: {"missing": [{"to": "customer"}]} done')), 1)
        self.assertIsNone(scorekeeper.parse("no json"))

    def test_run_retries_once_and_drops_items_with_an_unknown_step(self):
        replies = iter(["not json", json.dumps({"missing": [
            {"to": "customer", "what": "call back", "done_when": "called", "source_step": 1},
            {"to": "customer", "what": "ghost", "done_when": "x", "source_step": 99},
        ]})])
        accepted, stats = scorekeeper.run(lambda prompt: next(replies), "policy", "ledger", [], {1, 2})
        self.assertEqual([item["what"] for item in accepted], ["call back"])
        self.assertEqual((stats["calls"], stats["scorekeeper_bad_ref"], stats["scorekeeper_error"]), (2, 1, 0))

    def test_two_unreadable_answers_skip_the_top_up_and_are_logged(self):
        accepted, stats = scorekeeper.run(lambda prompt: "nope", "p", "l", [], {1})
        self.assertEqual((accepted, stats["scorekeeper_error"]), ([], 1))

    def test_the_scorekeeper_tops_up_the_book_with_its_own_source(self):
        reply = json.dumps({"missing": [{"to": "customer", "what": "tell them", "done_when": "told", "due_by": None, "source_step": 1}]})
        book, env = PromiseBook(LedgerConfig.full(), ask=lambda prompt: reply), Env()
        wake(book, env, lambda call: (call("send_message_to_user", user_id="U", channel="chat", text="We will call you."), call("end_session")))
        self.assertEqual(book.promises["P1"]["source"], "scorekeeper")
        self.assertEqual(len(book.added_by_scorekeeper), 1)
        self.assertEqual(book.stats["scorekeeper_calls"], 1)


class ModelAgentWithLedgerTest(unittest.TestCase):
    def test_end_session_goes_through_the_books_and_a_rejection_is_shown_to_the_model(self):
        class Gateway:
            def __init__(self):
                self.script = iter(
                    [
                        {"tool_calls": [{"id": "1", "function": {"name": "add_promise", "arguments": json.dumps({"to": "customer", "what": "call back", "done_when": "called", "due_by": "2026-01-06T10:00:00"})}}]},
                        {"tool_calls": [{"id": "2", "function": {"name": "end_session", "arguments": "{}"}}]},
                        {"tool_calls": [{"id": "3", "function": {"name": "schedule_followup", "arguments": json.dumps({"ticket_id": "T1", "due_at": "2026-01-06T09:00:00", "note": "x"})}}]},
                        {"tool_calls": [{"id": "4", "function": {"name": "end_session", "arguments": "{}"}}]},
                    ]
                )
                self.seen = []

            def reply(self, model, messages, tools=None):
                self.seen.append(messages)
                self.tools = tools
                return next(self.script)

        gateway, env = Gateway(), Env()
        book = PromiseBook(LedgerConfig.full())
        agent = model_agent(gateway, "m", None, {"visits": []}, ledger_tools=LedgerConfig.full())
        book.wrap(agent)({"t": "2026-01-05T10:00:00", "trigger_type": "case_intake", "trigger_id": "I"}, env)
        tool_messages = [m["content"] for m in gateway.seen[-1] if m["role"] == "tool"]
        self.assertTrue(tool_messages[0].startswith("[ref W1.1] "))
        self.assertIn("ledger_unbalanced", tool_messages[1])
        self.assertTrue(tool_messages[1].startswith("[ref W1.2] "))
        self.assertEqual(len(book.rejections), 1)
        self.assertIn("case_ledger", json.loads(gateway.seen[0][1]["content"]))
        self.assertIn("add_promise", {t["function"]["name"] for t in gateway.tools})
        self.assertIn("ledger", gateway.seen[0][0]["content"])


class TestSeedGuardTest(unittest.TestCase):
    def test_a_ledger_group_cannot_run_on_a_test_seed_before_the_freeze(self):
        import os
        from pathlib import Path

        from case_lifecycle_bench.template_run import run_one

        os.environ.pop("LEDGER_FROZEN", None)
        with self.assertRaises(ValueError):
            run_one("credit-delinquency", 300, "poll", "m", "m", "http://127.0.0.1:1/v1", Path("."), enable_thinking=None, group="E3")
        with self.assertRaises(ValueError):
            run_one("credit-delinquency", 391, "poll", "m", "m", "http://127.0.0.1:1/v1", Path("."), enable_thinking=None, group="A1")


if __name__ == "__main__":
    unittest.main()
