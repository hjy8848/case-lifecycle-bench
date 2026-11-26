"""Draw random check C and check B refusals for a human verdict (development seeds only).

    python scripts/ledger_refusal_audit.py runs/ledger-dev-retest --n 20 --seed 0 > audit.md

Each item carries what a reviewer needs to say whether the refusal was right, the rule was wrong, or the
promise should not have existed (a scorekeeper top-up that was not a real debt).
"""

import argparse
import json
import random
import re
from pathlib import Path


def load(root, groups):
    out = []
    for g in groups:
        for path in sorted((Path(root) / g).glob("*--*--*--*.json")):
            out.append((g, path.name, json.loads(path.read_text(encoding="utf-8"))))
    return out


def wake_time(record, wake_label):
    n = int(wake_label.lstrip("W").split(".")[0])
    starts = [row for row in record["run"]["trajectory"] if row["kind"] == "session_start"]
    return starts[n - 1]["t"] if n - 1 < len(starts) else "?"


def followups_at(record, wake_label):
    """Follow-ups scheduled up to and including this wake, with their due times."""
    n = int(wake_label.lstrip("W").split(".")[0])
    seen, out = 0, []
    for row in record["run"]["trajectory"]:
        if row["kind"] == "session_start":
            seen += 1
            if seen > n:
                break
        if row["kind"] == "tool_call" and row["tool"] == "schedule_followup":
            out.append((row["result"], row["args"].get("due_at")))
    return out


def promise(record, pid):
    return next((p for p in record["ledger"]["promises"] if p["id"] == pid), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    c_items, b_items = [], []
    for g, name, r in load(args.root, ("E3", "A2")):
        for row in r["ledger"]["rejections"]:
            if row["check"] == "C":
                for line in row["response"].get("open", []):
                    c_items.append((g, name, r, row, line))
    for g, name, r in load(args.root, ("E3", "A1")):
        for row in r["ledger"]["rejections"]:
            if row["check"] == "B":
                b_items.append((g, name, r, row))
    rng.shuffle(c_items)
    rng.shuffle(b_items)

    print(f"# Refusal audit ({args.root})\n")
    print(f"Check C: {min(args.n, len(c_items))} of {len(c_items)} refused lines. Check B: {min(args.n, len(b_items))} of {len(b_items)}.\n")
    print("Verdicts: **right** (the refusal was correct), **rule wrong** (the rule refused something it should not), "
          "**no debt** (the promise should not have existed, e.g. a wrong scorekeeper top-up).\n")
    print("## Check C\n")
    for i, (g, name, r, row, line) in enumerate(c_items[: args.n], 1):
        pid = line.split(":", 1)[0]
        p = promise(r, pid) or {}
        print(f"### C{i}. {g} {name} {row['wake']} (time {wake_time(r, row['wake'])})")
        print(f"- refused: {line}")
        print(f"- promise: source {p.get('source')}, created {p.get('created_ref')}, due_by {p.get('due_by')}, condition {p.get('condition')}, "
              f"wake_by {p.get('wake_by')}, done_when {p.get('done_when')}, status at end {p.get('status')}, overdue wakes {p.get('overdue_wakes', 0)}")
        print(f"- follow-ups scheduled so far: {followups_at(r, row['wake'])}")
        if p.get("source") == "scorekeeper":
            added = next((a for a in r["ledger"]["scorekeeper_added"] if a["id"] == pid), {})
            print(f"- scorekeeper cited step {added.get('source_step')}")
        print("- verdict: \n- reason: \n")
    print("## Check B\n")
    for i, (g, name, r, row) in enumerate(b_items[: args.n], 1):
        call = row.get("call", {})
        p = call.get("promise", {})
        print(f"### B{i}. {g} {name} {row['ref']} (time {wake_time(r, row['wake'])})")
        print(f"- refused: {row['response']}")
        print(f"- settle arguments: {call.get('args')}")
        print(f"- cited call: {call.get('cited')}")
        print(f"- promise: {p.get('id')} '{p.get('what')}' subject {p.get('subject')}, done_when {p.get('done_when')}, source {p.get('source')}")
        print("- verdict: \n- reason: \n")


if __name__ == "__main__":
    main()
