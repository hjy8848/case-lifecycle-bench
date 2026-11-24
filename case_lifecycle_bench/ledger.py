"""Tool-backed task memory and the legacy Retail guard."""

from copy import deepcopy
from datetime import datetime, timedelta


class TaskLedger:
    """Project observed CRM records without reading or writing on the agent's behalf."""

    def __init__(self):
        self._tickets = {}
        self._followups = {}

    def wrap(self, agent):
        def wrapped(session, call):
            if session["trigger_type"] == "followup_due":
                task_id = session.get("task_id", session["trigger_id"])
                task = self._followups.setdefault(
                    task_id,
                    {
                        "task_id": task_id,
                        "ticket_id": session["ticket_id"],
                        "due_at": session["t"],
                        "note": session.get("note", ""),
                    },
                )
                task["status"] = "fired"

            def observe(tool, **args):
                result = call(tool, **args)
                self._record(tool, args, result, session["t"])
                return result

            projection = self._projection()
            return agent({**session, "case_ledger": projection}, observe)

        return wrapped

    def _projection(self):
        tickets = list(self._tickets.values())
        return deepcopy(
            {
                "tickets": tickets,
                "followups": list(self._followups.values()),
                "commitments": [
                    {"ticket_id": ticket["ticket_id"], "t": note["t"], "text": fragment}
                    for ticket in tickets
                    for note in ticket.get("notes", [])
                    for fragment in _explicit_commitments(note["text"])
                ],
            }
        )

    def _record(self, tool, args, result, now):
        ok = isinstance(result, dict) and result.get("ok") is True
        if tool == "create_ticket" and isinstance(result, str):
            self._tickets[result] = {
                "ticket_id": result,
                "status": "open",
                "notes": [],
                **deepcopy(args),
            }
        elif (
            tool == "get_ticket" and isinstance(result, dict) and "ticket_id" in result
        ):
            self._remember_ticket(result)
        elif tool == "search_tickets" and isinstance(result, list):
            for ticket in result:
                self._remember_ticket(ticket)
        elif tool == "add_ticket_note" and ok:
            ticket = self._tickets.setdefault(
                args["ticket_id"], {"ticket_id": args["ticket_id"]}
            )
            ticket.setdefault("notes", []).append({"t": now, "text": args["text"]})
        elif tool == "close_ticket" and ok:
            ticket = self._tickets.setdefault(
                args["ticket_id"], {"ticket_id": args["ticket_id"]}
            )
            ticket.update(
                status="closed", resolution_code=args["resolution_code"], closed_at=now
            )
        elif tool == "schedule_followup" and isinstance(result, str):
            self._followups[result] = {
                "task_id": result,
                "status": "pending",
                **deepcopy(args),
            }
        elif tool == "list_followups" and isinstance(result, list):
            self._followups.update((row["task_id"], deepcopy(row)) for row in result)
        elif tool == "cancel_followup" and ok:
            task = self._followups.setdefault(
                args["task_id"], {"task_id": args["task_id"]}
            )
            task["status"] = "cancelled"

    def _remember_ticket(self, row):
        self._tickets[row["ticket_id"]] = {
            **self._tickets.get(row["ticket_id"], {}),
            **deepcopy(row),
        }


def _explicit_commitments(text):
    """Recognize only author-labeled promises and retain their original wording."""
    prefixes = ("承诺:", "承诺：", "commitment:", "promise:")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.casefold().startswith(prefixes):
            yield stripped


class LifecycleLedger:
    """Apply an industry's ledger policy around the shared agent/tool callback."""

    def __init__(self):
        self.guard_events = []

    def wrap(self, agent):
        def wrapped(session, call):
            now = datetime.fromisoformat(session["t"])

            def observe(tool, **args):
                result = call(tool, **args)
                self._record(tool, args, result, now)
                return result

            def guarded_call(tool, **args):
                reason = self._guard(tool, args, observe)
                if reason is None:
                    return observe(tool, **args)
                identifiers = {
                    key: args[key]
                    for key in (
                        "ticket_id",
                        "order_id",
                        "refund_id",
                        "policy_id",
                        "subject_id",
                    )
                    if isinstance(args.get(key), str)
                }
                self.guard_events.append(
                    {
                        "t": session["t"],
                        "trigger_id": session["trigger_id"],
                        "tool": tool,
                        "reason": reason,
                        **identifiers,
                    }
                )
                return {"error": "case_ledger_blocked", "reason": reason}

            self._reconcile(observe)
            return agent({**session, "case_ledger": self._projection()}, guarded_call)

        return wrapped


