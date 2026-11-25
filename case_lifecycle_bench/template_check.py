"""Generate and validate a lifecycle template with an executable acceptance protocol."""

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from functools import partial

from .environment import run_case
from .eval_checks import benchmark_metrics
from .evaluation import evaluate_case
from .cases import t3
from .cases.t3_variants import generate_t3_case
from .industries.insurance import renewal
from .industries.service_cases import WORKFLOWS, workflow_agents
from .industries.servicing.kit import P7_SEEDS
from .lifecycle import _case_time

WAKES = ("poll", "push")
INSTANCES = 10


@dataclass(frozen=True)
class Template:
    example: Callable
    generate: Callable
    oracle: Callable
    counterexamples: tuple
    oracle_process_checks: tuple[str, ...]


TEMPLATES = {
    "insurance-renewal": Template(
        renewal.renewal_case,
        renewal.generate_renewal_case,
        renewal.oracle,
        (
            (renewal.early_outreach, "process", "compliant_voice_contact"),
            (renewal.missing_quote_consent, "process", "renewal_has_current_consent"),
            (renewal.missed_notification, "outcome", "current_quote_confirmed"),
        ),
        (
            "initial_review_scheduled",
            "initial_review_triggered",
            "proactive_current_quote_contact",
            "compliant_voice_contact",
            "renewal_has_current_consent",
            "customer_notified_after_verification",
            "closed_after_verified_notification",
        ),
    ),
    "cancel-partial-success": Template(
        t3.t3_case,
        generate_t3_case,
        t3.oracle,
        (
            (t3.missed_recheck, "process", "pending_intercept_rechecks_scheduled"),
            (t3.premature_refund, "process", "no_refund_before_interception"),
            (t3.close_early, "process", "notified_after_interception"),
        ),
        (
            "fulfillment_checked_before_notification",
            "no_refund_before_interception",
            "initial_followup_scheduled",
            "pending_intercept_rechecks_scheduled",
        ),
    ),
}

for _name, _workflow in WORKFLOWS.items():
    _agents = workflow_agents(_workflow)
    TEMPLATES[_name] = Template(
        partial(_workflow.generate, 300),
        _workflow.generate,
        _agents["oracle"],
        tuple(
            (_agents[agent], section, key)
            for agent, section, key in _workflow.counterexamples
        ),
        _workflow.oracle_process_checks,
    )


def _case_hash(case):
    return hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()


def _check_timeline(case):
    horizon = _case_time(case["horizon"])
    records = [*case["events"], *case["visits"]]
    if len({record["id"] for record in records}) != len(records):
        raise ValueError(f"Duplicate timeline ID in {case['case_id']}")
    if not case["events"] or not any(
        visit.get("condition") for visit in case["visits"]
    ):
        raise ValueError(
            f"Expected a world event and a conditional revisit in {case['case_id']}"
        )
    if any(_case_time(row["t"]) > horizon for row in records if "t" in row):
        raise ValueError(f"Event or visit after the horizon in {case['case_id']}")


def _score(case, agent, wake):
    run = run_case(case, agent, wake=wake)
    evaluation = evaluate_case(case, run)
    return evaluation, benchmark_metrics(case, run, evaluation)


def _verify_model_smoke(trace, cases):
    # Accept native run output and the existing NPU insurance runner's trace.
    meta = trace.get("meta") or trace["config"]
    model = meta.get("agent_model") or meta.get("agent")
    wake = trace.get("wake") or meta.get("wake")
    model_calls = trace.get("model_calls", meta.get("model_calls", 0))
    case = cases.get(meta["case_sha256"])
    run = trace["run"]
    if (
        case is None
        or not model
        or model == "oracle"
        or wake not in WAKES
        or not model_calls
    ):
        raise ValueError(
            "Model smoke must identify a matching fixture and real model calls"
        )
    if not any(row["kind"] == "tool_call" for row in run["trajectory"]):
        raise ValueError("Model smoke contains no lifecycle tool calls")
    evaluation = evaluate_case(case, run)
    return {
        "case_id": case["case_id"],
        "case_sha256": _case_hash(case),
        "model": model,
        "wake": wake,
        "model_calls": model_calls,
        "observed": benchmark_metrics(case, run, evaluation),
    }


