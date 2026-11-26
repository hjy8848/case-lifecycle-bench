"""Render the ledger as the bill the agent sees on every wake (plain English text)."""

from .checks import parse_time


def estimate_tokens(text: str) -> int:
    """About four characters per token. The gateway does not return usage, so this is an estimate."""
    return max(1, len(text) // 4)


def _wake_note(promise, followups, push, now=None) -> str:
    wake_by = promise["wake_by"]
    if wake_by == "customer":
        return "customer contact" + (f" (spoke at {promise['told_ref']})" if promise.get("told_ref") else "")
    if wake_by == "event":
        return "system notification" + ("" if push else " (not delivered in this mode)")
    due = parse_time(promise.get("due_by"))
    overdue = due is not None and now is not None and now >= due
    pending = [
        t for t in followups.values()
        if t["status"] == "pending" and (due is None or overdue or (parse_time(t["due_at"]) or due) <= due)
    ]
    if pending:
        first = min(pending, key=lambda t: t["due_at"])
        return f"follow-up {first['task_id']} at {first['due_at']}"
    return "no follow-up scheduled yet"


def render(book, session) -> str:
    cfg = book.config
    now = parse_time(session.get("t"))
    lines = [f"CASE LEDGER (wake {book.wake_label}, time {session.get('t', 'unknown')})"]
    open_ = [p for p in book.promises.values() if p["status"] == "open"]
    lines.append("Open promises:" if open_ else "Open promises: none")
    for p in open_:
        parts = [f"{p['id']} to {p['to']}: {p['what']}", f"done when: {p['done_when']}"]
        if p.get("due_by"):
            due = parse_time(p["due_by"])
            if due is not None and now is not None and now >= due:
                hours = int((now - due).total_seconds() // 3600)
                parts.append(f"OVERDUE since {p['due_by']} ({hours // 24}d {hours % 24}h)")
            else:
                parts.append(f"due by: {p['due_by']}")
        if p.get("condition"):
            parts.append(f"only if: {p['condition']}")
        if p.get("subject"):
            parts.append(f"about: {p['subject']}")
        parts.append(f"wake: {_wake_note(p, book.followups, book.push, now)}")
        lines.append("  " + " | ".join(parts))
    if cfg.show_uncertain:
        unknown = [e for e in book.pending if not e["reconciled"]]
        if unknown:
            lines.append("Results not known (read the object before writing to it again):")
            for e in unknown:
                lines.append(f"  {e['ref']} {e['tool']} on {e['subject'] or 'the object'}: {e['error']}")
    if cfg.statement_journal:
        writes = [e for e in book.journal if e["kind"] in {"write", "handoff"}]
        if writes:
            lines.append("Writes so far:")
            for e in writes[-25:]:
                outcome = f"error {e['error']}" if e["error"] else "ok"
                lines.append(f"  {e['ref']} {e['tool']} {e['subject'] or ''} -> {outcome}".rstrip())
    settled = [p for p in book.promises.values() if p["status"] != "open"]
    if settled:
        lines.append("Settled: " + "; ".join(f"{p['id']} ({p['status']}, evidence {p.get('evidence')})" for p in settled))
    return "\n".join(lines)
