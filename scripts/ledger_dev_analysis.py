"""Development-seed analysis of the ledger groups (items 1-5 and 7 of the lead's request of 11/26).

    python scripts/ledger_dev_analysis.py runs/ledger-dev [--section 1,2,3,4,5,7]

Only for development seeds. Reads result files; changes nothing.
"""

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_classes import CLASSES  # noqa: E402

GROUPS = ("E0", "E1", "E2", "E3", "A1", "A2")
CLASS_OF = {check: name for name, checks in CLASSES.items() for check in checks}
CLASS_ORDER = ("omission", "omission_told", "premature", "duplicate", "false_completion", "two_way", "business_judgement")


def load(root):
    runs = {g: {} for g in GROUPS}
    for g in GROUPS:
        for path in (Path(root) / g).glob("*--*--*--*.json"):
            r = json.loads(path.read_text(encoding="utf-8"))
            runs[g][(r["meta"]["template"], r["meta"]["seed"], r["meta"]["wake"])] = r
    common = sorted(set.intersection(*(set(v) for v in runs.values())))
    return runs, common


def ok(r):
    return bool(r["evaluation"]["metrics"]["case_resolution"])


def failed(r):
    return [k for k, v in r["evaluation"]["process"].items() if v is False]


def terminal_diff(r):
    t = r["evaluation"].get("terminal", {})
    agent, oracle = t.get("agent", {}), t.get("oracle", {})
    return {k: (agent.get(k), oracle.get(k)) for k in oracle if agent.get(k) != oracle.get(k)}


def sessions(r):
    """Trajectory rows grouped by wake number (1-based), with how each session ended."""
    out, current = [], None
    for row in r["run"]["trajectory"]:
        if row["kind"] == "session_start":
            current = {"start": row, "rows": [], "end": None}
            out.append(current)
        elif current is not None:
            if row["kind"] == "session_end":
                current["end"] = row
            else:
                current["rows"].append(row)
    return out


def promise_source(r, text):
    pid = text.split(":", 1)[0]
    for p in r["ledger"]["promises"]:
        if p["id"] == pid:
            return p["source"]
    return "?"


def section_7(runs, common):
    print("## 7. Keep-waiting instances, paired against E0\n")
    wait = [k for k in common if runs["E0"][k]["evaluation"]["terminal_variant"] == "wait"]
    print(f"{len(wait)} keep-waiting jobs finished in every group.\n")
    print("| group | wins over E0 | losses to E0 | both pass | both fail |")
    print("| --- | --- | --- | --- | --- |")
    for g in GROUPS[1:]:
        c = collections.Counter((ok(runs[g][k]), ok(runs["E0"][k])) for k in wait)
        print(f"| {g} | {c[(True, False)]} | {c[(False, True)]} | {c[(True, True)]} | {c[(False, False)]} |")
    losses = [k for k in wait if not ok(runs["E3"][k]) and ok(runs["E0"][k])]
    wins = [k for k in wait if ok(runs["E3"][k]) and not ok(runs["E0"][k])]
    print("\nE3 wins: " + ", ".join(f"{t} {s} {w} (E0 failed {failed(runs['E0'][(t, s, w)])})" for t, s, w in wins))
    print("\nE3 losses: " + (", ".join(f"{t} {s} {w} (E3 failed {failed(runs['E3'][(t, s, w)])})" for t, s, w in losses) or "none"))
    print()


def section_1(runs, common):
    print("## 1. Resolved instances where E3 failed and E0 passed\n")
    pairs = [k for k in common if runs["E0"][k]["evaluation"]["terminal_variant"] == "resolved" and not ok(runs["E3"][k]) and ok(runs["E0"][k])]
    print(f"{len(pairs)} pairs.\n")
    for k in pairs:
        r = runs["E3"][k]
        rej = collections.Counter(row["check"] for row in r["ledger"]["rejections"])
        gate = [row for row in r["ledger"]["rejections"] if row["check"] == "close_gate"]
        gate_sources = collections.Counter(promise_source(r, line) for row in gate for line in row["response"].get("open", []))
        ends = collections.Counter((s["end"] or {}).get("reason") for s in sessions(r))
        print(f"- **{k[0]} {k[1]} {k[2]}**: failed {failed(r)}; terminal differs {terminal_diff(r)}")
        print(f"  rejections {dict(rej)}; close-gate refusals {len(gate)} on promises from {dict(gate_sources)}; session ends {dict(ends)}; "
              f"scorekeeper top-ups {len(r['ledger']['scorekeeper_added'])}; turn-limit hits {r['meta'].get('turn_limit_hits', 'n/a')}")
        for row in gate[:2]:
            print(f"  close refused at {row['ref']}: open {row['response'].get('open')}")
        for p in r["ledger"]["scorekeeper_added"]:
            print(f"  scorekeeper added {p['id']}: {p['what']} (done when: {p['done_when']}; due {p.get('due_by')}; status at end {next((q['status'] for q in r['ledger']['promises'] if q['id'] == p['id']), '?')})")
    print()