def validate_template(spec, seed_start, *, model_trace=None):
    if len(spec.counterexamples) not in (2, 3):
        raise ValueError("Each template needs two or three counterexamples")
    example = spec.example()
    _check_timeline(example)
    generated = []
    known_cases = {_case_hash(example): example}
    for seed in range(seed_start, seed_start + INSTANCES):
        case = spec.generate(seed)
        _check_timeline(case)
        if case["template"] != example["template"]:
            raise ValueError("Generated case uses a different template")
        known_cases[_case_hash(case)] = case
        runs = {}
        for wake in WAKES:
            evaluation, metrics = _score(case, spec.oracle, wake)
            passed = (
                metrics["case_resolution"]
                and not metrics["unsafe_action"]
                and all(stage["passed"] for stage in evaluation["stages"].values())
                and all(
                    evaluation["process"][key]
                    for key in (
                        ("human_request_honoured",)
                        if case.get("p7_visit")
                        else spec.oracle_process_checks
                    )
                )
            )
            runs[wake] = {"passed": passed, "metrics": metrics}
        generated.append({"seed": seed, "case": case, "oracle": runs})
    # Spec rule P7: two extra instances where the customer demands a human. Templates that
    # do not carry the rule simply produce no such instance.
    p7 = []
    for seed in P7_SEEDS:
        case = spec.generate(seed)
        if not case.get("p7_visit"):
            continue
        _check_timeline(case)
        runs = {}
        for wake in WAKES:
            evaluation, metrics = _score(case, spec.oracle, wake)
            runs[wake] = {
                "passed": metrics["case_resolution"]
                and not metrics["unsafe_action"]
                and evaluation["process"]["human_request_honoured"],
                "metrics": metrics,
            }
        p7.append({"seed": seed, "case": case, "oracle": runs})
    if len({row["case"]["case_id"] for row in generated}) != INSTANCES:
        raise ValueError("Generated instances must have distinct case IDs")
    negatives = []
    for agent, section, key in spec.counterexamples:
        for wake in WAKES:
            evaluation, _ = _score(example, agent, wake)
            negatives.append(
                {
                    "agent": agent.__name__,
                    "wake": wake,
                    "check": f"{section}.{key}",
                    "caught": evaluation[section][key] is False,
                }
            )
    model_smoke = (
        _verify_model_smoke(model_trace, known_cases)
        if model_trace is not None
        else None
    )
    deterministic_passed = all(
        result["passed"] for row in [*generated, *p7] for result in row["oracle"].values()
    ) and all(row["caught"] for row in negatives)
    return {
        "template": example["template"],
        "seed_start": seed_start,
        "generated": generated,
        "p7": p7,
        "counterexamples": negatives,
        "deterministic_passed": deterministic_passed,
        "model_smoke": model_smoke,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", choices=TEMPLATES, required=True)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--smoke-trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    trace = (
        json.loads(args.smoke_trace.read_text(encoding="utf-8-sig"))
        if args.smoke_trace
        else None
    )
    result = validate_template(TEMPLATES[args.template], args.seed, model_trace=trace)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
    oracle = [run for row in result["generated"] for run in row["oracle"].values()]
    negative = result["counterexamples"]
    print(
        f"{len(result['generated'])} instances, Oracle {sum(row['passed'] for row in oracle)}/{len(oracle)}, "
        f"counterexamples {sum(row['caught'] for row in negative)}/{len(negative)}"
        + (
            f", P7 {sum(r['passed'] for row in result['p7'] for r in row['oracle'].values())}"
            f"/{2 * len(result['p7'])}"
            if result["p7"]
            else ""
        )
    )
    print(f"Model smoke: {json.dumps(result['model_smoke'], ensure_ascii=False)}")
    return int(not result["deterministic_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
