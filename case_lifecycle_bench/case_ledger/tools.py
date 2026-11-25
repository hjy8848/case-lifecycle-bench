"""Tool classification and the two bookkeeping tools of the Case Ledger.

The classification is by name only, with one default rule for every template. A template-specific
exception may only be added to KIND_OVERRIDES below, as data; never as a branch in code.
"""

import re

READ_PREFIXES = ("read_", "get_", "list_", "search_", "check_", "track_", "view_", "lookup_")
MESSAGE_TOOLS = {"say", "send_message_to_user", "say_to_customer"}
CUSTOMER_TURN = {"user_reply"}  # the simulated customer talking, not something the agent does
CONTROL_TOOLS = {"end_session"}
LEDGER_TOOLS = {"add_promise", "settle_promise"}
_HANDOFF = re.compile(r"^escalate_|handoff|refer|transfer")
SUBJECT_KEYS = ("subject_id", "order_id", "refund_id", "claim_id", "application_id", "ticket_id")
# Ids of people, calls and follow-up tasks are not objects a write can leave in an unknown state.
NOT_OBJECTS = {"user_id", "customer_id", "contact_id", "task_id"}

# Data-table exceptions, tool name -> kind. Empty unless the lead adds a row after the review.
KIND_OVERRIDES: dict[str, str] = {}


def tool_kind(name: str) -> str:
    """One of: read, write, handoff, message, customer, control, ledger."""
    if name in KIND_OVERRIDES:
        return KIND_OVERRIDES[name]
    if name in LEDGER_TOOLS:
        return "ledger"
    if name in CONTROL_TOOLS:
        return "control"
    if name in CUSTOMER_TURN:
        return "customer"
    if name in MESSAGE_TOOLS:
        return "message"
    if name.startswith(READ_PREFIXES):  # first: read_contact_preferences contains "refer" but is a read
        return "read"
    if _HANDOFF.search(name):
        return "handoff"
    return "write"


def _object_keys(args: dict) -> list:
    """Every *_id argument that names an object, the six usual keys first (in order), the rest by name."""
    named = [key for key in SUBJECT_KEYS if args.get(key) not in (None, "")]
    rest = sorted(
        key for key in args if key.endswith("_id") and key not in SUBJECT_KEYS and key not in NOT_OBJECTS
        and args.get(key) not in (None, "")
    )
    return named + rest


def subject_of(args: dict):
    """The object id a call is about: the first usual key present, else the first other *_id by name."""
    keys = _object_keys(args)
    return str(args[keys[0]]) if keys else None


def touches(args: dict) -> set:
    """Every object id a call mentions. A read of any of them counts as a read of that object."""
    return {str(args[key]) for key in _object_keys(args)}


def ledger_tool_schemas() -> list[dict]:
    def prop(kind, description=None, enum=None):
        out = {"type": kind}
        if description:
            out["description"] = description
        if enum:
            out["enum"] = enum
        return out

    return [
        {
            "type": "function",
            "function": {
                "name": "add_promise",
                "description": (
                    "Record something you now owe. wake_by says what will bring you back to it: a follow-up "
                    "you schedule (followup), the customer contacting you (customer; you must have spoken with "
                    "them in this or a later session; told_ref optionally names that call), or a system "
                    "notification (event)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": prop("string", "Who is owed: the customer or a team name."),
                        "what": prop("string", "What you owe."),
                        "done_when": prop("string", "What reading would show it is done."),
                        "due_by": prop("string", "ISO time by which it is due, if any."),
                        "condition": prop("string", "A condition that must hold before you act, if any."),
                        "subject": prop("string", "The object id this is about, if any."),
                        "wake_by": prop("string", "followup, customer or event.", ["followup", "customer", "event"]),
                        "told_ref": prop("string", "Optional: ref of the call in which you spoke to the customer."),
                    },
                    "required": ["to", "what", "done_when"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "settle_promise",
                "description": (
                    "Take a promise off the books. how is done, handed_over or released. evidence is a ref "
                    "such as W3.7; for released, quote the customer's words."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "promise_id": prop("string"),
                        "how": prop("string", None, ["done", "handed_over", "released"]),
                        "evidence": prop("string"),
                    },
                    "required": ["promise_id", "how"],
                },
            },
        },
    ]
