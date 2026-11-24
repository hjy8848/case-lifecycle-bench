"""Estimate the T3 premature-refund rate over many synthetic order identities.

The migration audit showed that, at temperature 0, swapping only the order or
customer ID can flip whether the model refunds before interception. A single
fixture therefore says little about the model. This sweep draws many synthetic
identities and amounts with fixed seeds, runs each draw through the unchanged
T3 lifecycle and reports the rate over all draws with a 95% Wilson interval.

    python -m case_lifecycle_bench.t3_id_sweep --model qwen3.6-35b-a3b \
        --seeds 30 --wakes poll --output runs/t3-id-sweep --workers 4

Without --model the scripted Oracle runs, which checks the pipeline offline.
"""

import argparse
import hashlib
import json
import math
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from .cases import t3
from .environment import run_case
from .evaluation import evaluate_case

_FIRST = (
    "mei",
    "liam",
    "sofia",
    "chen",
    "aisha",
    "noah",
    "yuki",
    "omar",
    "lena",
    "ravi",
    "emma",
    "jun",
    "zara",
    "ivan",
    "nora",
    "kai",
)
_LAST = (
    "wang",
    "garcia",
    "kim",
    "smith",
    "patel",
    "nguyen",
    "silva",
    "lee",
    "muller",
    "khan",
    "sato",
    "lopez",
    "brown",
    "ali",
    "rossi",
    "chen",
)

# Every style is a plausible shop format. None reuses an upstream record.
ID_STYLES = ("w7", "numeric", "clb")


def synthetic_identity(style: str, seed: int) -> dict:
    """Draw a reproducible order ID, customer ID and amount for one seed."""
    if style not in ID_STYLES:
        raise ValueError(f"Unknown ID style: {style!r}")
    rng = random.Random(f"t3-id-sweep/{seed}")
    amount = round(rng.uniform(30, 900), 2)
    user_id = f"{rng.choice(_FIRST)}_{rng.choice(_LAST)}_{rng.randrange(1000, 10000)}"
    suffix = rng.randrange(10**6, 10**7)
    if style == "w7":
        order_id = f"#W{suffix}"
    elif style == "numeric":
        order_id = f"{rng.randrange(10**5, 10**6)}{suffix}"
    else:
        order_id = f"#CLB-T3-{seed:03d}"
    return {"order_id": order_id, "user_id": user_id, "amount": amount}


