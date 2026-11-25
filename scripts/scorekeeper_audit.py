"""Acceptance 6: draw 30 scorekeeper top-ups at random for a human to judge.

    python scripts/scorekeeper_audit.py runs/ledger-dev/E3 [more dirs] --n 30 --seed 0 > audit.md

For each sampled top-up the sheet shows the promise the scorekeeper added, the step it cites, and what
the agent said or did in that session around that step. The reviewer marks each as a real debt or not;
more than 20% false promises means the prompt may be tuned, on development seeds only.
"""

import argparse
import json
import random
from pathlib import Path


def collect(directories):
    items = []
    for directory in directories:
        for path in sorted(Path(directory).glob("*--*--*--*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            for promise in record.get("ledger", {}).get("scorekeeper_added", []):
                items.append((path.name, record, promise))
    return items


def session_lines(record, wake_label, step):
    """Agent words and calls of one wake, around the cited step."""
    wake = int(wake_label.split(".")[0].lstrip("W"))
    rows, wakes = [], 0
    last_session = None
    for row in record["run"]["trajectory"]:
        if row["kind"] == "session_start":
            wakes += 1
        if wakes == wake and row["kind"] in {"tool_call", "message"} and row.get("role") != "user":
            text = row.get("text") or f"{row.get('tool')} {json.dumps(row.get('args'), ensure_ascii=False)[:160]} -> {json.dumps(row.get('result'), ensure_ascii=False)[:120]}"
            rows.append(text)
    return rows[max(0, step - 3) : step + 2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    items = collect(args.directories)
    random.Random(args.seed).shuffle(items)
    print(f"# Scorekeeper audit ({min(args.n, len(items))} of {len(items)} top-ups)\n")
    print("Mark each: **real debt** / **not a debt**.\n")
    for index, (name, record, promise) in enumerate(items[: args.n], 1):
        print(f"## {index}. {name} ({promise['created_ref']})")
        print(f"- to: {promise['to']}\n- what: {promise['what']}\n- due_by: {promise.get('due_by')}\n- done_when: {promise['done_when']}\n- condition: {promise.get('condition')}")
        print("- around the cited step:")
        for line in session_lines(record, promise["created_ref"], promise["source_step"]):
            print(f"  - {line}")
        print("- verdict: [ ] real debt  [ ] not a debt\n")


if __name__ == "__main__":
    main()
