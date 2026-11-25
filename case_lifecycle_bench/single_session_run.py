"""The single-session comparison for the card templates.

The same Case, the same tools, the same rules, the same world events, but the whole Case is
one continuous conversation. The clock moves on; events arrive as system messages in the
conversation ("[系统] 现在是 D3 14:00，..."); a customer who arrives chats in the same
conversation. The agent is online all the time and keeps its whole context, so it has no
sessions to bridge and nothing to wake. It has no schedule_followup, and it lets time pass
with wait_until. The time rules (intervals, limits) run on the advanced clock as usual and
the three terminal variants stay. Against the ordinary run the only difference is the
broken-up sessions and the agent's own wake-ups.

    python -m case_lifecycle_bench.single_session_run --template field-service-repair \
        --model qwen3.6-35b-a3b --api-bases http://127.0.0.1:8101/v1 --seeds 10 \
        --output runs/single-session --disable-thinking [--llm-customer]
"""

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from heapq import heappop
from pathlib import Path

from . import lifecycle
from .environment import run_case
from .eval_checks import benchmark_metrics
from .evaluation import evaluate_case
from .lifecycle import _PUSH, _VISIT, _WORLD, _iso
from .model_agent import SIGNALS, ChatGateway, _customer_reply, _tool
from .runnable_templates import ALL

_DROPPED_TOOLS = {"schedule_followup", "list_followups", "cancel_followup"}
_WAIT = _tool(
    "wait_until",
    "Let virtual time pass until an ISO time (for example 2026-01-07T10:00:00) while you stay in this "
    "conversation. Time stops early when something happens (an event or a customer arrives).",
    {"until": "string"},
)
_SETTING = (
    "This Case runs as one continuous conversation. You stay online the whole time and keep everything you have "
    "seen. The system tells you, in messages starting with [系统], when time has passed, when something "
    "happens and when a customer arrives. There are no later wakes and you do not schedule follow-ups: when "
    "there is nothing to do now, call wait_until with the time you want to look again, or end_session when "
    "you are done. Every time rule in the Policy runs on the clock the system shows."
)
_OPS = {
    "part_arrived": "配件到货",
    "promise_deadline": "客户承诺的还款时间已到",
    "payment_posted": "有一笔款项入账",
    "payment_initiated": "客户发起了一笔还款",
    "decision": "审批中心有结论",
    "status_ping": "系统有状态更新",
    "review_ping": "系统有状态更新",
}
_MAX_TURNS = 600


def describe(event: dict) -> str:
    return _OPS.get(event.get("op"), f"系统有新的状态更新（{event.get('op')}）")


def _label(t: datetime) -> str:
    return f"D{(t - lifecycle._EPOCH).days + 1} {t:%H:%M}"


def single_session_tools(tools):
    return [tool for tool in tools if tool["function"]["name"] not in _DROPPED_TOOLS] + [_WAIT]


