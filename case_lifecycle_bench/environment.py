"""Deterministic retail lifecycle environment for the smoke-test cases."""

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


def run_case(case: dict, agent: Callable, *, wake: str = "poll") -> dict:
    """Run an isolated case. The callback receives only its current trigger and tools."""
    if wake not in {"poll", "push"}:
        raise ValueError(f"Unknown wake mode: {wake!r}")
    return _Environment(case, wake).run(agent)


class _Environment:
    def __init__(self, case: dict, wake: str):
        self.horizon = _case_time(case["horizon"])
        self.wake = wake
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
        init = case["init"]
        self.user_id = init["user"]
        self.order_id = init["order"]
        self.state = {
            "users": {self.user_id: {"user_id": self.user_id}},
            "orders": {
                self.order_id: {
                    "order_id": self.order_id,
                    "user_id": self.user_id,
                    "amount": init["amount"],
                    "status": init.get("order_status", "delivered"),
                }
            },
            "products": {},
            "tickets": {},
            "refunds": {},
            "shipments": {},
            "fulfillment": {},
            "payment_ledger": {},
            "followups": {},
            "outbox": {},
            "handoffs": {},
        }
        for patch in init.get("db_patch", []):
            self._patch_fulfillment(patch)
        if self.state["fulfillment"] and "order_status" not in init:
            self.state["orders"][self.order_id]["status"] = "pending"
        for visit in case["visits"]:
            self._enqueue(_case_time(visit["t"]), _VISIT, visit)
        for event in case["events"]:
            if "on" in event:
                self.reactive_events.append(event)
            else:
                self._enqueue(_case_time(event["t"]), _WORLD, event)

    def _patch_fulfillment(self, patch: str) -> None:
        match = re.fullmatch(
            r"fulfillment\[([^\[\]]+)\]\.status\s*=\s*(pending|picking|shipped|cancelled)",
            patch,
        )
        if not match or match[1] not in self.state["orders"]:
            raise ValueError(f"Unsupported initial DB patch: {patch!r}")
        order_id, status = match.groups()
        self.state["fulfillment"][order_id] = {
            "order_id": order_id,
            "status": status,
            "cancel_synced": status == "cancelled",
        }
        if status == "shipped":
            shipment_id = self._new_id("shipments", "SH")
            self.state["shipments"][shipment_id] = {
                "shipment_id": shipment_id,
                "order_id": order_id,
                "status": "in_transit",
                "events": [{"t": _iso(self.t), "status": "in_transit"}],
            }

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
        return {
            "trajectory": self.trace,
            "initial_state": initial_state,
            "final_state": self.state,
        }

    def _trigger(self, priority: int, payload: dict) -> tuple[dict | None, dict]:
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
                "order_id": self.order_id,
                "event_id": payload["id"],
            }
        else:
            if not self._visit_condition(payload.get("condition")):
                return None, changes
            trigger = {
                "trigger_type": "user_visit",
                "trigger_id": payload["id"],
                "channel": payload["channel"],
                "user_id": self.user_id,
                "message": payload.get("utterance", payload["goal"]),
            }
            knows = payload.get("knows", [])
            if "order_id" in knows or self.order_id in knows:
                trigger["order_id"] = self.order_id
        return {"t": _iso(self.t), **deepcopy(trigger)}, changes

    def _visit_condition(self, condition: str | None) -> bool:
        if condition is None:
            return True
        match = re.fullmatch(
            r"refund\(order=([^()]+)\)\.status != succeeded", condition
        )
        if not match:
            raise ValueError(f"Unsupported visit condition: {condition!r}")
        return not any(row["status"] == "succeeded" for row in self._refunds(match[1]))

    def _refunds(self, order_id: str) -> list[dict]:
        return [
            row for row in self.state["refunds"].values() if row["order_id"] == order_id
        ]

    def _run_session(
        self, agent: Callable, session_id: str, session: dict, changes: dict
    ) -> None:
        for event in self.waiting_events:
            event["t_observable"] = _iso(self.t)
        self.waiting_events.clear()
        self._apply(changes)
        start = {key: session[key] for key in ("trigger_type", "trigger_id")}
        is_visit = session["trigger_type"] == "user_visit"
        if is_visit:
            start["channel"] = session["channel"]
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

    def _execute(self, tool: str, args: dict, session_id: str) -> tuple[object, dict]:
        reads = {
            "get_order_details": ("orders", "order_id"),
            "get_fulfillment_status": ("fulfillment", "order_id"),
            "get_ticket": ("tickets", "ticket_id"),
            "get_refund": ("refunds", "refund_id"),
        }
        if tool in reads:
            table, argument = reads[tool]
            return self.state[table].get(args[argument], {"error": "not_found"}), {}
        if tool == "get_current_time":
            return _iso(self.t), {}
        if tool == "get_shipment":
            return self._shipment(args["order_id"]) or {"error": "not_found"}, {}
        if tool == "list_refunds":
            return self._refunds(args["order_id"]), {}
        if tool == "get_payment_ledger":
            return [
                row
                for row in self.state["payment_ledger"].values()
                if row["order_id"] == args["order_id"]
            ], {}
        if tool == "list_followups":
            return [
                row
                for row in self.state["followups"].values()
                if row["ticket_id"] == args["ticket_id"]
            ], {}
        if tool == "search_tickets":
            user_id, order_id = args.get("user_id"), args.get("order_id")
            return [
                row
                for row in self.state["tickets"].values()
                if (user_id is None or row["user_id"] == user_id)
                and (order_id is None or order_id in row["order_ids"])
            ], {}
        if tool == "end_session":
            return {"ok": True}, {}
        if tool == "schedule_followup":
            return self._schedule_followup(**args, session_id=session_id)
        writes = {
            "create_ticket": self._create_ticket,
            "add_ticket_note": self._add_ticket_note,
            "close_ticket": self._close_ticket,
            "cancel_pending_order": self._cancel_pending_order,
            "cancel_fulfillment": self._cancel_fulfillment,
            "issue_refund": self._issue_refund,
            "retry_refund": self._retry_refund,
            "cancel_followup": self._cancel_followup,
            "send_message_to_user": self._send_message,
        }
        if tool not in writes:
            return {"error": "unknown_tool"}, {}
        return writes[tool](**args)

    def _new_id(self, table: str, prefix: str) -> str:
        return f"{prefix}{len(self.state[table]) + 1}"

    def _create_ticket(self, user_id: str, order_ids: list[str], summary: str) -> tuple:
        if user_id not in self.state["users"] or any(
            order_id not in self.state["orders"] for order_id in order_ids
        ):
            return {"error": "not_found"}, {}
        ticket_id = self._new_id("tickets", "T")
        row = {
            "ticket_id": ticket_id,
            "user_id": user_id,
            "order_ids": list(order_ids),
            "summary": summary,
            "notes": [],
            "created_at": _iso(self.t),
            "status": "open",
            "resolution_code": None,
            "closed_at": None,
        }
        return ticket_id, {"tickets": {ticket_id: row}}

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

    def _cancel_pending_order(self, order_id: str) -> tuple:
        order = self.state["orders"].get(order_id)
        if order is None or order["status"] != "pending":
            return {"error": "order_not_pending"}, {}
        cancelled = {**order, "status": "cancelled"}
        return cancelled, {"orders": {order_id: cancelled}}

    def _shipment(self, order_id: str) -> dict | None:
        return next(
            (
                row
                for row in self.state["shipments"].values()
                if row["order_id"] == order_id
            ),
            None,
        )

    def _shipment_status(self, row: dict, status: str) -> dict:
        event = {"t": _iso(self.t), "status": status}
        return {**row, "status": status, "events": [*row["events"], event]}

    def _cancel_fulfillment(self, order_id: str) -> tuple:
        fulfillment = self.state["fulfillment"].get(order_id)
        if fulfillment is None:
            return {"error": "not_found"}, {}
        if fulfillment["status"] == "cancelled":
            return {"result": "cancelled"}, {}
        if fulfillment["status"] in {"pending", "picking"}:
            cancelled = {**fulfillment, "status": "cancelled", "cancel_synced": True}
            return {"result": "cancelled"}, {"fulfillment": {order_id: cancelled}}
        shipment = self._shipment(order_id)
        if shipment is None or shipment["status"] not in {
            "in_transit",
            "intercept_requested",
        }:
            return {"error": "shipment_not_interceptable"}, {}
        if shipment["status"] == "intercept_requested":
            return {"result": "intercept_requested"}, {}
        return {"result": "intercept_requested"}, {
            "shipments": {
                shipment["shipment_id"]: self._shipment_status(
                    shipment, "intercept_requested"
                )
            }
        }

    def _issue_refund(self, order_id: str, amount: float) -> tuple:
        if order_id not in self.state["orders"]:
            return {"error": "not_found"}, {}
        if not (0 < amount <= self.state["orders"][order_id]["amount"]):
            return {"error": "invalid_amount"}, {}
        refund_id = self._new_id("refunds", "R")
        row = {
            "refund_id": refund_id,
            "order_id": order_id,
            "amount": amount,
            "status": "requested",
            "history": [{"t": _iso(self.t), "status": "requested"}],
        }
        return refund_id, {
            "refunds": {refund_id: self._refund_status(row, "processing")}
        }

    def _refund_status(self, row: dict, status: str, reason: str | None = None) -> dict:
        entry = {"t": _iso(self.t), "status": status}
        if reason is not None:
            entry["reason"] = reason
        return {**row, "status": status, "history": [*row["history"], entry]}

    def _retry_refund(self, refund_id: str) -> tuple:
        row = self.state["refunds"].get(refund_id)
        if not row or row["status"] != "failed":
            return {"error": "refund_not_failed"}, {}
        return {"ok": True}, {
            "refunds": {refund_id: self._refund_status(row, "processing")}
        }

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

    def _send_message(self, user_id: str, channel: str, text: str) -> tuple:
        if user_id not in self.state["users"]:
            return {"error": "not_found"}, {}
        msg_id = self._new_id("outbox", "M")
        row = {
            "msg_id": msg_id,
            "user_id": user_id,
            "channel": channel,
            "t": _iso(self.t),
            "text": text,
        }
        return {"ok": True}, {"outbox": {msg_id: row}}

    def _react_to_tool(self, tool: str, args: dict, changes: dict) -> None:
        if tool in {"issue_refund", "retry_refund"}:
            context = {"refund_id": next(iter(changes["refunds"]))}
        else:
            context = {}
        target = args.get("order_id", args.get("refund_id"))
        for event in self.reactive_events[:]:
            if event["on"] in {
                f"agent.call({tool})",
                f"agent.call({tool}, {target})",
            }:
                self.reactive_events.remove(event)
                self._enqueue(
                    self.t + _duration(event["after"]),
                    _WORLD,
                    {**event, **context},
                )

    def _refund_event(self, event: dict) -> dict:
        match = re.fullmatch(
            r"refund\.set_status\((?:first_refund\(([^()]+)\)|retried|issued),\s*"
            r"(failed|succeeded)(?:,\s*reason=([^()]+))?\)",
            event["op"],
        )
        if not match:
            raise ValueError(f"Unsupported world event: {event['op']!r}")
        order_id, status, reason = match.groups()
        if order_id is None:
            refund = self.state["refunds"].get(event.get("refund_id"))
        else:
            refund = next(iter(self._refunds(order_id)), None)

        if refund is None or refund["status"] != "processing":
            return {}
        changes = {
            "refunds": {
                refund["refund_id"]: self._refund_status(refund, status, reason)
            }
        }
        if status == "succeeded":
            txn_id = self._new_id("payment_ledger", "P")
            changes["payment_ledger"] = {
                txn_id: {
                    "txn_id": txn_id,
                    "order_id": refund["order_id"],
                    "type": "refund",
                    "amount": refund["amount"],
                    "t": _iso(self.t),
                }
            }
        return changes

    def _shipment_event(self, event: dict) -> dict:
        match = re.fullmatch(
            r"shipment\.set_status\(([^()]+),\s*intercepted\)", event["op"]
        )
        if not match:
            raise ValueError(f"Unsupported world event: {event['op']!r}")
        order_id = match[1]
        shipment = self._shipment(order_id)
        if shipment is None or shipment["status"] != "intercept_requested":
            return {}
        fulfillment = self.state["fulfillment"][order_id]
        return {
            "shipments": {
                shipment["shipment_id"]: self._shipment_status(shipment, "intercepted")
            },
            "fulfillment": {
                order_id: {**fulfillment, "status": "cancelled", "cancel_synced": True}
            },
        }

    def _world_event(self, event: dict) -> None:
        if event["op"].startswith("shipment."):
            changes = self._shipment_event(event)
        else:
            changes = self._refund_event(event)
        # An event with no business effect carries no realized detection duty.
        applied = bool(changes)
        notify_agent = event.get("notify_agent", False)
        push = applied and notify_agent and self.wake == "push"
        self._apply(changes)
        row = self._log(
            "world_event",
            None,
            event_id=event["id"],
            op=event["op"],
            notify_agent=notify_agent,
            duty=event.get("duty") if applied else None,
            t_observable=_iso(self.t) if push else None,
            changes=changes,
        )
        if push:
            self._enqueue(self.t, _PUSH, event)
        elif applied:
            self.waiting_events.append(row)
