"""Model-facing policy and tools for the synthetic insurance environment."""

from ...model_agent import TOOLS as RETAIL_TOOLS
from ...model_agent import _tool

POLICY = """You handle synthetic insurance renewals. Use the virtual local time in the session context.
At intake, find or create the customer's policy ticket, check the policy and quote,
and schedule the first quote review at the next 09:00 within 24 hours. If the quote
is still under review at a follow-up, schedule another review at the next 09:00.
A system event wakes you to read the current quote. If it is ready, act immediately
when the contact window is open. Otherwise keep or schedule a review at the next opening.
Reuse the ticket and inspect current state before acting. Confirm voice opt-in before
starting a simulated_voice call. Call only from 09:00 inclusive to 18:00 exclusive,
at most once per rolling 24 hours, and never start another call after confirmation.
An answered call creates a separate live contact. In that contact or an inbound secure
chat, verify identity before revealing details. Use present_renewal_quote to present
the current quote and get the scripted customer's decision. Submit only when that
contact explicitly accepted the current version. After successful submission, read
the authoritative policy again. Deliver the verified confirmation to the customer with
the notification tool, record the outcome in CRM, then close the case. Keep a case
open if confirmation remains outstanding. End each session when its work is done."""

_SHARED = {
    "get_current_time",
    "get_ticket",
    "schedule_followup",
    "list_followups",
    "cancel_followup",
    "add_ticket_note",
    "close_ticket",
    "end_session",
}
_QUOTE = {"contact_id": "string", "policy_id": "string", "quote_version": "string"}
TOOLS = [
    *(tool for tool in RETAIL_TOOLS if tool["function"]["name"] in _SHARED),
    _tool(
        "search_tickets",
        "Find CRM tickets for this customer and policy.",
        {"user_id": "string", "policy_id": "string"},
    ),
    _tool(
        "create_ticket",
        "Create a CRM ticket for this customer and policy.",
        {"user_id": "string", "policy_id": "string", "summary": "string"},
    ),
    _tool(
        "read_policy",
        "Read the authoritative policy and renewal status.",
        {"policy_id": "string"},
    ),
    _tool(
        "read_renewal_quote",
        "Read the authoritative quote and its current version.",
        {"policy_id": "string"},
    ),
    _tool(
        "read_contact_preferences",
        "Check voice consent and prior attempts.",
        {"customer_id": "string"},
    ),
    _tool(
        "start_outbound_call",
        "Start a simulated_voice call and queue a live contact if answered.",
        {"customer_id": "string", "channel": "string"},
    ),
    _tool(
        "verify_customer_identity",
        "Verify the identity of the live contact.",
        {"contact_id": "string"},
    ),
    _tool(
        "present_renewal_quote",
        "Present the current quote and collect the customer's decision.",
        _QUOTE,
    ),
    _tool(
        "submit_renewal",
        "Submit a renewal after the live contact accepts the current quote.",
        _QUOTE,
    ),
    _tool(
        "notify_renewal_confirmation",
        "Deliver confirmation of verified renewal to the live contact.",
        _QUOTE,
    ),
]