def single_session_agent(env, gateway, model, user_gateway, user_model, policy, tools):
    """An agent callable that runs the whole timeline inside the one session it is given."""

    def advance(limit=None):
        """Move the clock to the next thing that happens, up to `limit`; return what to tell the agent."""
        while env.queue and env.queue[0][0] <= env.horizon and (limit is None or env.queue[0][0] <= limit):
            t, priority, _, payload = heappop(env.queue)
            env.t = t
            if priority == _WORLD:
                env._world_event(payload)
                row = env.trace[-1]
                if row["kind"] == "world_event" and row.get("notify_agent") and row.get("changes"):
                    return f"[系统] 现在是 {_label(t)}，{describe(payload)}。"
                continue
            if priority == _PUSH or priority != _VISIT:
                continue
            session, changes = env._trigger(priority, payload)
            if session is None:
                continue
            env._apply(changes)
            return ("visit", payload, session)
        if limit is not None and env.t < limit <= env.horizon:
            env.t = limit
            return f"[系统] 现在是 {_label(env.t)}。"
        return None

    def agent(session, call):
        context = {key: value for key, value in session.items() if key != "message"}
        messages = [
            {"role": "system", "content": f"{policy}\n{_SETTING}"},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            {"role": "user", "content": f"[系统] 现在是 {_label(env.t)}。{session.get('note', '')}"},
        ]
        visit, dialogue = None, []
        for _ in range(_MAX_TURNS):
            response = gateway.reply(model, messages, tools)
            text = response.get("content") or ""
            pending = response.get("tool_calls") or []
            assistant = {"role": "assistant", "content": text or None}
            if pending:
                assistant["tool_calls"] = pending
            messages.append(assistant)
            if text.strip():
                call("say", text=text)
                if visit is not None:
                    dialogue.append({"agent": text})
            idle, wait_for = not pending, None
            for tool_call in pending:
                name = tool_call["function"]["name"]
                try:
                    args = json.loads(tool_call["function"]["arguments"])
                    if name == "end_session":
                        result, idle = {"ok": True}, True
                    elif name == "wait_until":
                        wait_for = datetime.fromisoformat(args["until"].replace(" ", "T"))
                        result, idle = {"waiting_until": _iso(wait_for)}, True
                    else:
                        result = call(name, **args)
                except (KeyError, TypeError, ValueError) as error:
                    agent.invalid_arguments += 1
                    agent.invalid_log.append(
                        {"tool": name, "arguments": tool_call["function"].get("arguments"), "error": repr(error)[:200]}
                    )
                    result = {"error": "invalid_arguments"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if not idle:
                continue
            if visit is not None:
                reply = _customer_reply(user_gateway, user_model, visit, dialogue) if text.strip() else ""
                if reply and reply.upper() not in SIGNALS:
                    env._log("message", "S1", role="user", text=reply, step=0)
                    dialogue.append({"customer": reply})
                    messages.append({"role": "user", "content": reply})
                    continue
                visit = None
            moved = advance(wait_for)
            if moved is None:
                return None
            if isinstance(moved, tuple):
                _, payload, arrival = moved
                visit, dialogue = payload, [{"customer": arrival["message"]}]
                env._log("message", "S1", role="user", text=arrival["message"], step=0)
                moved = (
                    f"[系统] 现在是 {_label(env.t)}，客户在{payload['channel']}来访，客户说：{arrival['message']}"
                )
            messages.append({"role": "user", "content": moved})
        return None

    agent.invalid_arguments = 0
    agent.invalid_log = []
    return agent


def run_single_session(case, agent_factory, customer=None):
    """Run a case as one conversation and return a run the ordinary evaluators accept."""
    from .industries.service_cases import BY_TEMPLATE

    lifecycle._STEP_LIMIT = 800  # one conversation holds what many sessions did
    workflow = BY_TEMPLATE[case["template"]]
    env = workflow.environment(case, "poll", customer=customer)
    agent = agent_factory(env)
    run = env.run(agent)
    run["single_session"] = True
    return run, agent


def run_one(name, seed, model, user_model, base, output, *, enable_thinking, llm_customer=False):
    workflow = ALL[name]
    case = workflow.generate(seed)
    if llm_customer:
        case["customer_mode"] = "llm"
    path = output / f"{name}--{case['terminal_variant']}--{seed}--single.json"
    meta = {
        "template": name,
        "seed": seed,
        "wake": "single_session",
        "memory": "single_session",
        "agent_model": model,
        "user_model": user_model,
        "temperature": 0,
        "enable_thinking": enable_thinking,
        "customer_mode": "llm" if llm_customer else "script",
        "tools_sha256": hashlib.sha256(json.dumps(workflow.tools, sort_keys=True).encode()).hexdigest(),
        "policy_sha256": hashlib.sha256(workflow.policy.encode()).hexdigest(),
        "case_sha256": hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest(),
        "commit": os.environ.get("CLB_COMMIT"),
    }
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    gateway = ChatGateway(base, os.environ.get("CASE_LIFECYCLE_API_KEY"), enable_thinking=enable_thinking)
    tools = single_session_tools(workflow.tools)
    customer = None
    if llm_customer:

        def customer(visit, dialogue):
            return _customer_reply(gateway, user_model, visit, dialogue)

    run, agent = run_single_session(
        case,
        lambda env: single_session_agent(env, gateway, model, gateway, user_model, workflow.policy, tools),
        customer=customer,
    )
    evaluation = evaluate_case(case, run)
    meta.update(
        model_calls=gateway.calls,
        model_retries=gateway.retries,
        api_base=base,
        invalid_arguments=agent.invalid_arguments,
        invalid_calls=agent.invalid_log,
    )
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--template", choices=ALL, required=True)
    parser.add_argument("--start-seed", type=int, default=300)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--model", required=True)
    parser.add_argument("--user-model")
    parser.add_argument("--api-bases", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--workers-per-replica", type=int, default=1)
    parser.add_argument("--llm-customer", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    thinking = False if args.disable_thinking else None
    user_model = args.user_model or args.model
    pools = [ThreadPoolExecutor(max_workers=args.workers_per_replica) for _ in args.api_bases]
    futures, records, failures = {}, [], []
    try:
        for index, seed in enumerate(range(args.start_seed, args.start_seed + args.seeds)):
            replica = index % len(args.api_bases)
            future = pools[replica].submit(
                run_one,
                args.template,
                seed,
                args.model,
                user_model,
                args.api_bases[replica],
                args.output,
                enable_thinking=thinking,
                llm_customer=args.llm_customer,
            )
            futures[future] = seed
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as error:  # a failed run must not stop the batch
                failures.append({"seed": futures[future], "error": f"{type(error).__name__}: {error}"})
    finally:
        for pool in pools:
            pool.shutdown()
    print(json.dumps({"template": args.template, "runs": len(records), "failures": failures}, ensure_ascii=False))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
