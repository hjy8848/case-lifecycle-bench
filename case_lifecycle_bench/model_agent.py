"""OpenAI-compatible chat agent and customer simulator for the lifecycle pilot."""

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Transient gateway failures are retried with exponential backoff. A retry repeats
# only the model request; no tool has run yet, so it cannot duplicate a write.
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}
_RETRY_DELAYS = (2, 4, 8, 16, 32)


_POLICY = """You are a retail support agent. {session_setup}
Use the CRM to continue existing cases. Look up a case by customer before creating
a ticket, and write notes with the state and pending duties before ending a session.
OMS is authoritative for order contents and amounts; the payment gateway is
authoritative for refunds; 3PL is authoritative for shipping and cancellation
capability; the carrier is authoritative for parcel location and interception.
Only perform customer-requested writes with explicit confirmation. When a write
times out or its result is unknown, query the authoritative state before retrying.
Use issue_refund for a new refund only if none is processing or succeeded. Retry
a failed refund using retry_refund with its original ID. {refund_check}
If the same refund fails a second time, or stays processing for more than 5 calendar
days counted from when it was created (its history shows when), do not retry or
issue again: hand the ticket to the payment team with escalate_to_human and tell
the customer.
If a customer comes back about the same unresolved case for the third time, on a third
channel, hand the ticket to a human with escalate_to_human; never open a second ticket
for the same issue.
Order cancellation requires two separate actions. First, check get_order_details.
If OMS reports pending, call cancel_pending_order after the customer's confirmation,
even if 3PL reports shipped. Second, check get_fulfillment_status and call
cancel_fulfillment to cancel unshipped fulfillment or request interception.
Only report full cancellation after verifying both the OMS cancellation and the
fulfillment cancellation or carrier interception. {interception_check} Refund a
shipped order only once the parcel is intercepted or returned. If the carrier
reports the parcel delivered despite the request, do not refund: hand the ticket
to the returns team with escalate_to_human and tell the customer.
Send important changes and final results with send_message_to_user. Close a ticket
only after verifying the result, notifying the customer, and clearing pending
follow-ups. {followup_deadline}
Avoid promising dates of bank settlement. Use ISO virtual time without a time
zone. {session_ending}For live chat, respond in English.
"""

POLICY = _POLICY.format(
    session_setup="The context starts fresh with each wake.",
    refund_check="Verify the payment result of a refund you issue or retry.",
    interception_check="If shipped, track the interception until the carrier reports a result.",
    followup_deadline="While the case is open and you are waiting on anything, leave one pending follow-up\nfor your next check before you end a session.",
    session_ending="During a follow-up or system wake there is no live user. End such sessions\nwith end_session once the current work is done. ",
)

SINGLE_SESSION_POLICY = _POLICY.format(
    session_setup="This Case runs in one conversation, with no later wakes.\n"
    "External results can take effect during this session after their prerequisites are met.",
    refund_check="After issuing or retrying a refund, query the payment gateway again in this conversation to verify its status.",
    interception_check="If shipped, query the carrier result in this conversation after requesting interception.",
    followup_deadline="Complete required verification with the same tools during this conversation.",
    session_ending="",
)


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
        "Cancel the OMS order when get_order_details reports pending, including "
        "when 3PL reports shipped. Fulfillment requires a separate action.",
        {"order_id": "string"},
    ),
    _tool(
        "cancel_fulfillment",
        "Cancel 3PL fulfillment or request carrier interception. This does not "
        "cancel the OMS order.",
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
    _tool(
        "escalate_to_human",
        "Hand a ticket to a human team with a handoff note. The case stays open.",
        {"ticket_id": "string", "note": "string"},
    ),
    _tool("end_session", "Finish the current session."),
]
SIGNALS = {"CONFIRMED", "LEFT", "UNRESOLVED"}
_REFUND_AUTHORIZATION_REQUEST = re.compile(
    r"(?i)(?:^|[.!?\n])\s*(?:please\s+)?(?:(?:do|would|can|could)\s+you\s+)?"
    r"(?:confirm|agree\s+to|approve|authorize)\s+"
    r"(?:(?:the|a|this|that|your|full|an|processing|issuing|submitting|requesting)\s+)*refund"
    r"(?:\s+(?:of\s+)?[$\u00a5]?\s*[\d,.]+)?\s*\??\s*(?=$|[.!?\n])"
)


class ChatGateway:
    def __init__(self, api_base, key, *, enable_thinking=None):
        self.api_base = api_base.rstrip("/")
        self.key = key
        self.enable_thinking = enable_thinking
        self.calls = 0
        self.retries = 0

    def reply(self, model, messages, tools=None):
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 768,
        }
        if tools is not None:
            body["tools"] = tools
        if self.enable_thinking is not None:
            body["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        request = Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
        )
        self.calls += 1
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                with urlopen(request, timeout=120) as response:
                    return json.load(response)["choices"][0]["message"]
            except HTTPError as error:
                if error.code not in _RETRY_STATUS or attempt == len(_RETRY_DELAYS):
                    raise RuntimeError(
                        f"Chat completion failed with HTTP {error.code}"
                    ) from None
                failure = f"HTTP {error.code}"
            except (URLError, TimeoutError, ConnectionError) as error:
                if attempt == len(_RETRY_DELAYS):
                    raise RuntimeError(
                        f"Chat completion failed: {type(error).__name__}"
                    ) from None
                failure = type(error).__name__
            self.retries += 1
            time.sleep(_RETRY_DELAYS[attempt])
        raise RuntimeError(f"Chat completion failed: {failure}")


