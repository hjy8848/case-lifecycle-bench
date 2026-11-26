"""Interim comparison of the groups on the jobs every group has finished (development seeds only).

    python scripts/ledger_interim.py runs/ledger-dev
"""

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_classes import CLASSES  # noqa: E402

GROUPS = ("E0", "E1", "E2", "E3", "A1", "A2")
CLASS_OF = {check: name for name, checks in CLASSES.items() for check in checks}


def main(root):
    root = Path(root)
    runs = {g: {} for g in GROUPS}
    for g in GROUPS:
        for path in (root / g).glob("*--*--*--*.json"):
            r = json.loads(path.read_text(encoding="utf-8"))
            runs[g][(r["meta"]["template"], r["meta"]["seed"], r["meta"]["wake"])] = r
    common = set.intersection(*(set(v) for v in runs.values()))
    print(f"jobs finished in every group: {len(common)}  (per group: { {g: len(v) for g, v in runs.items()} })\n")
    print("| group | all | resolved | wait | escalate | poll | push | harmful | wrongful closure | omission fails | premature fails | duplicate fails | false-completion fails | two-way fails | business fails | memory tok/wake |")
    print("| --- | " + " | ".join("---" for _ in range(15)) + " |")
    for g in GROUPS:
        cell = collections.defaultdict(lambda: [0, 0])
        fails = collections.Counter()
        harmful = wrongful = 0
        memory = []
        for key in common:
            r = runs[g][key]
            ev = r["evaluation"]
            ok = bool(ev["metrics"]["case_resolution"])
            for name in ("all", ev["terminal_variant"], key[2]):
                cell[name][0] += ok
                cell[name][1] += 1
            harmful += ev["metrics"].get("harmful_action", 0)
            wrongful += ev["metrics"].get("wrongful_closure", 0)
            for check, value in ev["process"].items():
                if value is False and check in CLASS_OF:
                    fails[CLASS_OF[check]] += 1
            memory += (r.get("memory_tokens") or {}).get("per_wake", [])

        def show(name):
            a, b = cell[name]
            return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"

        print(
            f"| {g} | {show('all')} | {show('resolved')} | {show('wait')} | {show('escalate')} | {show('poll')} | {show('push')} "
            f"| {harmful} | {wrongful} | {fails['omission']} | {fails['premature']} | {fails['duplicate']} | {fails['false_completion']} "
            f"| {fails['two_way']} | {fails['business_judgement']} | {sum(memory) / len(memory) if memory else 0:.0f} |"
        )
    print("\nLedger mechanics on the same jobs:")
    for g in ("E2", "E3", "A1", "A2"):
        led = [runs[g][k]["ledger"] for k in common]
        checks = collections.Counter(row["check"] for item in led for row in item["rejections"])
        calls = sum(item["ledger_calls"] for item in led)
        sk = sum(item["scorekeeper_calls"] for item in led)
        print(
            f"  {g}: ledger tool calls {calls}, arg errors {sum(i['ledger_arg_errors'] for i in led)}; "
            f"rejections {dict(checks)}; unbalanced_at_end {sum(i['unbalanced_at_end'] for i in led)}; "
            f"scorekeeper calls {sk}, parse errors {sum(i['scorekeeper_error'] for i in led)}, "
            f"top-ups {sum(len(i['scorekeeper_added']) for i in led)}; agent-recorded promises "
            f"{sum(1 for i in led for p in i['promises'] if p['source'] == 'agent')}"
        )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "runs/ledger-dev")
