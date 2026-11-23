"""OpenAI-compatible chat agent and customer simulator for the lifecycle pilot."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


POLICY = """You are a retail support agent. The context starts fresh with each wake.
Use the CRM to continue existing cases. Look up a case by customer before creating
a ticket, and write notes with the state and pending duties before ending a session.
OMS is authoritative for order contents and amounts; the payment gateway is
authoritative for refunds; 3PL is authoritative for shipping and cancellation
capability; the carrier is authoritative for parcel location and interception.
Only perform customer-requested writes with explicit confirmation. When a write
times out or its result is unknown, query the authoritative state before retrying.
Use issue_refund for a new refund only if none is processing or succeeded. Retry
a failed refund using retry_refund with its original ID. After issuing or retrying
a refund, schedule a follow-up within 48h to verify the payment result.
Cancelling an order in OMS does not cancel shipping. Check 3PL status and use
cancel_fulfillment. If shipped, track the interception within 24h, and recheck
within another 24h if pending. Refund only once the parcel is intercepted or
returned. Do not report full cancellation before it is verified.
Send important changes and final results with send_message_to_user. Close a ticket
only after verifying the result, notifying the customer, and clearing pending
follow-ups. Schedule any promised follow-ups no later than their deadlines.
Avoid promising dates of bank settlement. Use ISO virtual time without a time
zone. During a follow-up or system wake there is no live user. End such sessions
with end_session once the current work is done. For live chat, respond in Chinese.
"""


def _tool(name, description, required=None, optional=None):
    required = required or {}
    properties = {
        key: {"type": kind}
        if kind != "array"
        else {"type": kind, "items": {"type": "string"}}
        for key, kind in (required | (optional or {})).items()
    }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    _tool("get_current_time", "Read the virtual time."),
    _tool(
        "get_order_details",
        "Read order status and amount from OMS.",
        {"order_id": "string"},
    ),
    _tool(
        "search_tickets",
        "Search the CRM by customer or order.",
        optional={"user_id": "string", "order_id": "string"},
    ),
    _tool("get_ticket", "Read a CRM ticket and notes.", {"ticket_id": "string"}),
    _tool(
        "create_ticket",
        "Open a CRM ticket.",
        {"user_id": "string", "order_ids": "array", "summary": "string"},
    ),
    _tool(
        "add_ticket_note",
        "Record case state and pending obligations.",
        {"ticket_id": "string", "text": "string"},
    ),
    _tool(
        "close_ticket",
        "Close a resolved ticket.",
        {"ticket_id": "string", "resolution_code": "string"},
    ),
    _tool("list_refunds", "Look up gateway refunds by order.", {"order_id": "string"}),
    _tool(
        "get_refund", "Read a refund from the payment gateway.", {"refund_id": "string"}
    ),
    _tool(
        "get_payment_ledger", "Read settled payment records.", {"order_id": "string"}
    ),
    _tool(
        "issue_refund",
        "Issue a new refund.",
        {"order_id": "string", "amount": "number"},
    ),
    _tool("retry_refund", "Retry an existing failed refund.", {"refund_id": "string"}),
    _tool(
        "get_fulfillment_status",
        "Read the 3PL shipping status.",
        {"order_id": "string"},
    ),
    _tool(
        "get_shipment",
        "Read the carrier's parcel status and history.",
        {"order_id": "string"},
    ),
    _tool(
        "cancel_pending_order",
        "Cancel a pending order in OMS only.",
        {"order_id": "string"},
    ),
    _tool(
        "cancel_fulfillment",
        "Cancel fulfillment or request carrier interception.",
        {"order_id": "string"},
    ),
    _tool(
        "schedule_followup",
        "Schedule a CRM wake at a later virtual ISO time.",
        {"ticket_id": "string", "due_at": "string", "note": "string"},
    ),
    _tool(
        "list_followups",
        "Read CRM follow-ups and their status.",
        {"ticket_id": "string"},
    ),
    _tool(
        "cancel_followup",
        "Cancel a redundant pending follow-up.",
        {"task_id": "string"},
    ),
    _tool(
        "send_message_to_user",
        "Send a user notification.",
        {"user_id": "string", "channel": "string", "text": "string"},
    ),
    _tool("end_session", "Finish the current session."),
]
TOOL_NAMES = {item["function"]["name"] for item in TOOLS}
SIGNALS = {"CONFIRMED", "LEFT", "UNRESOLVED"}


class ChatGateway:
    def __init__(self, api_base, key):
        self.api_base = api_base.rstrip("/")
        self.key = key
        self.calls = 0

    def reply(self, model, messages, tools=None):
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 768,
        }
        if tools is not None:
            body["tools"] = tools
        request = Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
        )
        self.calls += 1
        try:
            with urlopen(request, timeout=35) as response:
                return json.load(response)["choices"][0]["message"]
        except HTTPError as error:
            raise RuntimeError(
                f"Chat completion failed with HTTP {error.code}"
            ) from None
        except (URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Chat completion failed: {type(error).__name__}"
            ) from None


def _customer_reply(gateway, model, visit, dialogue):
    prompt = (
        "Simulate one customer visit in Chinese. Use only the visit's goal, opening "
        "message and observable facts. Respond to the latest support reply as the "
        "customer. If the conversation is over, instead return exactly one token: "
        "CONFIRMED (satisfied), LEFT (leave with no further request), or "
        "UNRESOLVED (dissatisfied or asking for human help). Visit: "
        + json.dumps(visit, ensure_ascii=False)
    )
    response = gateway.reply(
        model,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(dialogue, ensure_ascii=False)},
        ],
    )
    return (response.get("content") or "").strip().strip("#").strip()


def model_agent(gateway, model, user_model, case):
    visits = {row["id"]: row for row in case["visits"]}

    def agent(session, call):
        visit = (
            visits.get(session["trigger_id"])
            if session["trigger_type"] == "user_visit"
            else None
        )
        context = {key: value for key, value in session.items() if key != "message"}
        messages = [
            {"role": "system", "content": POLICY},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        dialogue = []
        if visit is not None:
            messages.append({"role": "user", "content": session["message"]})
            dialogue.append({"customer": session["message"]})

        for _ in range(30):
            response = gateway.reply(model, messages, TOOLS)
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

            for tool_call in pending:
                function = tool_call["function"]
                name = function["name"]
                try:
                    args = json.loads(function["arguments"])
                    if name not in TOOL_NAMES or not isinstance(args, dict):
                        raise ValueError
                    if name == "end_session":
                        return "LEFT" if visit is not None else None
                    result = call(name, **args)
                except (KeyError, TypeError, ValueError):
                    result = {"error": "invalid_arguments"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if pending:
                continue
            if visit is None or not text.strip():
                return None
            reply = _customer_reply(gateway, user_model, visit, dialogue)
            if reply.upper() in SIGNALS:
                return reply.upper()
            if not reply:
                return None
            call("user_reply", text=reply)
            dialogue.append({"customer": reply})
            messages.append({"role": "user", "content": reply})

    return agent