def _customer_reply(gateway, model, visit, dialogue):
    prompt = (
        "Simulate one customer visit in English. Use only the visit's goal, opening "
        "message, observable facts and authorization reply if provided. Respond to the "
        "latest support reply as the customer. Keep the scenario instructions private. "
        "When you lack a requested identifier, explain that in ordinary customer language. "
        "If support asks for your confirmation "
        "or authorization and you agree, give explicit consent in English so support "
        "can continue. For example, reply 'Confirmed, I agree to process this refund.' to authorize the "
        "requested refund. If the visit defines refund_authorization_reply, use it only "
        "to authorize that refund. A terminal token never grants authorization. Return "
        "exactly one token only when you are ending the conversation: "
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
    reply = (response.get("content") or "").strip().strip("#").strip()
    authorization = visit.get("refund_authorization_reply")
    if (
        reply.upper() == "CONFIRMED"
        and authorization
        and _REFUND_AUTHORIZATION_REQUEST.search(dialogue[-1]["agent"])
    ):
        return authorization
    return reply


def _numbered(call, wake):
    """Label each tool call of one wake W<wake>.<n>, the same way the ledger wrapper does, so every
    group sees one format. The label of the latest call is left on `last_ref`."""
    count = {"n": 0}

    def numbered(tool, **args):
        count["n"] += 1
        numbered.last_ref = f"W{wake}.{count['n']}"
        return call(tool, **args)

    numbered.numbered = True
    numbered.last_ref = None
    return numbered


def model_agent(
    gateway, model, user_model, case, *, user_gateway=None, policy=None, tools=TOOLS, ledger_tools=False
):
    """`ledger_tools` is False (no ledger), True (the full Case Ledger) or a LedgerConfig."""
    ledger_on = bool(ledger_tools)
    ledger_config = None
    if ledger_on:
        from .case_ledger import LedgerConfig, ledger_tool_schemas

        ledger_config = ledger_tools if isinstance(ledger_tools, LedgerConfig) else LedgerConfig.full()
        tools = [*tools, *ledger_tool_schemas()]
    visits = {row["id"]: row for row in case["visits"]}
    tool_names = {item["function"]["name"] for item in tools}
    wakes = {"n": 0}
    if user_gateway is None:
        user_gateway = gateway

    def agent(session, call):
        wakes["n"] += 1
        if not getattr(call, "numbered", False):
            call = _numbered(call, wakes["n"])
        visit = (
            visits.get(session["trigger_id"])
            if user_model and session["trigger_type"] == "user_visit"
            else None
        )
        context = {key: value for key, value in session.items() if key != "message"}
        session_policy = policy or (
            SINGLE_SESSION_POLICY if session.get("single_session") else POLICY
        )
        if ledger_on:
            from .case_ledger import ledger_policy

            session_policy = f"{session_policy}\n{ledger_policy(ledger_config)}"
        messages = [
            {"role": "system", "content": session_policy},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        dialogue = []

        def stop_gate():
            """With the ledger on, stopping goes through end_session so the books are checked.
            Returns the rejection to show the model, or None when it may stop."""
            if not ledger_on:
                return None
            outcome = call("end_session")
            if isinstance(outcome, dict) and outcome.get("error"):
                messages.append({"role": "user", "content": json.dumps(outcome, ensure_ascii=False)})
                return outcome
            return None

        if "message" in session:
            messages.append({"role": "user", "content": session["message"]})
            dialogue.append({"customer": session["message"]})

        for _ in range(30):
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

            for tool_call in pending:
                function = tool_call["function"]
                name = function["name"]
                try:
                    args = json.loads(function["arguments"])
                    if name not in tool_names or not isinstance(args, dict):
                        raise ValueError
                    if name == "end_session" and not ledger_on:
                        return "LEFT" if visit is not None else None
                    result = call(name, **args)
                    if name == "end_session" and not (isinstance(result, dict) and result.get("error")):
                        return "LEFT" if visit is not None else None
                except (KeyError, TypeError, ValueError) as error:
                    agent.invalid_arguments += 1
                    # Kept for the result file: a malformed call and a bug behind a call look
                    # alike from outside, and the log is how they are told apart.
                    agent.invalid_log.append(
                        {"tool": name, "arguments": function.get("arguments"), "error": repr(error)[:200]}
                    )
                    result = {"error": "invalid_arguments"}
                ref = getattr(call, "last_ref", None) if result != {"error": "invalid_arguments"} else None
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": (f"[ref {ref}] " if ref else "") + json.dumps(result, ensure_ascii=False),
                    }
                )
            if pending:
                continue
            if visit is None or not text.strip():
                if stop_gate() is not None:
                    continue
                return None
            reply = _customer_reply(user_gateway, user_model, visit, dialogue)
            if reply.upper() in SIGNALS:
                return reply.upper()
            if not reply:
                return None
            call("user_reply", text=reply)
            dialogue.append({"customer": reply})
            messages.append({"role": "user", "content": reply})

    # Malformed calls never reach the environment, so the agent counts them itself.
    agent.invalid_arguments = 0
    agent.invalid_log = []
    return agent
