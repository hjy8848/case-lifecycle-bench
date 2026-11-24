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
    parser.add_argument("--case", choices=(*CASES, "insurance-renewal"), default="t1")
    parser.add_argument("--wake", choices=("poll", "push"), default="poll")
    parser.add_argument("--agent", default="oracle")
    parser.add_argument(
        "--paired",
        action="store_true",
        help="Compare the lifecycle and single-session versions of a self-built T3 case",
    )
    parser.add_argument(
        "--memory",
        choices=("crm", "full-history", "ledger"),
        default="crm",
        help="Ledger is available for T1, T2 and T9",
    )
    parser.add_argument(
        "--retail-order", help="Seed the case from a τ-bench v1.0.1 Retail order"
    )
    parser.add_argument(
        "--quote-ready-at", help="Insurance quote release time, e.g. D2 14:00"
    )
    parser.add_argument(
        "--revise-quote-after-consent",
        action="store_true",
        help="Insurance variant: the insurer revises Q2 after the customer accepts it",
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
    parser.add_argument(
        "--expect-case-sha256", help="Refuse to run a changed case fixture"
    )
    args = parser.parse_args()
    insurance = args.case == "insurance-renewal"
    if (
        args.quote_ready_at is not None or args.revise_quote_after_consent
    ) and not insurance:
        parser.error("Quote options require --case insurance-renewal")

    if args.paired:
        if (
            args.case != "t3"
            or args.memory != "crm"
            or args.model
            or args.user_model
            or args.retail_order
            or args.expect_case_sha256
        ):
            parser.error(
                "--paired requires --case t3 with scripted agents and synthetic data"
            )
        if args.agent not in {"oracle", "premature-refund"}:
            parser.error("--paired supports oracle and premature-refund")
        from .industries.retail import retail_t3_case
        from .single_session import premature_refund_single, run_t3_pair

        case = retail_t3_case()
        options = {}
        if args.agent == "premature-refund":
            options = {
                "lifecycle_agent": t3.premature_refund,
                "single_agent": premature_refund_single,
            }
        pair = run_t3_pair(case, wake=args.wake, **options)
        if not args.trace:
            pair = {
                mode: {"evaluation": result["evaluation"]}
                for mode, result in pair.items()
            }
        print(
            json.dumps(
                {"case_id": case["case_id"], "agent": args.agent, **pair}, indent=2
            )
        )
        return

    if args.memory == "ledger" and args.case not in {"t1", "t2", "t9"}:
        parser.error("--memory ledger is currently supported for T1, T2 and T9")
    evaluator = evaluate_case
    if insurance:
        if args.memory != "crm" or args.user_model or args.retail_order:
            parser.error(
                "insurance-renewal uses CRM, synthetic data and a scripted customer"
            )
        from .industries.insurance import renewal
        from .industries.insurance.evaluation import evaluate_insurance

        try:
            case = renewal.renewal_case(
                args.quote_ready_at,
                revise_after_consent=args.revise_quote_after_consent,
            )
        except ValueError as error:
            parser.error(str(error))
        agents = {
            "oracle": renewal.oracle,
            "early-outreach": renewal.early_outreach,
            "missing-quote-consent": renewal.missing_quote_consent,
            "missed-notification": renewal.missed_notification,
        }
        evaluator = evaluate_insurance
    else:
        fixture, agents = CASES[args.case]
        case = fixture()
    if args.retail_order:
        try:
            case = retail_case(case, args.retail_order)
        except (ModuleNotFoundError, ValueError) as error:
            parser.error(str(error))
    case_hash = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
    if args.expect_case_sha256 and args.expect_case_sha256 != case_hash:
        parser.error("Case differs from the pilot artifact; regenerate the pilot")
    gateway = None
    user_gateway = None
    if args.user_model and not args.model:
        parser.error("--user-model requires --model")
    if args.model and args.agent != "oracle":
        parser.error("--model cannot be combined with a scripted error agent")
    user_model = None if insurance else args.user_model or args.model
    if args.model:
        key = os.environ.get("CASE_LIFECYCLE_API_KEY")
        base = os.environ.get("CASE_LIFECYCLE_API_BASE")
        if not (key and base):
            parser.error(
                "--model requires CASE_LIFECYCLE_API_BASE and CASE_LIFECYCLE_API_KEY"
            )
        gateway = ChatGateway(base, key)
        user_base = os.environ.get("CASE_LIFECYCLE_USER_API_BASE")
        user_thinking = os.environ.get("CASE_LIFECYCLE_USER_ENABLE_THINKING")
        if user_thinking not in (None, "true", "false"):
            parser.error("CASE_LIFECYCLE_USER_ENABLE_THINKING must be true or false")
        user_gateway = gateway
        if user_model and (user_base or user_thinking is not None):
            user_key = (
                os.environ.get("CASE_LIFECYCLE_USER_API_KEY") if user_base else key
            )
            user_gateway = ChatGateway(
                user_base or base,
                user_key,
                enable_thinking=(user_thinking == "true")
                if user_thinking is not None
                else None,
            )
        model_options = {}
        if insurance:
            from .industries.insurance import model as insurance_model

            model_options = {
                "policy": insurance_model.POLICY,
                "tools": insurance_model.TOOLS,
            }
        agent = model_agent(
            gateway,
            args.model,
            user_model,
            case,
            user_gateway=user_gateway,
            **model_options,
        )
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
        "evaluation": evaluator(case, run),
    }
    if gateway is not None:
        result["meta"] = {
            "api_base": gateway.api_base,
            "agent_model": args.model,
            "user_model": user_model,
            "memory": {
                "crm": "stateless_crm",
                "full-history": "full_history",
                "ledger": "case_ledger",
            }[args.memory],
            "case_sha256": case_hash,
            "temperature": 0,
            "model_calls": gateway.calls
            + (user_gateway.calls if user_gateway is not gateway else 0),
            "model_retries": gateway.retries
            + (user_gateway.retries if user_gateway is not gateway else 0),
        }
        if user_gateway is not gateway:
            result["meta"]["user_api_base"] = user_gateway.api_base
            result["meta"]["user_enable_thinking"] = user_gateway.enable_thinking
    if args.trace:
        result["run"] = run
    if ledger is not None:
        result["ledger_guard_events"] = ledger.guard_events
    if args.retail_order:
        result["retail_source"] = case["retail_source"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
