"""Summarize the offline CRM, Full History, and Case Ledger comparison."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

SETTINGS = ("crm", "full-history", "ledger")
_DUPLICATE_GUARDS = {
    "issue_refund": "A refund already exists.",
    "create_ticket": "Reuse the existing ticket",
}
_REFUND_CASES = (
    "refund_silent_failure",
    "refund_timeout_committed",
    "cross_channel_revisit",
)
_P8_CASES = ("refund_silent_failure", "cross_channel_revisit")
_METRICS = (
    "case_resolution",
    "conversation_success",
    "refund_failure_observed",
    "failure_notified_in_detection_session",
    "observed_failure_notified_in_detection_session",
    "committed_timeout_triggered",
    "queried_after_committed_timeout",
    "initial_followup_scheduled",
    "retry_followups_scheduled",
    "original_ticket_reused_on_revisit",
    "duplicate_refund_attempted",
    "duplicate_refund_committed",
    "duplicate_ticket_attempted",
    "duplicate_ticket_committed",
    "run_with_guard_action",
)


def _failed_refund_evidence(run):
    orders = run["initial_state"]["orders"]
    observations = {}
    notices = defaultdict(list)
    failures = []
    for row in run["trajectory"]:
        if row["kind"] == "world_event":
            for refund_id, refund in row.get("changes", {}).get("refunds", {}).items():
                if refund["status"] == "failed":
                    observations.pop(refund_id, None)
                    failures.append(
                        {
                            "event_id": row["event_id"],
                            "event_seq": row["seq"],
                            "refund_id": refund_id,
                            "user_id": orders[refund["order_id"]]["user_id"],
                            "observed_seq": None,
                            "session_id": None,
                            "notification_seq": None,
                        }
                    )
                    observations[refund_id] = failures[-1]
        if row["kind"] != "tool_call":
            continue
        tool = row["tool"]
        if tool in {"get_refund", "list_refunds"}:
            result = row["result"]
            refunds = result if isinstance(result, list) else [result]
            for refund in refunds:
                if not isinstance(refund, dict) or refund.get("status") != "failed":
                    continue
                failure = observations.get(refund.get("refund_id"))
                if failure is not None and failure["observed_seq"] is None:
                    failure["observed_seq"] = row["seq"]
                    failure["session_id"] = row["session_id"]
        if (
            tool == "send_message_to_user"
            and row["committed"]
            and row["args"]["text"].strip()
        ):
            notices[row["session_id"], row["args"]["user_id"]].append(row["seq"])

    for failure in failures:
        if failure["observed_seq"] is not None:
            failure["notification_seq"] = next(
                (
                    seq
                    for seq in notices[failure["session_id"], failure["user_id"]]
                    if seq > failure["observed_seq"]
                ),
                None,
            )
        del failure["user_id"]
    return failures


def _duplicate_calls(run):
    initial = run["initial_state"]
    refund_orders = {row["order_id"] for row in initial["refunds"].values()}
    ticket_orders = defaultdict(set)

    def add_ticket(row):
        ticket_orders[row["user_id"]].update(row["order_ids"])

    for ticket in initial["tickets"].values():
        add_ticket(ticket)
    attempts = defaultdict(list)
    for row in run["trajectory"]:
        if row["kind"] != "tool_call":
            continue
        tool, args = row["tool"], row["args"]
        duplicate = (tool == "issue_refund" and args["order_id"] in refund_orders) or (
            tool == "create_ticket"
            and bool(ticket_orders[args["user_id"]].intersection(args["order_ids"]))
        )
        if duplicate:
            attempts[tool].append(
                {key: row[key] for key in ("seq", "session_id", "committed")}
            )
        changes = row.get("changes", {})
        refund_orders.update(
            refund["order_id"] for refund in changes.get("refunds", {}).values()
        )
        for ticket in changes.get("tickets", {}).values():
            add_ticket(ticket)
    return attempts


def _ratio(values):
    return {"numerator": sum(values), "denominator": len(values)}


def _run_summary(result):
    state = result["run"]
    evaluation = result["evaluation"]
    metrics = evaluation["metrics"]
    process = evaluation["process"]
    evidence = evaluation.get("evidence", {})
    case_id = result["case_id"]
    setting = result["memory"]
    guard_events = result.get("ledger_guard_events")
    failures = _failed_refund_evidence(state) if case_id.startswith(_P8_CASES) else []
    duplicate = _duplicate_calls(state)
    guarded_duplicates = {
        tool: [
            event
            for event in guard_events or []
            if event["tool"] == tool and event["reason"].startswith(reason)
        ]
        for tool, reason in _DUPLICATE_GUARDS.items()
    }
    observations = defaultdict(list)

    def add(metric, observed):
        if observed is not None:
            observations[metric].append(observed)

    for metric in ("case_resolution", "conversation_success"):
        add(metric, metrics[metric])
    for failure in failures:
        observed = failure["observed_seq"] is not None
        notified = failure["notification_seq"] is not None
        add("refund_failure_observed", observed)
        add("failure_notified_in_detection_session", notified)
        if observed:
            add("observed_failure_notified_in_detection_session", notified)

    add("committed_timeout_triggered", process.get("timeout_after_commit_triggered"))
    if process.get("timeout_after_commit_triggered"):
        action = evidence.get("first_relevant_action_after_timeout") or {}
        add(
            "queried_after_committed_timeout",
            (action.get("action") == "query" and process["timeout_verified"]),
        )

    committed_tools = {
        row["tool"]
        for row in state["trajectory"]
        if row["kind"] == "tool_call" and row["committed"]
    }
    if case_id.startswith(_REFUND_CASES):
        if "issue_refund" in committed_tools:
            add("initial_followup_scheduled", process.get("initial_followup_scheduled"))
        if "retry_refund" in committed_tools:
            add("retry_followups_scheduled", process.get("retry_followups_scheduled"))
    add(
        "original_ticket_reused_on_revisit",
        process.get("reused_original_ticket_on_revisit"),
    )

    for tool, name in (("issue_refund", "refund"), ("create_ticket", "ticket")):
        if tool in committed_tools or duplicate[tool] or guarded_duplicates[tool]:
            if setting != "ledger" or guard_events is not None:
                add(
                    f"duplicate_{name}_attempted",
                    bool(duplicate[tool] or guarded_duplicates[tool]),
                )
            add(
                f"duplicate_{name}_committed",
                any(row["committed"] for row in duplicate[tool]),
            )
    if guard_events is not None:
        add("run_with_guard_action", bool(guard_events))

    return {
        "case_id": case_id,
        "setting": setting,
        "counts": {metric: _ratio(values) for metric, values in observations.items()},
        "failure_evidence": failures,
        "timeout_evidence": evidence,
        "duplicate_calls": dict(duplicate),
        "guarded_duplicate_calls": guarded_duplicates,
        "guard_events": guard_events,
    }


def _pairing_mismatches(by_case):
    mismatches = []
    for case_id, runs in sorted(by_case.items()):
        for key in ("agent", "wake", "agent_model", "user_model", "case_sha256"):
            defined = {
                run["memory"]: run.get(key) or run.get("meta", {}).get(key)
                for run in runs
            }
            if len({value for value in defined.values() if value is not None}) > 1:
                mismatches.append({"case_id": case_id, "field": key, "values": defined})
    return mismatches


def summarize_runs(runs, *, expected_cases=None, expected_settings=SETTINGS):
    """Aggregate trace output and expose coverage for the expected case-setting matrix."""
    runs = list(runs)
    case_ids = sorted(
        set(expected_cases)
        if expected_cases is not None
        else {run["case_id"] for run in runs}
    )
    settings = tuple(expected_settings)
    expected = {(case_id, setting) for case_id in case_ids for setting in settings}
    pairs = defaultdict(list)
    for result in runs:
        pairs[result["case_id"], result["memory"]].append(result)

    unique = {
        pair: copies[0]
        for pair, copies in pairs.items()
        if pair in expected and len(copies) == 1
    }
    missing = [
        {"case_id": case_id, "setting": setting}
        for case_id, setting in sorted(expected - pairs.keys())
    ]
    duplicates = [
        {"case_id": case_id, "setting": setting, "count": len(copies)}
        for (case_id, setting), copies in sorted(pairs.items())
        if len(copies) > 1
    ]
    unexpected = [
        {"case_id": case_id, "setting": setting}
        for case_id, setting in sorted(pairs.keys() - expected)
    ]
    by_case = defaultdict(list)
    for (case_id, _), result in unique.items():
        by_case[case_id].append(result)
    mismatches = _pairing_mismatches(by_case)
    unpaired = {row["case_id"] for row in mismatches}
    paired = [
        case_id
        for case_id in case_ids
        if len(by_case[case_id]) == len(settings) and case_id not in unpaired
    ]
    summaries = [_run_summary(result) for _, result in sorted(unique.items())]
    by_setting = {
        setting: {
            "runs": 0,
            "counts": {metric: _ratio([]) for metric in _METRICS},
            "guard_events_by_tool": {},
        }
        for setting in settings
    }
    guard_totals = defaultdict(Counter)
    for row in summaries:
        stats = by_setting[row["setting"]]
        stats["runs"] += 1
        for name, counts in row["counts"].items():
            stats["counts"][name]["numerator"] += counts["numerator"]
            stats["counts"][name]["denominator"] += counts["denominator"]
        guard_totals[row["setting"]].update(
            event["tool"] for event in row["guard_events"] or []
        )
    for setting, totals in guard_totals.items():
        by_setting[setting]["guard_events_by_tool"] = dict(sorted(totals.items()))

    return {
        "complete": bool(expected)
        and not (missing or duplicates or unexpected or mismatches),
        "expected_case_count": len(case_ids),
        "expected_run_count": len(expected),
        "input_run_count": len(runs),
        "paired_cases": paired,
        "missing": missing,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "pairing_mismatches": mismatches,
        "settings": by_setting,
        "runs": summaries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files", type=Path, nargs="+", help="Saved per-run CLI JSON with --trace"
    )
    parser.add_argument(
        "--panel", type=Path, help="Expected case manifest (list or {cases: [...]})."
    )
    args = parser.parse_args()

    def read(path):
        return json.loads(path.read_text(encoding="utf-8-sig"))

    expected_cases = None
    if args.panel is not None:
        panel = read(args.panel)
        entries = panel["cases"] if isinstance(panel, dict) else panel
        expected_cases = [entry["case_id"] for entry in entries]
    runs = [read(path) for path in args.files]
    print(json.dumps(summarize_runs(runs, expected_cases=expected_cases), indent=2))


if __name__ == "__main__":
    main()
