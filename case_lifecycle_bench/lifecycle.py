"""Shared virtual clock, lifecycle sessions, event delivery and CRM tools."""

import re
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
from heapq import heappop, heappush
from itertools import count

_EPOCH = datetime(2026, 1, 5)
_WORLD = 0
_PUSH = 1
_FOLLOWUP = 2
_VISIT = 3
_STEP_LIMIT = 30
_SIGNALS = {"CONFIRMED", "LEFT", "UNRESOLVED"}


class _SessionEnded(Exception):
    pass


def _iso(t: datetime) -> str:
    return t.isoformat()


def _case_time(value: str) -> datetime:
    match = re.fullmatch(r"D([1-9]\d*) (\d\d):(\d\d)", value)
    if not match:
        raise ValueError(f"Invalid case time: {value!r}")
    day, hour, minute = map(int, match.groups())
    return (_EPOCH + timedelta(days=day - 1)).replace(hour=hour, minute=minute)


def _duration(value: str) -> timedelta:
    match = re.fullmatch(r"([1-9]\d*)h", value)
    if not match:
        raise ValueError(f"Invalid case duration: {value!r}")
    return timedelta(hours=int(match[1]))


class LifecycleEnvironment:
    def __init__(self, case: dict, wake: str, single_session: bool):
        self.horizon = _case_time(case["horizon"])
        self.wake = wake
        self.single_session = single_session
        self.t = _EPOCH
        self.trace = []
        self.waiting_events = []
        self.queue = []
        self.queue_order = count()
        self.tool_counts = Counter()
        self.reactive_events = []
        self.faults = {
            (fault["tool"], fault["nth"]): fault["mode"]
            for fault in case.get("faults", [])
        }
        self.user_id, self.subject_id, self.state = self._initial_state(case["init"])
        for visit in case["visits"]:
            self._enqueue(_case_time(visit["t"]), _VISIT, visit)
        for event in case["events"]:
            if "on" in event:
                self.reactive_events.append(event)
            else:
                self._enqueue(_case_time(event["t"]), _WORLD, event)

    def _initial_state(self, init):
        raise NotImplementedError

    def _enqueue(self, t: datetime, priority: int, payload: dict) -> None:
        heappush(self.queue, (t, priority, next(self.queue_order), payload))

    def _log(self, kind: str, session_id: str | None, **fields) -> dict:
        row = {
            "seq": len(self.trace),
            "t": _iso(self.t),
            "kind": kind,
            "session_id": session_id,
            **fields,
        }
        self.trace.append(row)
        return row

    def _apply(self, changes: dict) -> None:
        for table, rows in changes.items():
            self.state[table].update(deepcopy(rows))

    def run(self, agent: Callable) -> dict:
        initial_state = deepcopy(self.state)
        session_index = count(1)
        while self.queue and self.queue[0][0] <= self.horizon:
            self.t, priority, _, payload = heappop(self.queue)
            if priority == _WORLD:
                self._world_event(payload)
                continue
            session, changes = self._trigger(priority, payload)
            if session is not None:
                self._run_session(agent, f"S{next(session_index)}", session, changes)
                if self.single_session:
                    break
        run = {
            "trajectory": self.trace,
            "initial_state": initial_state,
            "final_state": self.state,
        }
        if self.single_session:
            run["single_session"] = True
        return run

    def _trigger(self, priority, payload):
        changes = {}
        if priority == _FOLLOWUP:
            task = self.state["followups"][payload["task_id"]]
            if task["status"] != "pending":
                return None, changes
            changes = {"followups": {task["task_id"]: {**task, "status": "fired"}}}
            trigger = {
                "trigger_type": "followup_due",
                "trigger_id": task["task_id"],
                "task_id": task["task_id"],
                "ticket_id": task["ticket_id"],
                "note": task["note"],
            }
        elif priority == _PUSH:
            trigger = {
                "trigger_type": "system_event",
                "trigger_id": payload["id"],
                **self._event_subject(),
                "event_id": payload["id"],
            }
        else:
            return None, changes
        return self._session_context(trigger), changes

    def _event_subject(self):
        return {}

    def _session_context(self, trigger):
        if self.single_session:
            trigger = {**trigger, "single_session": True}
        return {"t": _iso(self.t), **deepcopy(trigger)}

    def _run_session(
        self, agent: Callable, session_id: str, session: dict, changes: dict
    ) -> None:
        for event in self.waiting_events:
            event["t_observable"] = _iso(self.t)
        self.waiting_events.clear()
        self._apply(changes)
        start = {key: session[key] for key in ("trigger_type", "trigger_id")}
        is_visit = session["trigger_type"] in {"user_visit", "outbound_contact"}
        if is_visit:
            start["channel"] = session["channel"]
            if "contact_id" in session:
                start["contact_id"] = session["contact_id"]
        if changes:
            start["changes"] = changes
        self._log("session_start", session_id, **start)
        if is_visit:
            self._log(
                "message", session_id, role="user", text=session["message"], step=0
            )
        steps = 0
        ended = False

        def call(tool: str, **args):
            nonlocal steps, ended
            if ended or steps == _STEP_LIMIT:
                raise _SessionEnded
            if tool == "user_reply" and is_visit:
                self._log(
                    "message", session_id, role="user", text=args["text"], step=steps
                )
                return None
            steps += 1
            if tool == "say":
                self._log(
                    "message", session_id, role="agent", text=args["text"], step=steps
                )
                return {"ok": True}
            result = self._call_tool(tool, args, session_id, steps)
            ended = tool == "end_session"
            return result

        signal = None
        try:
            signal = agent(session, call)
        except _SessionEnded:
            pass
        if signal is not None and (not is_visit or signal not in _SIGNALS):
            raise ValueError(f"Invalid user signal on {session_id}: {signal!r}")
        if signal is not None:
            reason = "user_signal"
        elif steps == _STEP_LIMIT and not ended:
            reason = "max_steps"
        else:
            reason = "end_session"
        self._log(
            "session_end", session_id, reason=reason, user_signal=signal, steps=steps
        )

    def _call_tool(self, tool: str, args: dict, session_id: str, step: int):
        self.tool_counts[tool] += 1
        fault = self.faults.get((tool, self.tool_counts[tool]))
        changes = {}
        if fault == "timeout_before_commit":
            result = {"error": "timeout"}
        elif fault == "transient_error":
            result = {"error": "service_unavailable"}
        elif fault in {None, "timeout_after_commit"}:
            result, changes = self._execute(tool, args, session_id)
            self._apply(changes)
            if fault:
                result = {"error": "timeout"}
        else:
            raise ValueError(f"Unsupported injected fault: {fault!r}")

        row = self._log(
            "tool_call",
            session_id,
            tool=tool,
            args=deepcopy(args),
            result=deepcopy(result),
            fault=fault,
            committed=bool(changes),
            step=step,
        )
        if changes:
            row["changes"] = changes
            if tool == "schedule_followup":
                task_id, task = next(iter(changes["followups"].items()))
                self._enqueue(
                    datetime.fromisoformat(task["due_at"]),
                    _FOLLOWUP,
                    {"task_id": task_id},
                )
            self._react_to_tool(tool, args, changes)
        return deepcopy(result)

    def _execute(self, tool, args, session_id):
        if tool == "get_ticket":
            return self.state["tickets"].get(
                args["ticket_id"], {"error": "not_found"}
            ), {}
        if tool == "get_current_time":
            return _iso(self.t), {}
        if tool == "end_session":
            return {"ok": True}, {}
        if tool == "list_followups":
            return [
                row
                for row in self.state["followups"].values()
                if row["ticket_id"] == args["ticket_id"]
            ], {}
        if tool == "schedule_followup":
            return self._schedule_followup(**args, session_id=session_id)
        writes = {
            "add_ticket_note": self._add_ticket_note,
            "close_ticket": self._close_ticket,
            "cancel_followup": self._cancel_followup,
        }
        if tool not in writes:
            return {"error": "unknown_tool"}, {}
        return writes[tool](**args)

    def _new_id(self, table: str, prefix: str) -> str:
        return f"{prefix}{len(self.state[table]) + 1}"

    def _add_ticket_note(self, ticket_id: str, text: str) -> tuple:
        row = self.state["tickets"].get(ticket_id)
        if not row:
            return {"error": "not_found"}, {}
        notes = [*row["notes"], {"t": _iso(self.t), "text": text}]
        return {"ok": True}, {"tickets": {ticket_id: {**row, "notes": notes}}}

    def _close_ticket(self, ticket_id: str, resolution_code: str) -> tuple:
        row = self.state["tickets"].get(ticket_id)
        if not row:
            return {"error": "not_found"}, {}
        closed = {
            **row,
            "status": "closed",
            "resolution_code": resolution_code,
            "closed_at": _iso(self.t),
        }
        return {"ok": True}, {"tickets": {ticket_id: closed}}

    def _schedule_followup(
        self, ticket_id: str, due_at: str, note: str, session_id: str
    ) -> tuple:
        if ticket_id not in self.state["tickets"]:
            return {"error": "not_found"}, {}
        try:
            due = datetime.fromisoformat(due_at)
        except (TypeError, ValueError):
            return {"error": "invalid_due_at"}, {}
        if due.tzinfo is not None or due <= self.t or len(note) > 500:
            return {"error": "invalid_followup"}, {}
        task_id = self._new_id("followups", "F")
        row = {
            "task_id": task_id,
            "ticket_id": ticket_id,
            "due_at": _iso(due),
            "note": note,
            "created_in_session": session_id,
            "status": "pending",
        }
        return task_id, {"followups": {task_id: row}}

    def _cancel_followup(self, task_id: str) -> tuple:
        row = self.state["followups"].get(task_id)
        if not row or row["status"] != "pending":
            return {"error": "followup_not_pending"}, {}
        return {"ok": True}, {"followups": {task_id: {**row, "status": "cancelled"}}}

    def _react_to_tool(self, tool, args, changes):
        self._schedule_reactive_events(tool, args.get("subject_id"))

    def _schedule_reactive_events(
        self, tool, target=None, *, context=None, immediate=None
    ):
        triggers = {f"agent.call({tool})", f"agent.call({tool}, {target})"}
        for event in self.reactive_events[:]:
            if event["on"] not in triggers:
                continue
            self.reactive_events.remove(event)
            payload = {**event, **(context or {})}
            if immediate is not None and immediate(event):
                self._world_event(payload, in_session=True)
            else:
                self._enqueue(self.t + _duration(event["after"]), _WORLD, payload)

    def _world_event(self, event, *, in_session=False):
        raise NotImplementedError

    def _record_world_event(
        self, event: dict, changes: dict, *, in_session: bool = False
    ) -> None:
        # An event with no business effect carries no realized detection duty.
        applied = bool(changes)
        notify_agent = event.get("notify_agent", False)
        push = (
            applied and notify_agent and self.wake == "push" and not self.single_session
        )
        observable = applied and (push or in_session)
        self._apply(changes)
        row = self._log(
            "world_event",
            None,
            event_id=event["id"],
            op=event["op"],
            notify_agent=notify_agent,
            duty=event.get("duty") if applied else None,
            t_observable=_iso(self.t) if observable else None,
            changes=changes,
        )
        if push:
            self._enqueue(self.t, _PUSH, event)
        elif applied and not observable:
            self.waiting_events.append(row)
