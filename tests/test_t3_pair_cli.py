import json
import subprocess
import sys
import unittest


class T3PairCliTest(unittest.TestCase):
    def test_pair_uses_the_same_synthetic_starting_state(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "case_lifecycle_bench",
                "--case",
                "t3",
                "--paired",
                "--trace",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        paired = json.loads(result.stdout)
        lifecycle = paired["lifecycle"]
        single = paired["single_session"]
        self.assertEqual(
            lifecycle["run"]["initial_state"], single["run"]["initial_state"]
        )
        self.assertTrue(lifecycle["evaluation"]["metrics"]["case_resolution"])
        self.assertTrue(single["evaluation"]["metrics"]["case_resolution"])
