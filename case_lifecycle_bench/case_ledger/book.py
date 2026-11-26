"""PromiseBook: one ledger per case, wrapped around an agent.

The model judges; the book only keeps score. It never reads a duty label, the Oracle, the scoring code
or anything else the model cannot see, and it has no template-specific branch.
"""

from copy import deepcopy

from . import checks, scorekeeper
from .config import LedgerConfig
from .statement import estimate_tokens, render
from .tools import subject_of, tool_kind, touches

MAX_END_REJECTIONS = 2
_WAKE_BY = {"followup", "customer", "event"}
_HOW = {"done", "handed_over", "released"}


class PromiseBook:
    def __init__(self, config: LedgerConfig | None = None, *, policy: str = "", ask=None, push: bool = False):
        self.config = config or LedgerConfig.full()
        self.policy = policy
        self.ask = ask  # ask(prompt) -> text; None switches the scorekeeper off
        self.push = push
        self.promises: dict = {}
        self.journal: list = []
        self.refs: dict = {}
        self.pending: list = []
        self.followups: dict = {}
        self.customer_texts: list = []
        self.rejections: list = []
        self.would_reject: list = []
        self.added_by_scorekeeper: list = []
        self.statement_tokens: list = []
        self.stats = {
            "scorekeeper_calls": 0, "scorekeeper_tokens": 0, "scorekeeper_error": 0, "scorekeeper_bad_ref": 0,
            "unbalanced_at_end": 0, "implicit_ends": 0, "ledger_calls": 0, "ledger_arg_errors": 0,
        }
        self.wake = 0
        self.wake_label = "W0"
        self.now = None
        self._seq = 0
        self._next_promise = 0
        self._end_rejects: dict = {}

    # -- rejections ---------------------------------------------------------------------

    def _reject(self, check, ref, payload, call=None):
        row = {"check": check, "ref": ref, "wake": self.wake_label, "response": payload}
        if call is not None:
            row["call"] = call  # the tool and arguments that were refused, for the analysis
        if self.config.record_only:
            self.would_reject.append(row)
            return None
        self.rejections.append(row)
        return payload

    # -- the wrapper ----------------------------------------------------------------------

    def wrap(self, agent):
        def wrapped(session, call):
            self.wake += 1
            self.wake_label = f"W{self.wake}"
            if session.get("trigger_type") == "followup_due" and session.get("trigger_id") in self.followups:
                self.followups[session["trigger_id"]]["status"] = "fired"
            if session.get("message"):
                self.customer_texts.append(str(session["message"]))
            self.now = checks.parse_time(session.get("t"))
            for promise in self.promises.values():
                due = checks.parse_time(promise.get("due_by"))
                if promise["status"] == "open" and due is not None and self.now is not None and self.now >= due:
                    promise["overdue_wakes"] = promise.get("overdue_wakes", 0) + 1
            wake_calls: list = []
            state = {"n": 0, "scored": False, "ended": False}
            if self.config.record_only:
                shown = session
            else:
                bill = render(self, session)
                self.statement_tokens.append(estimate_tokens(bill))
                shown = {**session, "case_ledger": bill}

            def ledger_call(tool, **args):
                state["n"] += 1
                ref = f"{self.wake_label}.{state['n']}"
                ledger_call.last_ref = ref
                return self._handle(tool, args, ref, state, wake_calls, call, session)

            ledger_call.numbered = True
            ledger_call.last_ref = None
            try:
                outcome = agent(shown, ledger_call)
            finally:
                if not state["ended"]:
                    self._implicit_end(state, wake_calls, session)
            return outcome

        return wrapped

    # -- one call -------------------------------------------------------------------------

    def _entry(self, ref, tool, kind, args, result, state):
        self._seq += 1
        error = result.get("error") if isinstance(result, dict) else None
        entry = {
            "ref": ref, "wake": self.wake, "seq": self._seq, "n": state["n"], "tool": tool, "kind": kind,
            "args": deepcopy(args), "subject": subject_of(args), "touches": touches(args),
            "result": deepcopy(result), "error": error,
            # Spoke with the customer: a message tool, or any call that carries a contact id.
            "direct": kind == "message" or (kind != "blocked" and "contact_id" in args),
        }
        self.refs[ref] = entry
        if kind in {"read", "write", "handoff", "message"}:
            self.journal.append(entry)
        return entry

    def _handle(self, tool, args, ref, state, wake_calls, call, session):
        kind = tool_kind(tool)
        if kind == "ledger":
            result = self._ledger_tool(tool, args, ref)
            self._entry(ref, tool, kind, args, result, state)
            return result
        if kind == "control":
            result = self._end_session(state, wake_calls, session)
            self._entry(ref, tool, kind, args, result, state)
            return result
        if tool == "close_ticket" and self.config.close_gate:
            open_ = checks.open_promises(self.promises)
            if open_:
                payload = {"error": "ledger_open_promises", "open": open_}
                blocked = self._reject("close_gate", ref, payload, {"tool": tool, "args": deepcopy(args)})
                if blocked is not None:
                    self._entry(ref, tool, "blocked", args, blocked, state)
                    wake_calls.append({"n": state["n"], "tool": tool, "kind": kind, "args": args, "result": blocked})
                    return blocked
        if kind in {"write", "handoff"} and self.config.check_a:
            refusal = checks.check_a(self.pending, self.journal, self.wake, touches(args))
            if refusal is not None:
                blocked = self._reject("A", ref, refusal, {"tool": tool, "args": deepcopy(args)})
                if blocked is not None:
                    # A refused call never reached the environment: it is not a write and not a journal entry.
                    self._entry(ref, tool, "blocked", args, blocked, state)
                    return blocked
        result = call(tool, **args)
        entry = self._entry(ref, tool, kind, args, result, state)
        if kind == "customer":
            self.customer_texts.append(str(args.get("text", "")))
        if kind in {"write", "handoff"} and entry["error"] == "timeout":
            self.pending.append({**entry, "reconciled": False})
        self._track_followup(tool, args, result)
        wake_calls.append({"n": state["n"], "tool": tool, "kind": kind, "args": args, "result": result})
        return result

    def _track_followup(self, tool, args, result):
        if tool == "schedule_followup":
            task_id = result if isinstance(result, str) else (result or {}).get("task_id") if isinstance(result, dict) else None
            if task_id and not (isinstance(result, dict) and result.get("error")):
                self.followups[task_id] = {"task_id": task_id, "due_at": args.get("due_at"), "status": "pending"}
        elif tool == "cancel_followup" and args.get("task_id") in self.followups:
            if not (isinstance(result, dict) and result.get("error")):
                self.followups[args["task_id"]]["status"] = "cancelled"

    # -- the two bookkeeping tools ------------------------------------------------------------

    def _ledger_tool(self, tool, args, ref):
        self.stats["ledger_calls"] += 1
        result = self._add(args, ref) if tool == "add_promise" else self._settle(args, ref)
        if isinstance(result, dict) and result.get("error") in {"invalid_arguments", "unknown_promise"}:
            self.stats["ledger_arg_errors"] += 1
        return result

    def _add(self, args, ref):
        for field in ("to", "what", "done_when"):
            if not isinstance(args.get(field), str) or not args[field].strip():
                return {"error": "invalid_arguments", "why": f"{field} is required."}
        wake_by = args.get("wake_by") or "followup"
        if wake_by not in _WAKE_BY:
            return {"error": "invalid_arguments", "why": "wake_by must be followup, customer or event."}
        return {"promise_id": self._new_promise(args, wake_by, "agent", ref)}

    def _new_promise(self, args, wake_by, source, ref):
        self._next_promise += 1
        pid = f"P{self._next_promise}"
        self.promises[pid] = {
            "id": pid, "to": args["to"], "what": args["what"], "done_when": args["done_when"],
            "due_by": args.get("due_by"), "condition": args.get("condition"), "subject": args.get("subject"),
            "wake_by": wake_by, "told_ref": args.get("told_ref"), "status": "open", "source": source,
            "created_ref": ref, "created_wake": self.wake,
        }
        return pid

    def _settle(self, args, ref):
        promise = self.promises.get(args.get("promise_id"))
        how = args.get("how")
        if promise is None:
            return {"error": "unknown_promise", "why": "No promise with that id."}
        if how not in _HOW:
            return {"error": "invalid_arguments", "why": "how must be done, handed_over or released."}
        if promise["status"] != "open":
            return {"error": "already_settled", "why": f"{promise['id']} is already {promise['status']}."}
        evidence = args.get("evidence")
        if self.config.check_b:
            failure = checks.check_b(how, evidence, promise, self.refs, self.wake, self.customer_texts, self.policy)
            if failure is not None:
                cited = self.refs.get(checks.normalize_ref(evidence))
                blocked = self._reject("B", ref, failure, {
                    "tool": "settle_promise", "args": deepcopy(args), "promise": dict(promise),
                    "cited": None if cited is None else {"tool": cited["tool"], "kind": cited["kind"], "wake": cited["wake"]},
                })
                if blocked is not None:
                    return blocked
        promise["status"] = how
        promise["evidence"] = evidence
        return {"ok": True}

    # -- ending a session ---------------------------------------------------------------------

    def _run_scorekeeper(self, state, wake_calls, session):
        if state["scored"] or not (self.config.scorekeeper and self.ask):
            return
        state["scored"] = True
        steps = {row["n"] for row in wake_calls}
        accepted, stats = scorekeeper.run(self.ask, self.policy, render(self, session), wake_calls, steps)
        self.stats["scorekeeper_calls"] += stats["calls"]
        self.stats["scorekeeper_tokens"] += stats["tokens"]
        self.stats["scorekeeper_error"] += stats["scorekeeper_error"]
        self.stats["scorekeeper_bad_ref"] += stats["scorekeeper_bad_ref"]
        for item in accepted:
            pid = self._new_promise(item, "followup", "scorekeeper", f"{self.wake_label}.step{item['source_step']}")
            self.added_by_scorekeeper.append({**self.promises[pid], "source_step": item["source_step"]})

    def _end_session(self, state, wake_calls, session):
        self._run_scorekeeper(state, wake_calls, session)
        problems = checks.unbalanced(self.promises, self.followups, self.refs, self.push, self.now) if self.config.check_c else []
        if problems:
            used = self._end_rejects.get(self.wake, 0)
            if self.config.record_only:
                self._reject("C", f"{self.wake_label}.end", {"error": "ledger_unbalanced", "open": problems})
            elif used < MAX_END_REJECTIONS:
                self._end_rejects[self.wake] = used + 1
                return self._reject("C", f"{self.wake_label}.end", {"error": "ledger_unbalanced", "open": problems})
            else:
                self.stats["unbalanced_at_end"] += 1
        state["ended"] = True
        return {"ok": True}

    def _implicit_end(self, state, wake_calls, session):
        """The agent stopped without calling end_session: score and count, but nothing can be sent back."""
        self.stats["implicit_ends"] += 1
        self._run_scorekeeper(state, wake_calls, session)
        if self.config.check_c and checks.unbalanced(self.promises, self.followups, self.refs, self.push, self.now):
            self.stats["unbalanced_at_end"] += 1

    # -- the log ------------------------------------------------------------------------------------

    def log(self) -> dict:
        return {
            "config": self.config.name,
            "promises": list(self.promises.values()),
            "rejections": self.rejections,
            "would_reject": self.would_reject,
            "scorekeeper_added": self.added_by_scorekeeper,
            "uncertain_left": [e["ref"] for e in self.pending if not e["reconciled"]],
            "overdue_wakes": {p["id"]: p.get("overdue_wakes", 0) for p in self.promises.values() if p.get("overdue_wakes")},
            "statement_tokens": self.statement_tokens,
            "statement_tokens_mean": (sum(self.statement_tokens) / len(self.statement_tokens)) if self.statement_tokens else 0,
            **self.stats,
        }
