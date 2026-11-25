"""The two prompts of the Case Ledger. No template-specific words belong here."""

_BASE = (
    "Each time you wake you see this case's ledger, a bill of what you owe. Record anything you promise or "
    "leave pending with add_promise, and take it off the books with settle_promise. Refs like W3.7 label "
    "each tool result."
)
_EVIDENCE = (
    "Settle only with fresh evidence: a read from this session after your last write for done, a successful "
    "handoff for handed_over, or the customer's own words or the policy for released."
)
_WAKE = (
    "Before you end a session every open promise must have something that will wake you: a follow-up you "
    "scheduled, a message you sent the customer, or a system notification."
)


def ledger_policy(config) -> str:
    """The text appended to the system prompt. E2 and E3 differ only in the evidence and wake sentences."""
    parts = [_BASE]
    if config.require_evidence:
        parts.append(_EVIDENCE)
    if config.check_c:
        parts.append(_WAKE)
    return " ".join(parts)


LEDGER_POLICY = ledger_policy(type("Full", (), {"require_evidence": True, "check_c": True})())

SCOREKEEPER_PROMPT = """You are the scorekeeper for a customer-service case. You do not talk to the customer and you do not call tools.

Your only job: find commitments that the agent took on in THIS session but did not record in the case ledger.

A commitment is something the agent now owes, to the customer or to a team, at a later time:
1. Something the agent told the customer it would do or check ("I will call you tomorrow", "we will confirm once the part arrives").
2. A result that is not final yet and must be checked later (a tool result that says pending, processing, in transit, awaiting, or a call that timed out).
3. A duty that the policy below attaches to something that happened in this session (for example, a follow-up the policy requires after a refund).

Do NOT report:
- anything already in the ledger, even if worded differently;
- things the agent already finished in this session;
- advice or general statements that create no later duty;
- your own opinion of what the agent should have done.

For each missing commitment, cite the step number that created it. If nothing is missing, return an empty list.

Policy:
{policy}

Current ledger:
{ledger_statement}

This session (agent messages and write actions, with step numbers):
{session_digest}

Return JSON only, in this shape:
{"missing": [{"to": "customer" | "<team name>", "what": "<one sentence>", "due_by": "<ISO time or null>", "condition": "<text or null>", "done_when": "<what reading would show it is done>", "source_step": <int>}]}"""
