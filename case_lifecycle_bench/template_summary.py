"""Summarize a directory of template_run results, rescored with the current scorer.

Every record keeps its trajectory, so the numbers are recomputed from the trace
rather than read from the stored evaluation. A scorer fix therefore reaches old
results, and the same scorer produces every table.

    python -m case_lifecycle_bench.template_summary runs/npu-template-run
"""

import argparse
import collections
import json
from pathlib import Path

from .evaluation import evaluate_case
from .industries.service_cases import WORKFLOWS
from .outcomes import INTERFACE_ERRORS, VARIANTS


def load(directory: Path):
    for path in sorted(directory.glob("*--*--*--*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if "run" in record:
            yield record


def rescore(record):
    meta = record["meta"]
    case = WORKFLOWS[meta["template"]].generate(meta["seed"])
    if meta.get("customer_mode") == "llm":
        case["customer_mode"] = "llm"
    return evaluate_case(case, record["run"])


def summarize(records) -> dict:
    groups = collections.defaultdict(list)
    for record in records:
        meta = record["meta"]
        groups[(meta["template"], meta.get("customer_mode", "script"), meta["wake"])].append(
            (record, rescore(record))
        )
    table = {}
    for key, rows in sorted(groups.items()):
        by_variant = {variant: [0, 0] for variant in VARIANTS}
        chats, calls, errors, malformed = [], 0, 0, 0
        interface_kinds = collections.Counter()
        for record, evaluation in rows:
            variant = evaluation["terminal_variant"]
            by_variant[variant][1] += 1
            by_variant[variant][0] += bool(evaluation["metrics"]["case_resolution"])
            if evaluation["metrics"]["conversation_success"] is not None:
                chats.append(evaluation["metrics"]["conversation_success"])
            calls += evaluation["metrics"]["tool_calls"]
            errors += evaluation["metrics"]["interface_errors"]
            malformed += record["meta"].get("invalid_arguments", 0)
            for step in record["run"]["trajectory"]:
                result = step.get("result")
                if (
                    step["kind"] == "tool_call"
                    and isinstance(result, dict)
                    and result.get("error") in INTERFACE_ERRORS
                ):
                    interface_kinds[result["error"]] += 1
        table["/".join(key)] = {
            "runs": len(rows),
            "responsibility": sum(v[0] for v in by_variant.values()),
            "by_variant": {k: f"{v[0]}/{v[1]}" for k, v in by_variant.items() if v[1]},
            "unsafe": sum(
                bool(e["metrics"]["unsafe_action_rate"]) for _, e in rows
            ),
            "wrongful_closure": sum(e["metrics"]["wrongful_closure"] for _, e in rows),
            "conversation_success": sum(chats) / len(chats) if chats else None,
            "interface_error_rate": (errors + malformed) / (calls + malformed)
            if calls
            else None,
            "interface_errors": dict(interface_kinds) | (
                {"invalid_arguments": malformed} if malformed else {}
            ),
            "model_retries": sum(r["meta"].get("model_retries", 0) for r, _ in rows),
        }
    return table


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(load(args.directory)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
