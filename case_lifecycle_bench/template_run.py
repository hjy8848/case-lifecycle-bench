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
from .runnable_templates import ALL
from .industries.servicing.kit import P7_SEEDS, UNCHANGED_SEEDS
from .model_agent import ChatGateway, _customer_reply, model_agent
from .outcomes import summarize_by_variant


GROUPS = ("E0", "E2", "E3", "A1", "A2")  # E1 (full history) and E4 (single session) have their own runners
TEST_SEEDS = {*range(300, 310), 390, 391}


def _ledger_config(group):
    from .case_ledger import LedgerConfig

    return {"E2": LedgerConfig.todo, "E3": LedgerConfig.full, "A1": LedgerConfig.no_keep, "A2": LedgerConfig.no_earn}[group]()


def run_one(
    name, seed, wake, model, user_model, base, output, *, enable_thinking, llm_customer=False, group="E0"
):
    if group != "E0" and seed in TEST_SEEDS and not os.environ.get("LEDGER_FROZEN"):
        raise ValueError(f"Ledger group {group} may not run on test seed {seed} before the freeze")
    workflow = ALL[name]
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
        "memory": "stateless_crm" if group == "E0" else f"case_ledger_{group}",
        "agent_model": model,
        "user_model": user_model,
        "temperature": 0,
        "enable_thinking": enable_thinking,
        "customer_mode": "llm" if llm_customer else "script",
        # Tool descriptions and policy are frozen with the question set. A change shows
        # up here, so a result can always be tied to the exact wording it was run with.
        "tools_sha256": hashlib.sha256(
            json.dumps(workflow.tools, sort_keys=True).encode()
        ).hexdigest(),
        "policy_sha256": hashlib.sha256(workflow.policy.encode()).hexdigest(),
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
    book = None
    if group == "E0":
        agent = model_agent(gateway, model, user_model, case, policy=workflow.policy, tools=workflow.tools)
    else:
        from .case_ledger import PromiseBook

        config = _ledger_config(group)
        book = PromiseBook(
            config,
            policy=workflow.policy,
            ask=lambda prompt: gateway.reply(model, [{"role": "user", "content": prompt}]).get("content"),
            push=(wake == "push"),
        )
        inner = model_agent(
            gateway, model, user_model, case, policy=workflow.policy, tools=workflow.tools, ledger_tools=config
        )
        agent = book.wrap(inner)
        agent.invalid_arguments, agent.invalid_log = 0, []
    customer = None
    if llm_customer:
        # The simulator answers on outbound calls, with the same model and replica.
        def customer(visit, dialogue):
            return _customer_reply(gateway, user_model, visit, dialogue)

    run = run_case(case, agent, wake=wake, customer=customer)
    evaluation = evaluate_case(case, run)
    counter = inner if book is not None else agent
    meta.update(
        model_calls=gateway.calls,
        model_retries=gateway.retries,
        api_base=base,
        invalid_arguments=counter.invalid_arguments,
        invalid_calls=counter.invalid_log,
    )
    record = {
        "case_id": case["case_id"],
        "meta": meta,
        "observed": benchmark_metrics(case, run, evaluation),
        "evaluation": evaluation,
        "run": run,
    }
    if book is not None:
        record["ledger"] = book.log()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return record


def summarize(records):
    p7_rows = [row for row in records if row["evaluation"].get("p7")]
    records = [row for row in records if not row["evaluation"].get("p7")]
    rows = [
        {
            "terminal_variant": row["evaluation"]["terminal_variant"],
            "case_resolution": row["evaluation"]["metrics"]["case_resolution"],
            "wrongful_closure": row["evaluation"]["metrics"]["wrongful_closure"],
        }
        for row in records
    ]
    n = len(records)
    calls = sum(row["evaluation"]["metrics"]["tool_calls"] for row in records)
    wrong = sum(row["evaluation"]["metrics"]["interface_errors"] for row in records)
    malformed = sum(row["meta"].get("invalid_arguments", 0) for row in records)
    return {
        "runs": n,
        "interface": {
            "tool_calls": calls,
            "errors": wrong + malformed,
            "error_rate": (wrong + malformed) / (calls + malformed) if calls else None,
            "runs_with_an_error": sum(
                row["evaluation"]["metrics"]["interface_errors"]
                + row["meta"].get("invalid_arguments", 0)
                > 0
                for row in records
            ),
        },
        "responsibility": sum(bool(r["case_resolution"]) for r in rows),
        "unsafe": sum(
            bool(row["evaluation"]["metrics"]["unsafe_action_rate"]) for row in records
        ),
        "wrongful_closure": sum(r["wrongful_closure"] for r in rows),
        "by_variant": summarize_by_variant(rows),
        "p7_human_request": {
            "runs": len(p7_rows),
            "honoured": sum(bool(r["evaluation"]["metrics"]["case_resolution"]) for r in p7_rows),
        },
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
    parser.add_argument("--template", choices=ALL, required=True)
    parser.add_argument("--start-seed", type=int, default=300)
    parser.add_argument(
        "--p7",
        action="store_true",
        help="also run the two extra instances where the customer demands a human (spec rule P7)",
    )
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--wakes", default="poll")
    parser.add_argument("--model", required=True)
    parser.add_argument("--user-model", help="defaults to --model")
    parser.add_argument("--api-bases", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--group", choices=GROUPS, default="E0", help="memory group; E0 is the CRM-only baseline")
    parser.add_argument(
        "--workers-per-replica",
        type=int,
        default=1,
        help="runs in flight on each replica; a vLLM replica batches many at once",
    )
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
    seeds = list(range(args.start_seed, args.start_seed + args.seeds))
    if args.p7:
        # Only templates that carry rule P7 have such instances.
        seeds += [
            seed
            for seed in (*P7_SEEDS, *UNCHANGED_SEEDS)
            if seed not in seeds
            and (
                ALL[args.template].generate(seed).get("p7_visit")
                or ALL[args.template].generate(seed).get("review_unchanged")
            )
        ]
    jobs = [(seed, wake) for seed in seeds for wake in wakes]
    records, failures = [], []
    if args.workers_per_replica < 1:
        parser.error("--workers-per-replica must be positive")
    # Jobs are dealt to the replicas in turn; each replica runs at most this many at once.
    pools = [
        ThreadPoolExecutor(max_workers=args.workers_per_replica) for _ in args.api_bases
    ]
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
                group=args.group,
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