def _c_reason(line):
    m = re.search(r"\(([^()]*)\)\s*$", line)
    text = m.group(1) if m else line
    if text.startswith("no follow-up before"):
        return "no follow-up due in time"
    return text


def section_2(runs, common):
    print("## 2. Check B and C refusals by code (E3 and the ablations)\n")
    for g in ("E3", "A1", "A2"):
        codes = collections.defaultdict(list)
        for k, r in runs[g].items():
            for row in r["ledger"]["rejections"]:
                if row["check"] == "B":
                    codes[("B", row["response"]["error"])].append((k, row))
                elif row["check"] == "C":
                    for line in row["response"].get("open", []):
                        codes[("C", _c_reason(line))].append((k, {**row, "line": line}))
        if not codes:
            continue
        print(f"### {g}\n")
        for (check, code), items in sorted(codes.items(), key=lambda kv: -len(kv[1])):
            print(f"- {check} `{code}`: {len(items)}")
            for k, row in items[:2]:
                detail = row.get("line") or row["response"].get("why")
                call = row.get("call", {})
                cited = call.get("cited")
                evidence = (call.get("args") or {}).get("evidence")
                extra = f"; evidence {evidence!r} -> {cited}" if call else "; (arguments not logged in this run)"
                print(f"  - {k[0]} {k[1]} {k[2]} at {row['ref']}: {detail}{extra if check == 'B' else ''}")
        print()


def section_3(runs, common):
    print("## 3. Sessions that ended with the books unbalanced (E3, A2)\n")
    for g in ("E3", "A2"):
        for k, r in sorted(runs[g].items()):
            if not r["ledger"]["unbalanced_at_end"]:
                continue
            by_wake = collections.defaultdict(list)
            for row in r["ledger"]["rejections"]:
                if row["check"] == "C":
                    by_wake[row["wake"]].append(row)
            ss = sessions(r)
            for wake, rows in by_wake.items():
                n = int(wake.lstrip("W"))
                s = ss[n - 1] if n - 1 < len(ss) else None
                end = (s["end"] or {}).get("reason") if s else "?"
                tools = [row.get("tool") for row in (s["rows"] if s else []) if row["kind"] == "tool_call"]
                last_open = rows[-1]["response"].get("open", [])
                print(f"- {g} {k[0]} {k[1]} {k[2]} {wake}: {len(rows)} C refusals; ended by {end}; "
                      f"follow-ups scheduled in that session: {tools.count('schedule_followup')}; open at the last refusal: "
                      + "; ".join(f"{line} [{promise_source(r, line)}]" for line in last_open))
        print()


def section_4(runs, common):
    print("## 4. Failed checks per class (preregistered classes), same jobs in every group\n")
    print("| group | pass | " + " | ".join(CLASS_ORDER) + " | wrongful closure | harmful | procedural |")
    print("| --- | --- | " + " | ".join("---" for _ in CLASS_ORDER) + " | --- | --- | --- |")
    for g in GROUPS:
        c = collections.Counter()
        extra = collections.Counter()
        for k in common:
            r = runs[g][k]
            for check in failed(r):
                if check in CLASS_OF:
                    c[CLASS_OF[check]] += 1
            m = r["evaluation"]["metrics"]
            extra["wrongful"] += m.get("wrongful_closure", 0)
            extra["harmful"] += m.get("harmful_action", 0)
            extra["procedural"] += m.get("procedural_violation", 0)
        passes = sum(ok(runs[g][k]) for k in common)
        print(f"| {g} | {passes}/{len(common)} | " + " | ".join(str(c[name]) for name in CLASS_ORDER)
              + f" | {extra['wrongful']} | {extra['harmful']} | {extra['procedural']} |")
    print()


def section_5(runs, common):
    print("## 5. E2 failed where E0 passed\n")
    pairs = [k for k in common if not ok(runs["E2"][k]) and ok(runs["E0"][k])]
    print(f"{len(pairs)} pairs.\n")
    for k in pairs:
        r = runs["E2"][k]
        print(f"- {k[0]} {k[1]} {k[2]} ({r['evaluation']['terminal_variant']}): failed {failed(r)}; terminal differs {terminal_diff(r)}; "
              f"promises {[(p['id'], p['status']) for p in r['ledger']['promises']]}")
    print()


def main():
    root = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "runs/ledger-dev"
    wanted = "1,2,3,4,5,7"
    if "--section" in sys.argv:
        wanted = sys.argv[sys.argv.index("--section") + 1]
    runs, common = load(root)
    print(f"# Development-seed analysis ({len(common)} jobs finished in every group)\n")
    for number in wanted.split(","):
        {"1": section_1, "2": section_2, "3": section_3, "4": section_4, "5": section_5, "7": section_7}[number](runs, common)


if __name__ == "__main__":
    main()
