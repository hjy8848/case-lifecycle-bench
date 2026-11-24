"""Check that a template's terminal variants are scored as responsibility.

The Oracle must be responsible on every instance in both wake modes. Agents that do
nothing, close early or refund early must never pass on any instance, and the
reported rates are split by variant.

    python -m case_lifecycle_bench.variant_check --seed 300
"""

import argparse
import json

from .cases import t3
from .cases.t3_variants import generate_t3_case
from .environment import run_case
from .evaluation import evaluate_case
from .outcomes import VARIANTS, summarize_by_variant

WAKES = ("poll", "push")
INSTANCES = 10
EXPECTED_SPLIT = {"resolved": (5, 6), "wait": (2, 3), "escalate": (1, 2)}

TEMPLATES = {
    "cancel-partial-success": (
        generate_t3_case,
        t3.oracle,
        {
            "do_nothing": t3.do_nothing,
            "close_early": t3.close_early,
            "premature_refund": t3.premature_refund,
        },
    )
}


def _row(case, agent, wake):
    evaluation = evaluate_case(case, run_case(case, agent, wake=wake))
    metrics = evaluation["metrics"]
    return {
        "terminal_variant": case["terminal_variant"],
        "case_resolution": bool(metrics["case_resolution"]),
        "wrongful_closure": metrics["wrongful_closure"],
        "unsafe_action": bool(metrics["unsafe_action_rate"]),
        "business_outcome": evaluation["business_outcome"],
    }


def check_variants(name: str, seed_start: int) -> dict:
    generate, oracle, negatives = TEMPLATES[name]
    cases = [generate(seed) for seed in range(seed_start, seed_start + INSTANCES)]
    split = {
        variant: sum(case["terminal_variant"] == variant for case in cases)
        for variant in VARIANTS
    }
    oracle_rows = [_row(case, oracle, wake) for case in cases for wake in WAKES]
    negative_rows = {
        agent_name: [_row(case, agent, "poll") for case in cases]
        for agent_name, agent in negatives.items()
    }
    return {
        "template": name,
        "seed_start": seed_start,
        "split": split,
        "split_ok": all(
            EXPECTED_SPLIT[variant][0] <= count <= EXPECTED_SPLIT[variant][1]
            for variant, count in split.items()
        ),
        "oracle": summarize_by_variant(oracle_rows),
        "oracle_passed": all(row["case_resolution"] for row in oracle_rows),
        "counterexamples": {
            agent_name: {
                "never_passes": not any(row["case_resolution"] for row in rows),
                "by_variant": summarize_by_variant(rows),
            }
            for agent_name, rows in negative_rows.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", choices=TEMPLATES, default="cancel-partial-success")
    parser.add_argument("--seed", type=int, default=300)
    args = parser.parse_args()
    result = check_variants(args.template, args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    passed = (
        result["split_ok"]
        and result["oracle_passed"]
        and all(row["never_passes"] for row in result["counterexamples"].values())
    )
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
