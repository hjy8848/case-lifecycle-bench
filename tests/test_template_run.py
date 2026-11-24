import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from case_lifecycle_bench import template_run


def fake_record(seed):
    return {
        "case_id": f"x-{seed}",
        "meta": {"invalid_arguments": 0},
        "evaluation": {
            "terminal_variant": "resolved",
            "metrics": {
                "case_resolution": True,
                "wrongful_closure": 0,
                "unsafe_action_rate": 0,
                "tool_calls": 10,
                "interface_errors": 0,
            },
            "process": {},
        },
    }


class Probe:
    """Stands in for run_one: records which replica ran what and how many overlapped."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = {}
        self.peak = {}
        self.bases = {}

    def __call__(self, name, seed, wake, model, user_model, base, output, **kwargs):
        with self.lock:
            self.active[base] = self.active.get(base, 0) + 1
            self.peak[base] = max(self.peak.get(base, 0), self.active[base])
            self.bases[seed] = base
        time.sleep(0.05)
        with self.lock:
            self.active[base] -= 1
        return fake_record(seed)


def run_main(*extra, seeds=8):
    probe = Probe()
    argv = [
        "template_run",
        "--template", "credit-delinquency",
        "--model", "m",
        "--api-bases", "http://127.0.0.1:1/v1", "http://127.0.0.1:2/v1",
        "--seeds", str(seeds),
        "--output", tempfile.mkdtemp(),
        *extra,
    ]
    with mock.patch.object(template_run, "run_one", probe), mock.patch("sys.argv", argv), \
            mock.patch("builtins.print"):
        code = template_run.main()
    return probe, code


class ConcurrencyTest(unittest.TestCase):
    def test_by_default_each_replica_runs_one_at_a_time(self):
        probe, code = run_main()

        self.assertEqual(code, 0)
        self.assertEqual(set(probe.peak.values()), {1})

    def test_more_workers_overlap_runs_on_the_same_replica(self):
        probe, _ = run_main("--workers-per-replica", "3", seeds=12)

        self.assertGreater(max(probe.peak.values()), 1)
        self.assertLessEqual(max(probe.peak.values()), 3)

    def test_jobs_are_dealt_to_the_replicas_in_turn(self):
        probe, _ = run_main("--workers-per-replica", "2", seeds=8)

        bases = [probe.bases[300 + index] for index in range(8)]
        self.assertEqual(bases, ["http://127.0.0.1:1/v1", "http://127.0.0.1:2/v1"] * 4)


class SummaryTest(unittest.TestCase):
    def test_the_interface_error_rate_counts_malformed_calls_too(self):
        records = [fake_record(1), fake_record(2)]
        records[0]["evaluation"]["metrics"]["interface_errors"] = 1
        records[1]["meta"]["invalid_arguments"] = 2

        summary = template_run.summarize(records)

        self.assertEqual(summary["interface"]["errors"], 3)
        self.assertEqual(summary["interface"]["tool_calls"], 20)
        self.assertAlmostEqual(summary["interface"]["error_rate"], 3 / 22)
        self.assertEqual(summary["interface"]["runs_with_an_error"], 2)


if __name__ == "__main__":
    unittest.main()
