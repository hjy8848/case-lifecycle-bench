import json
import tempfile
import unittest
from pathlib import Path

from case_lifecycle_bench import template_summary
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.industries.service_cases import WORKFLOWS


def write(directory, template, seed, agent, *, retries=0, invalid=0):
    workflow = WORKFLOWS[template]
    case = workflow.generate(seed)
    run = run_case(case, workflow.agents[agent])
    record = {
        "meta": {
            "template": template,
            "seed": seed,
            "wake": "poll",
            "customer_mode": "script",
            "model_retries": retries,
            "invalid_arguments": invalid,
        },
        "run": run,
    }
    path = Path(directory) / f"{template}--{case['terminal_variant']}--{seed}--poll.json"
    path.write_text(json.dumps(record), encoding="utf-8")


class TemplateSummaryTest(unittest.TestCase):
    def test_the_summary_is_rescored_from_the_trace_and_split_by_variant(self):
        with tempfile.TemporaryDirectory() as directory:
            for seed in range(300, 310):
                write(directory, "credit-delinquency", seed, "oracle")
            write(directory, "credit-delinquency", 310, "do-nothing")

            table = template_summary.summarize(template_summary.load(Path(directory)))

        group = table["credit-delinquency/script/poll"]
        self.assertEqual(group["runs"], 11)
        self.assertEqual(group["responsibility"], 10)
        self.assertEqual(group["by_variant"]["resolved"], "5/6")
        self.assertEqual(group["by_variant"]["wait"], "3/3")
        self.assertEqual(group["by_variant"]["escalate"], "2/2")
        self.assertEqual(group["interface_error_rate"], 0)

    def test_malformed_calls_and_retries_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            write(directory, "field-service-repair", 300, "oracle", retries=2, invalid=3)

            group = template_summary.summarize(template_summary.load(Path(directory)))[
                "field-service-repair/script/poll"
            ]

        self.assertEqual(group["model_retries"], 2)
        self.assertEqual(group["interface_errors"], {"invalid_arguments": 3})
        self.assertGreater(group["interface_error_rate"], 0)

    def test_templates_and_customer_modes_are_kept_apart(self):
        with tempfile.TemporaryDirectory() as directory:
            write(directory, "credit-delinquency", 300, "oracle")
            write(directory, "credit-application", 300, "oracle")

            table = template_summary.summarize(template_summary.load(Path(directory)))

        self.assertEqual(
            set(table), {"credit-delinquency/script/poll", "credit-application/script/poll"}
        )


if __name__ == "__main__":
    unittest.main()
