"""The scorekeeper: one extra model call per session that notes promises the agent forgot to record."""

import json
import re

from .prompts import SCOREKEEPER_PROMPT
from .statement import estimate_tokens

_FIELDS = ("to", "what", "done_when")


def digest(calls) -> str:
    """The session as the scorekeeper sees it: agent words and write actions, with step numbers."""
    lines = []
    for row in calls:
        if row["kind"] == "message":
            text = row["args"].get("text", "")
            lines.append(f"step {row['n']}: agent said: {text}")
        elif row["kind"] in {"write", "handoff"}:
            lines.append(f"step {row['n']}: {row['tool']} {json.dumps(row['args'], ensure_ascii=False)} -> {json.dumps(row['result'], ensure_ascii=False)}")
    return "\n".join(lines) or "(nothing yet)"


def parse(text):
    """Return the list of `missing` items, or None if the text is not the expected JSON."""
    if not isinstance(text, str):
        return None
    for candidate in (text, *re.findall(r"\{.*\}", text, flags=re.S)):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        missing = data.get("missing") if isinstance(data, dict) else None
        if isinstance(missing, list):
            return [item for item in missing if isinstance(item, dict)]
    return None


def run(ask, policy, statement, calls, steps):
    """Call the model once (twice if the JSON is unreadable). Returns (accepted, stats).

    `ask(prompt) -> text`. `steps` is the set of step numbers of this session; items citing any other
    step are dropped and counted as scorekeeper_bad_ref.
    """
    prompt = (
        SCOREKEEPER_PROMPT.replace("{policy}", policy)
        .replace("{ledger_statement}", statement)
        .replace("{session_digest}", digest(calls))
    )
    stats = {"calls": 0, "tokens": 0, "scorekeeper_error": 0, "scorekeeper_bad_ref": 0}
    items = None
    for _ in range(2):
        reply = ask(prompt)
        stats["calls"] += 1
        stats["tokens"] += estimate_tokens(prompt) + estimate_tokens(reply or "")
        items = parse(reply)
        if items is not None:
            break
    if items is None:
        stats["scorekeeper_error"] = 1
        return [], stats
    accepted = []
    for item in items:
        if not all(item.get(field) for field in _FIELDS) or item.get("source_step") not in steps:
            stats["scorekeeper_bad_ref"] += 1
            continue
        accepted.append(item)
    return accepted, stats