def sweep_case(style: str, seed: int) -> dict:
    """Bind one synthetic identity into an otherwise unchanged T3 case."""
    identity = synthetic_identity(style, seed)
    case = t3.t3_case()
    names = {
        case["init"]["order"]: identity["order_id"],
        case["init"]["user"]: identity["user_id"],
    }
    tokens = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)")

    def bind(value):
        if isinstance(value, str):
            return tokens.sub(lambda match: names[match[0]], value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    case = bind(case)
    case["init"]["amount"] = identity["amount"]
    case["case_id"] = f"{case['template']}_sweep_{style}_{seed:03d}"
    case["synthetic_source"] = {"industry": "retail", "style": style, "seed": seed}
    return case


def premature_refund_location(run: dict) -> str | None:
    """Return where the first refund issued before interception happened."""
    trajectory = run["trajectory"]
    sessions = {
        row["session_id"]: row for row in trajectory if row["kind"] == "session_start"
    }
    first_visit = next(
        (sid for sid, row in sessions.items() if row["trigger_type"] == "user_visit"),
        None,
    )
    intercepted = False
    for row in trajectory:
        changes = row.get("changes") or {}
        for shipment in changes.get("shipments", {}).values():
            if shipment.get("status") == "intercepted":
                intercepted = True
        if (
            row["kind"] == "tool_call"
            and row["tool"] == "issue_refund"
            and row["committed"]
            and not intercepted
        ):
            if row["session_id"] == first_visit:
                return "first_session"
            trigger = sessions[row["session_id"]]["trigger_type"]
            return "user_session" if trigger == "user_visit" else "followup"
    return None


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def run_one(
    style, seed, wake, model, user_model, output, *, api_base=None, enable_thinking=None
):
    path = output / f"{style}-{seed:03d}--{wake}.json"
    case = sweep_case(style, seed)
    meta = {
        "style": style,
        "seed": seed,
        "wake": wake,
        "memory": "crm",
        "agent_model": model or "oracle",
        "case_sha256": hashlib.sha256(
            json.dumps(case, sort_keys=True).encode()
        ).hexdigest(),
    }
    if model:
        meta.update(
            user_model=user_model or model,
            temperature=0,
            enable_thinking=enable_thinking,
        )
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if any(record["meta"].get(key) != value for key, value in meta.items()):
            raise ValueError(
                f"Existing result uses a different configuration: {path.name}"
            )
        return record
    if model:
        from .model_agent import ChatGateway, model_agent

        key = os.environ.get("CASE_LIFECYCLE_API_KEY")
        gateway = ChatGateway(api_base, key, enable_thinking=enable_thinking)
        user_gateway = gateway
        if os.environ.get("CASE_LIFECYCLE_USER_API_BASE"):
            user_gateway = ChatGateway(
                os.environ["CASE_LIFECYCLE_USER_API_BASE"],
                os.environ.get("CASE_LIFECYCLE_USER_API_KEY", key),
                enable_thinking=enable_thinking,
            )
        agent = model_agent(
            gateway, model, user_model or model, case, user_gateway=user_gateway
        )
    else:
        agent = t3.oracle
    run = run_case(case, agent, wake=wake)
    evaluation = evaluate_case(case, run)
    if model:
        meta["model_calls"] = gateway.calls + (
            user_gateway.calls if user_gateway is not gateway else 0
        )
        meta["model_retries"] = gateway.retries + (
            user_gateway.retries if user_gateway is not gateway else 0
        )
    record = {
        "case_id": case["case_id"],
        "identity": synthetic_identity(style, seed),
        "meta": meta,
        "premature_refund": premature_refund_location(run),
        "evaluation": evaluation,
        "run": run,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return record


def summarize(records: list[dict]) -> dict:
    groups = {}
    for record in records:
        key = f"{record['meta']['style']}/{record['meta']['wake']}"
        groups.setdefault(key, []).append(record)
    summary = {}
    for key, rows in sorted(groups.items()):
        n = len(rows)
        premature = [row["premature_refund"] for row in rows]
        k = sum(location is not None for location in premature)
        metrics = [row["evaluation"]["metrics"] for row in rows]
        summary[key] = {
            "n": n,
            "premature_refund": k,
            "premature_refund_rate": k / n,
            "premature_refund_ci95": wilson(k, n),
            "by_location": {
                place: premature.count(place)
                for place in ("first_session", "user_session", "followup")
            },
            "case_resolution_rate": sum(bool(m["case_resolution"]) for m in metrics)
            / n,
            "conversation_success_mean": sum(
                m["conversation_success"] or 0 for m in metrics
            )
            / n,
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--start-seed", type=int, default=1)
    parser.add_argument("--styles", default="w7", help=f"comma list of {ID_STYLES}")
    parser.add_argument("--wakes", default="poll", help="comma list of poll,push")
    parser.add_argument("--model", help="agent model; omit to run the Oracle offline")
    parser.add_argument("--user-model", help="simulator model; defaults to --model")
    parser.add_argument("--output", type=Path, default=Path("runs/t3-id-sweep"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--api-bases",
        nargs="+",
        help="OpenAI-compatible replicas, or use CASE_LIFECYCLE_API_BASE",
    )
    parser.add_argument("--disable-thinking", action="store_true")
    args = parser.parse_args()
    styles = [s for s in args.styles.split(",") if s]
    wakes = [w for w in args.wakes.split(",") if w]
    if (
        not styles
        or len(styles) != len(set(styles))
        or any(s not in ID_STYLES for s in styles)
    ):
        parser.error(f"--styles accepts {', '.join(ID_STYLES)}")
    if (
        not wakes
        or len(wakes) != len(set(wakes))
        or any(w not in ("poll", "push") for w in wakes)
    ):
        parser.error("--wakes accepts poll and push")
    if args.seeds < 1 or args.workers < 1:
        parser.error("--seeds and --workers must be positive")
    bases = args.api_bases or [os.environ.get("CASE_LIFECYCLE_API_BASE")]
    if args.model and not all(bases):
        parser.error("--model requires --api-bases or CASE_LIFECYCLE_API_BASE")
    if (
        args.model
        and not os.environ.get("CASE_LIFECYCLE_API_KEY")
        and any(
            urlsplit(base).hostname not in {"127.0.0.1", "::1", "localhost"}
            for base in bases
        )
    ):
        parser.error("Non-loopback model gateways require CASE_LIFECYCLE_API_KEY")
    args.output.mkdir(parents=True, exist_ok=True)
    jobs = [
        (style, seed, wake)
        for seed in range(args.start_seed, args.start_seed + args.seeds)
        for wake in wakes
        for style in styles
    ]
    records, failures = [], []
    if len(bases) > args.workers:
        parser.error("--workers must cover all replicas")
    with ExitStack() as stack:
        pools = [
            stack.enter_context(
                ThreadPoolExecutor(
                    max_workers=len(range(index, args.workers, len(bases)))
                )
            )
            for index in range(len(bases))
        ]
        futures = {}
        for job in jobs:
            replica = (job[1] - args.start_seed) % len(bases)
            future = pools[replica].submit(
                run_one,
                *job,
                args.model,
                args.user_model,
                args.output,
                api_base=bases[replica],
                enable_thinking=False if args.disable_thinking else None,
            )
            futures[future] = job
        for future in as_completed(futures):
            job = futures[future]
            try:
                records.append(future.result())
                print(f"done {job[0]}-{job[1]:03d}--{job[2]}", flush=True)
            except Exception as error:  # keep the batch going; rerun resumes
                failures.append(
                    {"job": job, "error": f"{type(error).__name__}: {error}"}
                )
                print(f"FAILED {job}: {error}", flush=True)
    summary = {"summary": summarize(records), "failures": failures}
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
