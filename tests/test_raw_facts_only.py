import re
import unittest

from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.industries.service_cases import WORKFLOWS

# A system record gives raw facts: times, counts, states. It never says "you may now" or
# "the limit is exceeded"; working the clock out is the agent's job. The tool may still
# refuse an action that comes too early, and that refusal is scored as a violation.
VERDICT = re.compile(r"allowed|overdue|exceeded|within_24h|deadline_passed|_due$|^due_|stuck|expired")


def keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from keys(inner)


class RawFactsOnlyTest(unittest.TestCase):
    def test_no_read_tool_result_carries_a_verdict_key(self):
        for name, workflow in WORKFLOWS.items():
            for seed in range(300, 310):
                case = workflow.generate(seed)
                for wake in ("poll", "push"):
                    run = run_case(case, workflow.agents["oracle"], wake=wake)
                    for row in run["trajectory"]:
                        if row.get("kind") != "tool_call" or not row["tool"].startswith(("read_", "get_")):
                            continue
                        bad = [k for k in keys(row["result"]) if VERDICT.search(k) and k != "amount_overdue"]
                        with self.subTest(template=name, seed=seed, wake=wake, tool=row["tool"]):
                            self.assertEqual(bad, [])

    def test_the_policies_do_not_point_at_a_flag_the_record_no_longer_has(self):
        for name, workflow in WORKFLOWS.items():
            with self.subTest(template=name):
                for phrase in ("shows a reminder allowed", "whether it is overdue", "whether it has passed",
                               "whether the limit is exceeded", "is due now"):
                    self.assertNotIn(phrase, workflow.policy)

    def test_a_reminder_that_comes_too_early_is_still_refused(self):
        from case_lifecycle_bench.industries.servicing import claim_documents as i2

        case = i2.generate(300)
        env = i2.ClaimEnvironment(case, "poll")
        env.t = __import__("datetime").datetime.fromisoformat(case["init"]["checklist_sent_at"])
        self.assertEqual(
            env._domain_execute("send_supplement_reminder", {"text": "x"}, "S1")[0],
            {"error": "reminder_not_allowed"},
        )


if __name__ == "__main__":
    unittest.main()
