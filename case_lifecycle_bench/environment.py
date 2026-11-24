"""Retail lifecycle tools and the public case runner."""

import re
from collections.abc import Callable

from .industries.retail import initial_state as retail_initial_state
from .lifecycle import LifecycleEnvironment, _VISIT, _iso


def run_case(
    case: dict, agent: Callable, *, wake: str = "poll", single_session: bool = False
) -> dict:
    """Run an isolated case. The callback receives only its current trigger and tools."""
    if wake not in {"poll", "push"}:
        raise ValueError(f"Unknown wake mode: {wake!r}")
    if single_session and case["template"] != "cancel_partial_success":
        raise ValueError("Single-session comparison requires a T3 case")
    industry = case.get("industry") or "retail"
    if industry == "insurance":
        from .industries.insurance.environment import InsuranceEnvironment

        return InsuranceEnvironment(case, wake).run(agent)
    if industry in {"credit", "field_service"}:
        from .industries.service import ServiceEnvironment
        from .industries.service_cases import BY_TEMPLATE

        return ServiceEnvironment(case, wake, BY_TEMPLATE[case["template"]]).run(agent)
    if industry != "retail":
        raise ValueError(f"Unsupported industry: {industry}")
    return RetailEnvironment(case, wake, single_session).run(agent)


class RetailEnvironment(LifecycleEnvironment):
    def _initial_state(self, init: dict) -> tuple[str, str, dict]:
        return init["user"], init["order"], retail_initial_state(init, _iso(self.t))

    def _trigger(self, priority, payload):
        if priority != _VISIT:
            return super()._trigger(priority, payload)
        if not self._visit_condition(payload.get("condition")):
            return None, {}
        trigger = {
            "trigger_type": "user_visit",
            "trigger_id": payload["id"],
            "channel": payload["channel"],
            "user_id": self.user_id,
            "message": payload.get("utterance", payload["goal"]),
        }
        knows = payload.get("knows", [])
        if "order_id" in knows or self.subject_id in knows:
            trigger["order_id"] = self.subject_id
        return self._session_context(trigger), {}

    def _event_subject(self):
        return {"order_id": self.subject_id}

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

    def _execute(self, tool: str, args: dict, session_id: str) -> tuple[object, dict]:
        reads = {
            "get_order_details": ("orders", "order_id"),
            "get_fulfillment_status": ("fulfillment", "order_id"),
            "get_refund": ("refunds", "refund_id"),
        }
        if tool in reads:
            table, argument = reads[tool]
            return self.state[table].get(args[argument], {"error": "not_found"}), {}
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
        if tool == "search_tickets":
            user_id, order_id = args.get("user_id"), args.get("order_id")
            return [
                row
                for row in self.state["tickets"].values()
                if (user_id is None or row["user_id"] == user_id)
                and (order_id is None or order_id in row["order_ids"])
            ], {}
        writes = {
            "create_ticket": self._create_ticket,
            "cancel_pending_order": self._cancel_pending_order,
            "cancel_fulfillment": self._cancel_fulfillment,
            "issue_refund": self._issue_refund,
            "retry_refund": self._retry_refund,
            "send_message_to_user": self._send_message,
        }
        if tool not in writes:
            return super()._execute(tool, args, session_id)
        return writes[tool](**args)

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
        compressed = (
            self.single_session
            and tool == "cancel_fulfillment"
            and "shipments" in changes
        )
        self._schedule_reactive_events(
            tool,
            target,
            context=context,
            immediate=lambda event: compressed and event["op"].startswith("shipment."),
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

    def _world_event(self, event: dict, *, in_session: bool = False) -> None:
        if event["op"].startswith("shipment."):
            changes = self._shipment_event(event)
        else:
            changes = self._refund_event(event)
        self._record_world_event(event, changes, in_session=in_session)
