"""Acceptance 7: what the ledger costs, next to what the diary (E1) would cost.

    python scripts/ledger_cost_report.py runs/ledger-dev/E3 runs/ledger-dev/E2 ... [--baseline runs/dev/E0]

Each argument is a result directory of one group. The E1 column is estimated from the baseline (E0)
runs: the tokens of every earlier session's records, as E1 would append them on each wake. Token counts
are characters divided by four (the gateway returns no usage), so they compare groups, not absolute cost.
"""

import json
import sys
from pathlib import Path


def tokens(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False)) // 4


def history_tokens(record) -> float:
    """Mean tokens E1 would carry on each wake of this run (all earlier sessions, in full)."""
    by_session: dict = {}
    for row in record["run"]["trajectory"]:
        sid = row.get("session_id")
        if sid and row["kind"] in {"tool_call", "message"}:
            by_session.setdefault(sid, []).append({k: row.get(k) for k in ("tool", "args", "result", "role", "text")})
    order = list(by_session)
    if not order:
        return 0.0
    seen, sizes = 0, []
    for sid in order:
        sizes.append(seen)
        seen += tokens(by_session[sid])
    return sum(sizes) / len(sizes)


def load(directory):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("*--*--*--*.json"))]


def main(argv):
    baseline = None
    if "--baseline" in argv:
        index = argv.index("--baseline")
        baseline = argv[index + 1]
        argv = argv[:index] + argv[index + 2:]
    print("| group | runs | bill tokens/wake | scorekeeper tokens/run | scorekeeper calls/run | rejections/run | unbalanced_at_end | implicit ends/run | ledger tool error rate | scorekeeper parse errors |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for directory in argv:
        records = [r for r in load(directory) if "ledger" in r]
        if not records:
            print(f"| {directory} | 0 | | | | | | | | |")
            continue
        n = len(records)
        led = [r["ledger"] for r in records]
        calls = sum(item["ledger_calls"] for item in led)
        print(
            f"| {Path(directory).name} | {n} | {sum(item['statement_tokens_mean'] for item in led) / n:.0f} "
            f"| {sum(item['scorekeeper_tokens'] for item in led) / n:.0f} | {sum(item['scorekeeper_calls'] for item in led) / n:.1f} "
            f"| {sum(len(item['rejections']) for item in led) / n:.2f} | {sum(item['unbalanced_at_end'] for item in led)}/{n} "
            f"| {sum(item['implicit_ends'] for item in led) / n:.1f} "
            f"| {(sum(item['ledger_arg_errors'] for item in led) / calls) if calls else 0:.3f} "
            f"| {sum(item['scorekeeper_error'] for item in led)}/{max(1, sum(item['scorekeeper_calls'] for item in led))} |"
        )
    if baseline:
        records = load(baseline)
        if records:
            print(f"\nE1 estimate from {len(records)} baseline runs: {sum(history_tokens(r) for r in records) / len(records):.0f} history tokens per wake")


if __name__ == "__main__":
    main(sys.argv[1:])
