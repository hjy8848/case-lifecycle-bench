"""Acceptance 7: what the ledger costs, next to what the diary (E1) would cost.

    python scripts/ledger_cost_report.py runs/ledger-dev/E3 runs/ledger-dev/E2 ... [--baseline runs/dev/E0]

Each argument is a result directory of one group (E1 included: its history tokens are measured on its own
runs). Token counts are characters divided by four for every group (the gateway returns no usage), so they
compare groups and are an estimate, not a billed cost.
"""

import json
import sys
from pathlib import Path


def load(directory):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("*--*--*--*.json"))]


def main(argv):
    print("| group | runs | memory tokens/wake | bill tokens/wake | scorekeeper tokens/run | scorekeeper calls/run | rejections/run | unbalanced_at_end | implicit ends/run | ledger tool error rate | scorekeeper parse errors |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for directory in argv:
        everything = load(directory)
        waked = [r["memory_tokens"]["per_wake"] for r in everything if r.get("memory_tokens")]
        flat = [x for run in waked for x in run]
        memory = f"{sum(flat) / len(flat):.0f}" if flat else "0"
        records = [r for r in everything if "ledger" in r]
        if not records:
            print(f"| {Path(directory).name} | {len(everything)} | {memory} | | | | | | | |")
            continue
        n = len(records)
        led = [r["ledger"] for r in records]
        calls = sum(item["ledger_calls"] for item in led)
        print(
            f"| {Path(directory).name} | {n} | {memory} | {sum(item['statement_tokens_mean'] for item in led) / n:.0f} "
            f"| {sum(item['scorekeeper_tokens'] for item in led) / n:.0f} | {sum(item['scorekeeper_calls'] for item in led) / n:.1f} "
            f"| {sum(len(item['rejections']) for item in led) / n:.2f} | {sum(item['unbalanced_at_end'] for item in led)}/{n} "
            f"| {sum(item['implicit_ends'] for item in led) / n:.1f} "
            f"| {(sum(item['ledger_arg_errors'] for item in led) / calls) if calls else 0:.3f} "
            f"| {sum(item['scorekeeper_error'] for item in led)}/{max(1, sum(item['scorekeeper_calls'] for item in led))} |"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
