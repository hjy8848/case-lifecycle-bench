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
from .runnable_templates import ALL
from .outcomes import INTERFACE_ERRORS, VARIANTS


def load(directory: Path):
    for path in sorted(directory.glob("*--*--*--*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if "run" in record:
            yield record


def rescore(record):
    meta = record["meta"]
    case = ALL[meta["template"]].generate(meta["seed"])
    if meta.get("customer_mode") == "llm":
        case["customer_mode"] = "llm"
    return evaluate_case(case, record["run"])


def evaluation_basis(rows) -> str:
    bases = {evaluation.get("scoring") for _, evaluation in rows}
    return bases.pop() if len(bases) == 1 else "mixed"


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
        p7 = [0, 0]  # spec rule P7 instances are reported on their own
        unchanged = [0, 0]  # review ended with no change (I4, L4)
        interface_kinds = collections.Counter()
        for record, evaluation in rows:
            variant = evaluation["terminal_variant"]
            if evaluation.get("review_unchanged"):
                unchanged[1] += 1
                unchanged[0] += bool(evaluation["metrics"]["case_resolution"])
            elif evaluation.get("p7"):
                p7[1] += 1
                p7[0] += bool(evaluation["metrics"]["case_resolution"])
            else:
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
        content_failures = collections.Counter(
            name
            for _, evaluation in rows
            for name, value in evaluation.get("content", {}).items()
            if value is False
        )
        table["/".join(key)] = {
            "runs": len(rows),
            # The main score counts what the agent did. The content checks are kept apart
            # and recomputed here, so a judge can be added later without rerunning anything.
            "scoring": evaluation_basis(rows),
            "responsibility": sum(v[0] for v in by_variant.values()),
            "responsibility_with_content": sum(
                bool(e["metrics"]["case_resolution"])
                and e["responsibility"]["content_checks_passed"]
                for _, e in rows
                if not e.get("p7")
            ),
            "content_failures": dict(content_failures),
            "by_variant": {k: f"{v[0]}/{v[1]}" for k, v in by_variant.items() if v[1]},
            "p7_human_request": f"{p7[0]}/{p7[1]}" if p7[1] else None,
            "review_unchanged": f"{unchanged[0]}/{unchanged[1]}" if unchanged[1] else None,
            "harmful": sum(bool(e["metrics"].get("harmful_action")) for _, e in rows),
            "procedural": sum(bool(e["metrics"].get("procedural_violation")) for _, e in rows),
            "malformed": sum(e["metrics"].get("malformed", e["metrics"].get("interface_errors", 0)) for _, e in rows),
            "policy_refused": sum(e["metrics"].get("policy_refused", 0) for _, e in rows),
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
