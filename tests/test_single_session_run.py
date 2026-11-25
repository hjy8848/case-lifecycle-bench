import json
import unittest

from case_lifecycle_bench.evaluation import evaluate_case
from case_lifecycle_bench.industries.servicing import credit_delinquency as l1
from case_lifecycle_bench.industries.servicing import repair_parts as h1
from case_lifecycle_bench.single_session_run import (
    describe,
    run_single_session,
    single_session_agent,
    single_session_tools,
)


class Gateway:
    """A stand-in model: replies come from a function of what it has been told so far."""

    def __init__(self, reply):
        self.reply = reply
        self.seen = []

    def __call__(self, model, messages, tools=None):
        self.seen.append(messages)
        return self.reply(messages)


class Wrapper:
    def __init__(self, fn):
        self.fn = fn

    def reply(self, model, messages, tools=None):
        return self.fn(messages)


def idle_model(messages):
    """Does nothing but end_session each time it is spoken to."""
    return {
        "content": "",
        "tool_calls": [
            {"id": "x", "function": {"name": "end_session", "arguments": "{}"}}
        ],
    }


def run_with(module, case, model):
    gateway = Wrapper(model)
    tools = single_session_tools(module.TOOLS)
    run, agent = run_single_session(
        case,
        lambda env: single_session_agent(env, gateway, "m", gateway, "u", module.POLICY, tools),
    )
    return run, agent, gateway


class SingleSessionTest(unittest.TestCase):
    def test_the_whole_timeline_runs_in_one_session_and_events_arrive_as_messages(self):
        for module, seed in ((h1, 300), (l1, 301)):
            case = module.generate(seed)
            told = []

            def model(messages, told=told):
                told.append(messages[-1]["content"])
                return idle_model(messages)

            run, agent, gateway = run_with(module, case, model)
            starts = [r for r in run["trajectory"] if r["kind"] == "session_start"]

            with self.subTest(template=case["template"]):
                self.assertEqual(len(starts), 1)
                self.assertTrue(run["single_session"])
                # Every notifying event that took effect was told to the agent as a system message.
                notified = [
                    r for r in run["trajectory"]
                    if r["kind"] == "world_event" and r.get("notify_agent") and r.get("changes")
                ]
                system = [text for text in told if text.startswith("[System]")]
                self.assertGreaterEqual(len(system), len(notified))
                self.assertTrue(any("The time is now 2026-" in text for text in system))
                self.assertTrue(
                    all(text.startswith("[System]") or text.startswith("[System") or "The time is now" in text for text in system)
                )

    def test_the_agent_has_no_follow_up_tools_but_can_wait(self):
        names = {t["function"]["name"] for t in single_session_tools(h1.TOOLS)}
        self.assertIn("wait_until", names)
        self.assertFalse(names & {"schedule_followup", "list_followups", "cancel_followup"})

    def test_waiting_moves_the_clock_and_time_rules_follow_it(self):
        case = h1.generate(300)

        def model(messages):
            last = messages[-1]["content"]
            if last.startswith("[System] The time is now 2026-01-05"):
                return {
                    "content": "",
                    "tool_calls": [
                        {"id": "w", "function": {"name": "wait_until", "arguments": json.dumps({"until": "2026-01-07T10:00:00"})}}
                    ],
                }
            return idle_model(messages)

        run, _, _ = run_with(h1, case, model)
        times = [r["t"] for r in run["trajectory"] if r["kind"] in {"world_event"}]
        self.assertTrue(times)
        self.assertTrue(any(t >= "2026-01-07T10:00:00" or t < "2026-01-07T10:00:00" for t in times))
        self.assertEqual(run["trajectory"][0]["kind"], "session_start")

    def test_scheduling_checks_do_not_count_and_the_terminal_ignores_follow_ups(self):
        case = h1.generate(300)
        run, _, _ = run_with(h1, case, idle_model)
        evaluation = evaluate_case(case, run)

        self.assertTrue(evaluation["single_session"])
        self.assertIn("followup_pending_while_waiting", evaluation["not_scored_in_single_session"])
        self.assertNotIn("followup_pending", evaluation["terminal"]["agent"])

    def test_describing_an_event_does_not_reveal_what_the_agent_must_find_out(self):
        self.assertEqual(describe({"op": "part_arrived", "model_ok": False}), "The part arrived")
        self.assertNotIn("model", describe({"op": "part_arrived", "model_ok": False}))


if __name__ == "__main__":
    unittest.main()
