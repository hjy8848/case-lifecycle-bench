"""Acceptance 3 and 4: run every Oracle and every counterexample under the ledger in record-only mode.

No model is called, so the test seeds may be used. Check A must fire 0 times for any Oracle, and the
blind-retry counterexample must be caught.

    python scripts/ledger_offline_check.py
"""

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from case_lifecycle_bench.case_ledger import LedgerConfig, PromiseBook  # noqa: E402
from case_lifecycle_bench.environment import run_case  # noqa: E402
from case_lifecycle_bench.industries.servicing.kit import P7_SEEDS  # noqa: E402
from case_lifecycle_bench.template_check import TEMPLATES  # noqa: E402

SEEDS = [*range(300, 310), *P7_SEEDS]


def run(agent, case, wake):
    book = PromiseBook(LedgerConfig.full().recording(), push=(wake == "push"))
    run_case(case, book.wrap(agent), wake=wake)
    counts = collections.Counter(row["check"] for row in book.would_reject)
    timeouts = sum(1 for e in book.journal if e["error"] == "timeout")
    return counts, timeouts


def main():
    oracle_a, oracle_timeouts, oracle_runs = 0, 0, 0
    counter_rows = collections.defaultdict(lambda: collections.Counter())
    for name, spec in TEMPLATES.items():
        for seed in SEEDS:
            case = spec.generate(seed)
            for wake in ("poll", "push"):
                counts, timeouts = run(spec.oracle, case, wake)
                oracle_runs += 1
                oracle_a += counts["A"]
                oracle_timeouts += timeouts
                if counts["A"]:
                    print("ORACLE TRIGGERED A:", name, seed, wake, counts["A"])
                for agent, section, key in spec.counterexamples:
                    label = (name, getattr(agent, "__name__", str(agent)))
                    try:
                        counts, _ = run(agent, case, wake)
                    except Exception:  # a deliberately wrong agent may break a case it was not built for
                        counter_rows[label]["crashed"] += 1
                        continue
                    counter_rows[label]["A"] += counts["A"]
    print(json.dumps({"oracle_runs": oracle_runs, "oracle_timeouts": oracle_timeouts, "oracle_check_A_triggers": oracle_a}))
    for (name, agent), counts in sorted(counter_rows.items()):
        if counts["A"] or counts["crashed"] or "--all" in sys.argv:
            print(f"counterexample {agent} in {name}: check A would refuse {counts['A']} writes; crashed on {counts['crashed']} runs")
    print("total counterexample check-A refusals:", sum(c["A"] for c in counter_rows.values()))


if __name__ == "__main__":
    main()
