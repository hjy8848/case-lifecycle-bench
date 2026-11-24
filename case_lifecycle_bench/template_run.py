"""Run a model agent on generated instances of a card-based template.

The agent and the customer simulator are chat models on OpenAI-compatible replicas.
Chat visits use the simulator; outbound contacts follow the instance's script.
Existing result files are reused, so an interrupted batch continues where it stopped.

    python -m case_lifecycle_bench.template_run --template credit-delinquency \
        --model qwen3.6-35b-a3b --api-bases http://127.0.0.1:18101/v1 --seeds 10 \
        --output runs/template-run --disable-thinking
"""

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

from .environment import run_case
from .eval_checks import benchmark_metrics
from .evaluation import evaluate_case
from .industries.service_cases import WORKFLOWS
from .model_agent import ChatGateway, model_agent
from .outcomes import summarize_by_variant


def run_one(
    name, seed, wake, model, user_model, base, output, *, enable_thinking, llm_customer=False
):
    workflow = WORKFLOWS[name]
    case = workflow.generate(seed)
    if llm_customer:
        # The simulator also voices outbound calls, and tool results stop carrying
        # the customer's private intent.
        case["customer_mode"] = "llm"
    path = output / f"{name}--{case['terminal_variant']}--{seed}--{wake}.json"
    meta = {
        "template": name,
        "seed": seed,
        "wake": wake,
        "memory": "stateless_crm",
        "agent_model": model,
        "user_model": user_model,
        "temperature": 0,
        "enable_thinking": enable_thinking,
        "customer_mode": "llm" if llm_customer else "script",
        "case_sha256": hashlib.sha256(
            json.dumps(case, sort_keys=True).encode()
        ).hexdigest(),
    }
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if any(record["meta"].get(key) != value for key, value in meta.items()):
            raise ValueError(f"Existing result uses another configuration: {path.name}")
        return record
    gateway = ChatGateway(base, os.environ.get("CASE_LIFECYCLE_API_KEY"), enable_thinking=enable_thinking)
    agent = model_agent(
        gateway,
        model,
        user_model,
        case,
        policy=workflow.policy,
        tools=workflow.tools,
    )
    run = run_case(case, agent, wake=wake)
    evaluation = evaluate_case(case, run)
    meta.update(model_calls=gateway.calls, model_retries=gateway.retries, api_base=base)
    record = {
        "case_id": case["case_id"],
        "meta": meta,
        "observed": benchmark_metrics(case, run, evaluation),
        "evaluation": evaluation,
        "run": run,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return record


def summarize(records):
    rows = [
        {
            "terminal_variant": row["evaluation"]["terminal_variant"],
            "case_resolution": row["evaluation"]["metrics"]["case_resolution"],
            "wrongful_closure": row["evaluation"]["metrics"]["wrongful_closure"],
        }
        for row in records
    ]
    n = len(records)
    return {
        "runs": n,
        "responsibility": sum(bool(r["case_resolution"]) for r in rows),
        "unsafe": sum(
            bool(row["evaluation"]["metrics"]["unsafe_action_rate"]) for row in records
        ),
        "wrongful_closure": sum(r["wrongful_closure"] for r in rows),
        "by_variant": summarize_by_variant(rows),
        "failed_checks": _failed_checks(records),
    }


def _failed_checks(records):
    counts = {}
    for row in records:
        for key, value in row["evaluation"]["process"].items():
            if value is False:
                counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", choices=WORKFLOWS, required=True)
    parser.add_argument("--start-seed", type=int, default=300)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--wakes", default="poll")
    parser.add_argument("--model", required=True)
    parser.add_argument("--user-model", help="defaults to --model")
    parser.add_argument("--api-bases", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument(
        "--llm-customer",
        action="store_true",
        help="simulate outbound-call customers with the user model, not the script",
    )
    args = parser.parse_args()
    wakes = [w for w in args.wakes.split(",") if w]
    if any(w not in ("poll", "push") for w in wakes):
        parser.error("--wakes accepts poll and push")
    if not os.environ.get("CASE_LIFECYCLE_API_KEY") and any(
        urlsplit(base).hostname not in {"127.0.0.1", "::1", "localhost"}
        for base in args.api_bases
    ):
        parser.error("Non-loopback gateways require CASE_LIFECYCLE_API_KEY")
    args.output.mkdir(parents=True, exist_ok=True)
    thinking = False if args.disable_thinking else None
    user_model = args.user_model or args.model
    jobs = [
        (seed, wake)
        for seed in range(args.start_seed, args.start_seed + args.seeds)
        for wake in wakes
    ]
    records, failures = [], []
    # One worker per replica keeps each replica's runs sequential.
    pools = [ThreadPoolExecutor(max_workers=1) for _ in args.api_bases]
    try:
        futures = {}
        for index, (seed, wake) in enumerate(jobs):
            replica = index % len(args.api_bases)
            future = pools[replica].submit(
                run_one,
                args.template,
                seed,
                wake,
                args.model,
                user_model,
                args.api_bases[replica],
                args.output,
                enable_thinking=thinking,
                llm_customer=args.llm_customer,
            )
            futures[future] = (seed, wake)
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as error:  # a failed run must not stop the batch
                failures.append({"job": futures[future], "error": f"{type(error).__name__}: {error}"})
    finally:
        for pool in pools:
            pool.shutdown()
    summary = summarize(records) if records else {}
    print(json.dumps({"template": args.template, "failures": failures, **summary}, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
