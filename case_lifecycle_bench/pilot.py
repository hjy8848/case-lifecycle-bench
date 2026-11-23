"""Generate and validate a small Retail order panel without model calls."""

import argparse
import json
from collections import Counter
from pathlib import Path
from random import Random

from .cases import t1, t2, t3, t9
from .environment import run_case
from .evaluation import evaluate_case
from .retail import load_retail_tools, retail_case


PILOT = (
    (t1.t1_case, t1.oracle, 8),
    (t2.t2_case, t2.oracle, 8),
    (t3.t3_case, t3.oracle, 7),
    (t9.t9_case, t9.oracle, 7),
)
SMOKE_ORDERS = {"#W4817420", "#W6304490", "#W5918442"}


def run_pilot(seed: int, *, include_trace: bool = False) -> dict:
    tools = load_retail_tools()
    order_ids = sorted(set(tools.db.orders) - SMOKE_ORDERS)
    Random(seed).shuffle(order_ids)
    used = set()
    results = []

    for fixture, oracle, target in PILOT:
        template = fixture()
        status = template["init"].get("order_status", "delivered")
        generated = 0
        for order_id in order_ids:
            if order_id in used or tools.db.orders[order_id].status != status:
                continue
            try:
                case = retail_case(template, order_id, tools=tools)
            except ValueError:
                continue
            evaluations = {}
            for wake in ("poll", "push"):
                run = run_case(case, oracle, wake=wake)
                evaluations[wake] = {"evaluation": evaluate_case(case, run)}
                if include_trace:
                    evaluations[wake]["run"] = run
            results.append({"case": case, "wake_modes": evaluations})
            used.add(order_id)
            generated += 1
            if generated == target:
                break
        if generated != target:
            raise ValueError(
                f"{template['template']}: generated {generated} of {target} cases"
            )

    checks = [
        row["evaluation"]["metrics"]
        for result in results
        for row in result["wake_modes"].values()
    ]
    return {
        "seed": seed,
        "tau_bench_version": "1.0.1",
        "agent": "oracle",
        "template_counts": dict(Counter(row["case"]["template"] for row in results)),
        "case_count": len(results),
        "passed": sum(
            row["case_resolution"] and not row["unsafe_action_rate"] for row in checks
        ),
        "runs": len(checks),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    result = run_pilot(args.seed, include_trace=args.trace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(
        f"{result['case_count']} cases, {result['passed']}/{result['runs']} runs passed"
    )
    if result["passed"] != result["runs"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