class CaseLedger(LifecycleLedger):
    """Carry one case's obligations and enforce checks through its public tools."""

    def __init__(self):
        super().__init__()
        self._tickets = {}
        self._refunds = {}
        self._obligations = {}
        self._failure_notices = {}
        self._followups = {}

    def _reconcile(self, observe):
        open_tickets = [
            row for row in self._tickets.values() if row["status"] == "open"
        ]
        order_ids = {order_id for row in open_tickets for order_id in row["order_ids"]}
        order_ids.update(
            row["order_id"]
            for row in self._refunds.values()
            if row["status"] != "succeeded"
        )
        for order_id in sorted(order_ids):
            observe("list_refunds", order_id=order_id)
        for ticket in open_tickets:
            observe("list_followups", ticket_id=ticket["ticket_id"])

    def _projection(self):
        return deepcopy(
            {
                "tickets": list(self._tickets.values()),
                "refunds": list(self._refunds.values()),
                "monitoring_obligations": list(self._obligations.values()),
                "failure_notification_obligations": list(
                    self._failure_notices.values()
                ),
                "followups": list(self._followups.values()),
            }
        )

    def _guard(self, tool, args, observe):
        if tool == "create_ticket":
            tickets = observe("search_tickets", user_id=args["user_id"])
            if not isinstance(tickets, list):
                return "Search CRM before opening a ticket."
            order_ids = set(args["order_ids"])
            if any(order_ids.intersection(ticket["order_ids"]) for ticket in tickets):
                return "Reuse the existing ticket for this order."
        elif tool == "issue_refund":
            refunds = observe("list_refunds", order_id=args["order_id"])
            if not isinstance(refunds, list):
                return "Verify gateway state before issuing a refund."
            if refunds:
                return "A refund already exists. Retry its original ID only if failed."
        elif tool == "retry_refund":
            refund = observe("get_refund", refund_id=args["refund_id"])
            if refund.get("status") != "failed":
                return "The gateway must report the original refund as failed before retry."
        elif tool == "close_ticket":
            return self._check_close(args["ticket_id"], observe)
        return None

    def _check_close(self, ticket_id, observe):
        ticket = observe("get_ticket", ticket_id=ticket_id)
        if ticket.get("status") != "open":
            return "Verify an open CRM ticket before closing it."
        refunds = []
        for order_id in ticket["order_ids"]:
            rows = observe("list_refunds", order_id=order_id)
            if not isinstance(rows, list):
                return "Verify the refund in the payment gateway before closure."
            refunds.extend(rows)
        if len(refunds) != 1 or refunds[0]["status"] != "succeeded":
            return "Close only after the payment gateway confirms a single successful refund."
        refund_id = refunds[0]["refund_id"]
        if self._refunds[refund_id]["final_notified_at"] is None:
            return "Send the customer the verified refund result before closure."
        tasks = observe("list_followups", ticket_id=ticket_id)
        if not isinstance(tasks, list) or any(
            task["status"] == "pending" for task in tasks
        ):
            return "Clear pending follow-ups before closure."
        if any(
            duty["refund_id"] == refund_id and duty["verified_at"] is None
            for duty in self._obligations.values()
        ):
            return "Complete the refund verification obligation before closure."
        return None

    def _record(self, tool, args, result, now):
        ok = isinstance(result, dict) and result.get("ok") is True
        if tool == "create_ticket" and isinstance(result, str):
            self._remember_ticket({"ticket_id": result, "status": "open", **args})
        elif tool == "search_tickets" and isinstance(result, list):
            for row in result:
                self._remember_ticket(row)
        elif tool == "get_ticket" and "ticket_id" in result:
            self._remember_ticket(result)
        elif tool == "close_ticket" and ok:
            self._tickets[args["ticket_id"]]["status"] = "closed"
        elif tool == "issue_refund" and isinstance(result, str):
            self._record_processing(result, args["order_id"], now)
        elif tool == "retry_refund" and ok:
            refund_id = args["refund_id"]
            self._record_processing(
                refund_id, self._refunds[refund_id]["order_id"], now
            )
        elif tool == "list_refunds" and isinstance(result, list):
            for row in result:
                self._verify_refund(row, now)
        elif tool == "get_refund" and "refund_id" in result:
            self._verify_refund(result, now)
        elif tool == "schedule_followup" and isinstance(result, str):
            self._followups[result] = {
                "task_id": result,
                "ticket_id": args["ticket_id"],
                "due_at": args["due_at"],
                "status": "pending",
            }
        elif tool == "list_followups" and isinstance(result, list):
            for row in result:
                self._followups[row["task_id"]] = {
                    key: row[key]
                    for key in ("task_id", "ticket_id", "due_at", "status")
                }
        elif tool == "cancel_followup" and ok and args["task_id"] in self._followups:
            self._followups[args["task_id"]]["status"] = "cancelled"
        elif tool == "send_message_to_user" and ok and args["text"].strip():
            self._record_notification(args["user_id"], args["channel"], now)

    def _remember_ticket(self, row):
        self._tickets[row["ticket_id"]] = {
            "ticket_id": row["ticket_id"],
            "user_id": row["user_id"],
            "order_ids": list(row["order_ids"]),
            "status": row["status"],
        }

    def _record_processing(self, refund_id, order_id, now):
        self._refunds[refund_id] = {
            "refund_id": refund_id,
            "order_id": order_id,
            "status": "processing",
            "checked_at": None,
            "final_notified_at": None,
        }
        self._open_obligation(refund_id, now)

    def _open_obligation(self, refund_id, started_at):
        self._obligations.setdefault(
            (refund_id, started_at),
            {
                "refund_id": refund_id,
                "started_at": started_at.isoformat(),
                "due_at": (started_at + timedelta(hours=48)).isoformat(),
                "verified_at": None,
            },
        )

    def _verify_refund(self, row, now):
        refund_id = row["refund_id"]
        previous = self._refunds.get(refund_id, {})
        self._refunds[refund_id] = {
            "refund_id": refund_id,
            "order_id": row["order_id"],
            "status": row["status"],
            "checked_at": now.isoformat(),
            "final_notified_at": previous.get("final_notified_at"),
        }
        # Gateway history recovers a committed write whose response timed out.
        for entry in row["history"]:
            if entry["status"] == "processing":
                self._open_obligation(refund_id, datetime.fromisoformat(entry["t"]))
            elif entry["status"] == "failed":
                self._failure_notices.setdefault(
                    (refund_id, entry["t"]),
                    {
                        "refund_id": refund_id,
                        "order_id": row["order_id"],
                        "failed_at": entry["t"],
                        "observed_at": now.isoformat(),
                        "notified_at": None,
                        "notification_channel": None,
                    },
                )
        for (duty_refund, started_at), duty in self._obligations.items():
            if (
                duty_refund == refund_id
                and started_at < now
                and duty["verified_at"] is None
            ):
                duty["verified_at"] = now.isoformat()

    def _record_notification(self, user_id, channel, now):
        # Message delivery is observable here. Assessing the text belongs to the judge.
        orders = {
            order_id
            for ticket in self._tickets.values()
            if ticket["user_id"] == user_id
            for order_id in ticket["order_ids"]
        }
        for refund in self._refunds.values():
            if refund["order_id"] not in orders:
                continue
            if refund["status"] == "succeeded":
                refund["final_notified_at"] = now.isoformat()
            elif refund["status"] in {"failed", "processing"}:
                failure = next(
                    (
                        notice
                        for notice in reversed(self._failure_notices.values())
                        if notice["refund_id"] == refund["refund_id"]
                    ),
                    None,
                )
                if failure is not None and failure["notified_at"] is None:
                    failure["notified_at"] = now.isoformat()
                    failure["notification_channel"] = channel
