"""Run a CaseLifecycleBench smoke case."""

import argparse
import hashlib
import json
import os

from .cases import t1, t2, t3, t9
from .environment import run_case
from .evaluation import evaluate_case
from .model_agent import ChatGateway, model_agent
from .retail import retail_case

CASES = {
    "t1": (
        t1.t1_case,
        {
            "oracle": t1.oracle,
            "missed-followup": t1.missed_followup,
            "duplicate-refund": t1.duplicate_refund,
        },
    ),
    "t2": (t2.t2_case, {"oracle": t2.oracle, "blind-retry": t2.blind_retry}),
    "t3": (
        t3.t3_case,
        {
            "oracle": t3.oracle,
            "missed-recheck": t3.missed_recheck,
            "premature-refund": t3.premature_refund,
        },
    ),
    "t9": (t9.t9_case, {"oracle": t9.oracle, "duplicate-ticket": t9.duplicate_ticket}),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, default="t1")
    parser.add_argument("--wake", choices=("poll", "push"), default="poll")
    parser.add_argument("--agent", default="oracle")
    parser.add_argument(
        "--memory",
        choices=("crm", "full-history", "ledger"),
        default="crm",
        help="Ledger is available for T1",
    )
    parser.add_argument(
        "--retail-order", help="Seed the case from a τ-bench v1.0.1 Retail order"
    )
    parser.add_argument(
        "--model", help="Exact gateway model ID for a stateless model agent"
    )
    parser.add_argument(
        "--user-model", help="Gateway user-simulator model; defaults to --model"
    )
    parser.add_argument(
        "--trace", action="store_true", help="Include the trajectory and state"
    )
    args = parser.parse_args()

    if args.memory == "ledger" and args.case != "t1":
        parser.error("--memory ledger is currently supported for T1 only")
    fixture, agents = CASES[args.case]
    case = fixture()
    if args.retail_order:
        try:
            case = retail_case(case, args.retail_order)
        except (ModuleNotFoundError, ValueError) as error:
            parser.error(str(error))
    gateway = None
    if args.user_model and not args.model:
        parser.error("--user-model requires --model")
    if args.model and args.agent != "oracle":
        parser.error("--model cannot be combined with a scripted error agent")
    if args.model:
        key = os.environ.get("CASE_LIFECYCLE_API_KEY")
        base = os.environ.get("CASE_LIFECYCLE_API_BASE")
        if not (key and base):
            parser.error(
                "--model requires CASE_LIFECYCLE_API_BASE and CASE_LIFECYCLE_API_KEY"
            )
        gateway = ChatGateway(base, key)
        agent = model_agent(gateway, args.model, args.user_model or args.model, case)
    else:
        if args.agent not in agents:
            parser.error(f"agents for {args.case}: {', '.join(agents)}")
        agent = agents[args.agent]
    ledger = None
    if args.memory == "ledger":
        from .ledger import CaseLedger

        ledger = CaseLedger()
        agent = ledger.wrap(agent)
    elif args.memory == "full-history":
        from .history import FullHistory

        agent = FullHistory().wrap(agent)
    run = run_case(case, agent, wake=args.wake)
    result = {
        "case_id": case["case_id"],
        "agent": args.model or args.agent,
        "wake": args.wake,
        "memory": args.memory,
        "evaluation": evaluate_case(case, run),
    }
    if gateway is not None:
        result["meta"] = {
            "api_base": gateway.api_base,
            "agent_model": args.model,
            "user_model": args.user_model or args.model,
            "memory": {
                "crm": "stateless_crm",
                "full-history": "full_history",
                "ledger": "case_ledger",
            }[args.memory],
            "case_sha256": hashlib.sha256(
                json.dumps(case, sort_keys=True).encode()
            ).hexdigest(),
            "temperature": 0,
            "model_calls": gateway.calls,
        }
    if args.trace:
        result["run"] = run
    if ledger is not None:
        result["ledger_guard_events"] = ledger.guard_events
    if args.retail_order:
        result["retail_source"] = case["retail_source"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
