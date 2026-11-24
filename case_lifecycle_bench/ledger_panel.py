"""Run the same lifecycle cases with CRM, full history and a generic task ledger."""

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from .cases import t1, t2, t3, t9
from .environment import run_case
from .eval_checks import benchmark_metrics
from .evaluation import evaluate_case
from .history import FullHistory
from .industries.insurance import model as insurance_model
from .industries.insurance import renewal
from .ledger import TaskLedger
from .model_agent import POLICY, TOOLS, ChatGateway, model_agent
from .t3_id_sweep import sweep_case

MEMORIES = ("crm", "full-history", "task-ledger")
CASES = {
    "t1": (t1.t1_case, t1.oracle),
    "t2": (t2.t2_case, t2.oracle),
    "t3": (lambda: sweep_case("numeric", 1), t3.oracle),
    "t9": (t9.t9_case, t9.oracle),
    "insurance-renewal": (lambda: renewal.generate_renewal_case(301), renewal.oracle),
}
_COMMITMENT_NOTE = "\nFor an explicit promise that must be tracked, record a CRM note beginning with Commitment: and include the agreed date or condition."


def run_case_group(name, model, user_model, base, key, output):
    fixture, oracle = CASES[name]
    case = fixture()
    fingerprint = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
    insurance = name == "insurance-renewal"
    policy = insurance_model.POLICY if insurance else POLICY
    tools = insurance_model.TOOLS if insurance else TOOLS
    customer = None if insurance or not model else user_model or model
    rows = []
    for memory in MEMORIES:
        gateway = ChatGateway(base, key, enable_thinking=False) if model else None
        agent = (
            model_agent(
                gateway,
                model,
                customer,
                case,
                policy=policy + _COMMITMENT_NOTE,
                tools=tools,
            )
            if gateway
            else oracle
        )
        if memory == "full-history":
            agent = FullHistory().wrap(agent)
        elif memory == "task-ledger":
            agent = TaskLedger().wrap(agent)
        run = run_case(case, agent, wake="poll")
        verdict = evaluate_case(case, run)
        metrics = benchmark_metrics(case, run, verdict)
        meta = {
            "template": name,
            "case_id": case["case_id"],
            "case_sha256": fingerprint,
            "memory": memory,
            "wake": "poll",
            "agent_model": model or "oracle",
            "customer": "scripted" if customer is None else customer,
            "model_calls": gateway.calls if gateway else 0,
            "model_retries": gateway.retries if gateway else 0,
            "temperature": 0,
            "enable_thinking": False,
        }
        row = {"meta": meta, "metrics": metrics}
        with (output / f"{name}--{memory}.json").open("x", encoding="utf-8") as file:
            json.dump(
                {**row, "evaluation": verdict, "run": run},
                file,
                ensure_ascii=False,
                indent=2,
            )
        print(
            f"{name} {memory} resolved={metrics['case_resolution']} unsafe={metrics['unsafe_action']}",
            flush=True,
        )
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--model", help="Omit to verify the five case groups with Oracle"
    )
    parser.add_argument(
        "--user-model", help="Retail customer model, defaulting to --model"
    )
    parser.add_argument(
        "--api-bases", nargs="+", help="Inference replicas for the five case groups"
    )
    args = parser.parse_args()
    if args.user_model and not args.model:
        parser.error("--user-model requires --model")
    bases = args.api_bases or [os.environ.get("CASE_LIFECYCLE_API_BASE")]
    key = os.environ.get("CASE_LIFECYCLE_API_KEY")
    if args.model and (
        not all(bases)
        or (
            not key
            and any(
                urlsplit(base).hostname not in {"127.0.0.1", "::1", "localhost"}
                for base in bases
            )
        )
    ):
        parser.error(
            "Provide an inference endpoint and a key for non-loopback endpoints"
        )
    args.output.mkdir(parents=True, exist_ok=False)

    # Each template's three arms stay on the same replica, with serial calls per replica.
    def run_replica(index):
        return [
            row
            for name in tuple(CASES)[index :: len(bases)]
            for row in run_case_group(
                name, args.model, args.user_model, bases[index], key, args.output
            )
        ]

    with ThreadPoolExecutor(max_workers=len(bases)) as pool:
        results = [
            row for group in pool.map(run_replica, range(len(bases))) for row in group
        ]
    with (args.output / "summary.json").open("x", encoding="utf-8") as file:
        json.dump(
            {"runs": len(results), "results": results},
            file,
            ensure_ascii=False,
            indent=2,
        )
    print(f"{len(results)} traces completed", flush=True)
    return int(
        not args.model and any(not row["metrics"]["case_resolution"] for row in results)
    )


if __name__ == "__main__":
    raise SystemExit(main())
