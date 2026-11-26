"""The three Case Ledger checks, as pure functions of the ledger state.

They test bookkeeping only (was the object read, is the evidence fresh, does every open promise have
someone to wake it) and never judge whether a business action was right. Rejection texts are English
and say what is missing and how to supply it, never which business action to take.
"""

import re
from datetime import datetime


def parse_time(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).strip().replace(" ", "T"))
    except ValueError:
        return None


def normalize_ref(text) -> str:
    return re.sub(r"^\s*\[?\s*(?:ref\s*)?", "", str(text or ""), flags=re.I).strip(" ]").upper()


def _squash(text) -> str:
    """Lower case, punctuation removed, whitespace collapsed: a quote matches however it is punctuated."""
    text = re.sub(r"['\u2019]", "", str(text))  # don't -> dont
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip().lower()


# -- check A: reconcile first -------------------------------------------------------------


def uncertain_text(entry) -> str:
    return f"{entry['ref']} {entry['tool']} on {entry['subject'] or 'the object'}: result unknown ({entry['error']})"


def reconcile(pending, journal, wake):
    """Mark every uncertain write that has since been read in this wake as reconciled."""
    for entry in pending:
        if entry["reconciled"]:
            continue
        if any(
            row["kind"] == "read" and row["wake"] == wake and row["seq"] > entry["seq"] and row["touches"] & entry["touches"]
            for row in journal
        ):
            entry["reconciled"] = True


def check_a(pending, journal, wake, target):
    """Return None, or the rejection for a write to the objects in `target`."""
    reconcile(pending, journal, wake)
    open_entries = [entry for entry in pending if not entry["reconciled"] and entry["touches"] & target]
    if not open_entries:
        return None
    shared = sorted(open_entries[0]["touches"] & target)[0]
    return {
        "error": "reconcile_first",
        "uncertain": [uncertain_text(entry) for entry in open_entries],
        "why": f"Read {shared} before writing to it again.",
    }


# -- check B: settle with evidence --------------------------------------------------------


def check_b(how, evidence, promise, refs, wake, customer_texts, policy):
    """Return None, or {"error", "why"} for a settle_promise call."""
    ref = normalize_ref(evidence)
    entry = refs.get(ref)
    if how == "done":
        if entry is None or entry["kind"] != "read":
            return {"error": "evidence_not_a_read", "why": "Evidence for done must be the ref of a read tool call."}
        if entry["wake"] != wake:
            return {"error": "evidence_not_fresh", "why": "The read must be from this session, after you woke up."}
        subject = promise.get("subject")
        if subject:
            if subject not in entry["touches"]:
                return {"error": "evidence_not_fresh", "why": f"The read must be about {subject}."}
            last_write = max(
                (row["seq"] for row in refs.values() if row["kind"] in {"write", "handoff"} and subject in row["touches"]),
                default=-1,
            )
            if entry["seq"] < last_write:
                return {"error": "evidence_not_fresh", "why": f"{subject} was written after that read; read it again."}
        return None
    if how == "handed_over":
        if entry is None or entry["kind"] != "handoff" or entry["error"]:
            return {"error": "evidence_not_a_handoff", "why": "Evidence for handed_over must be the ref of a handoff tool call that succeeded."}
        return None
    quote = _squash(evidence)
    if len(quote) >= 8 and (any(quote in _squash(text) for text in customer_texts) or quote in _squash(policy)):
        return None
    return {"error": "evidence_not_found", "why": "Quote at least 8 characters of the customer's words or of the policy."}


# -- check C: the books must balance ------------------------------------------------------


def promise_line(promise) -> str:
    return f"{promise['id']}: {promise['what']}"


def unbalanced(promises, followups, refs, push, now=None):
    """Open promises that nobody will wake, as readable lines.

    A promise whose due_by has come (now >= due_by) is balanced by any pending follow-up: nothing can be
    scheduled before a time that has passed, so asking for that would trap the agent.
    """
    problems = []
    pending = [task for task in followups.values() if task["status"] == "pending"]
    for promise in promises.values():
        if promise["status"] != "open":
            continue
        wake_by = promise["wake_by"]
        if wake_by == "customer":
            told = promise.get("told_ref")
            if told:
                entry = refs.get(normalize_ref(told))
                if entry is None or not entry.get("direct"):
                    problems.append(f"{promise_line(promise)} (told_ref is not a call in which you spoke with the customer)")
            elif not any(
                entry.get("direct") and entry["wake"] >= promise.get("created_wake", 0) for entry in refs.values()
            ):
                problems.append(f"{promise_line(promise)} (you have not spoken with the customer since you made this promise)")
        elif wake_by == "event":
            if not push:
                problems.append(f"{promise_line(promise)} (no system notification arrives in this mode; schedule a follow-up)")
        else:
            due = parse_time(promise.get("due_by"))
            overdue = due is not None and now is not None and now >= due
            ok = any(due is None or overdue or (parse_time(task["due_at"]) or due) <= due for task in pending)
            if not ok:
                if overdue:
                    problems.append(f"{promise_line(promise)} (overdue since {promise['due_by']}; no follow-up scheduled)")
                else:
                    when = f"before {promise['due_by']}" if due else "scheduled"
                    problems.append(f"{promise_line(promise)} (no follow-up {when})")
    return problems


def open_promises(promises):
    return [promise_line(p) for p in promises.values() if p["status"] == "open"]
