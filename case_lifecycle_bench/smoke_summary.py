"""Aggregate the offline CRM smoke panel without imposing a decision threshold."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from .eval_checks import duty_detection_times

_REVIEW_STATUSES = {
    "pending_review",
    "confirmed_simulator_issue",
    "not_simulator_issue",
    "unattributed",
}


def _average(values):
    return sum(values) / len(values) if values else None


def _stats(values):
    return {"mean": _average(values), "n": len(values)}


def _gap(left, right):
    return None if left is None or right is None else left - right


def _key(row):
    return row["case_id"], row["wake"], row["trial"]


def _identity(key):
    return dict(zip(("case_id", "wake", "trial"), key))


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _metrics(run):
    metrics = run["evaluation"]["metrics"]
    trajectory = run["run"]["trajectory"]
    exposure = Counter(
        row.get("channel")
        for row in trajectory
        if row["kind"] == "session_start" and row["trigger_type"] == "user_visit"
    )
    conversations = sum(exposure.values())
    conversation_success = metrics.get("conversation_success")
    case_resolution = metrics.get("case_resolution")
    return {
        "conversation_success": conversation_success,
        "case_resolution": case_resolution,
        "detection_rate": metrics.get("detection_rate"),
        "h1_gap": _gap(conversation_success, case_resolution),
        "conversations": conversations,
        "user_revisits": max(conversations - 1, 0),
        "conversations_by_channel": dict(exposure),
        "duty_events": duty_detection_times(trajectory),
    }


def _case_comparison(case, by_key, wakes, trials):
    case_id = case["case_id"]
    indexed = {
        wake: {
            trial: by_key[case_id, wake, trial]
            for trial in trials
            if (case_id, wake, trial) in by_key
        }
        for wake in wakes
    }
    by_wake = {}
    for wake, rows in indexed.items():
        by_wake[wake] = {
            "runs": len(rows),
            "trials": [{"trial": trial, **metrics} for trial, metrics in rows.items()],
            "conversations": sum(row["conversations"] for row in rows.values()),
            **{
                key: _stats([row[key] for row in rows.values() if row[key] is not None])
                for key in (
                    "conversation_success",
                    "case_resolution",
                    "detection_rate",
                    "h1_gap",
                )
            },
        }

    pair_trials = [
        {
            "trial": trial,
            "h1_mean_across_wakes": _average([poll["h1_gap"], push["h1_gap"]])
            if poll["h1_gap"] is not None and push["h1_gap"] is not None
            else None,
            "h2_push_minus_poll": _gap(push["detection_rate"], poll["detection_rate"]),
            "user_revisits_poll_minus_push": poll["user_revisits"]
            - push["user_revisits"],
        }
        for trial in trials
        if (poll := indexed["poll"].get(trial)) is not None
        and (push := indexed["push"].get(trial)) is not None
    ]
    return {
        "case_id": case_id,
        "template": case["template"],
        "by_wake": by_wake,
        "pairs": pair_trials,
        "h1_paired_wakes": _stats(
            [
                row["h1_mean_across_wakes"]
                for row in pair_trials
                if row["h1_mean_across_wakes"] is not None
            ]
        ),
        "h2": _stats(
            [
                row["h2_push_minus_poll"]
                for row in pair_trials
                if row["h2_push_minus_poll"] is not None
            ]
        ),
    }


def _cohort(rows):
    def collect(summaries):
        case_means = [stats["mean"] for stats in summaries if stats["n"]]
        return {
            "mean": _average(case_means),
            "n_cases": len(case_means),
            "n_trials": sum(stats["n"] for stats in summaries),
        }

    wake_trials = {
        wake: [trial for row in rows for trial in row["by_wake"][wake]["trials"]]
        for wake in ("poll", "push")
    }
    revisit_totals = {
        wake: sum(row["user_revisits"] for row in trials)
        for wake, trials in wake_trials.items()
    }
    return {
        "h1_by_wake": {
            wake: {
                "gap": collect([row["by_wake"][wake]["h1_gap"] for row in rows]),
                "conversation_success": collect(
                    [row["by_wake"][wake]["conversation_success"] for row in rows]
                ),
                "case_resolution": collect(
                    [row["by_wake"][wake]["case_resolution"] for row in rows]
                ),
                "n_conversations": sum(
                    row["by_wake"][wake]["conversations"] for row in rows
                ),
            }
            for wake in ("poll", "push")
        },
        "h1_paired_wakes": collect([row["h1_paired_wakes"] for row in rows]),
        "h2_push_minus_poll": collect([row["h2"] for row in rows]),
        "n_present_wake_pairs": sum(len(row["pairs"]) for row in rows),
        "detection_by_wake": {
            wake: collect([row["by_wake"][wake]["detection_rate"] for row in rows])
            for wake in ("poll", "push")
        },
        "detection_delay_by_wake": {
            wake: _detection_delays(trials) for wake, trials in wake_trials.items()
        },
        "user_revisits_by_wake": {
            wake: {"total": revisit_totals[wake], "n_trials": len(trials)}
            for wake, trials in wake_trials.items()
        },
        "paired_user_revisits_poll_minus_push": sum(
            pair["user_revisits_poll_minus_push"]
            for row in rows
            for pair in row["pairs"]
        ),
    }


def _detection_delays(trials):
    events = [event for trial in trials for event in trial["duty_events"]]
    return {
        "realized_events": len(events),
        **{
            name: _delay_stats(
                [event[name] for event in events if event[name] is not None]
            )
            for name in ("event_to_detection_hours", "opportunity_to_detection_hours")
        },
    }


def _delay_stats(values):
    return {**_stats(values), "median": median(values) if values else None}


def summarize_smoke(runs, manifest, *, simulator_reviews=()):
    """Use trial-matched wake pairs and weight each case equally within a cohort."""
    cases = manifest["panel"]["cases"]
    panel = {row["case_id"]: row for row in cases}
    wakes = tuple(manifest["wakes"])
    if (
        len(panel) != len(cases)
        or set(wakes) != {"poll", "push"}
        or tuple(manifest["settings"]) != ("crm",)
    ):
        raise ValueError(
            "Smoke manifest must specify unique cases, poll/push, and CRM memory"
        )
    trials = tuple(range(manifest["trials"]))
    expected = {
        (case_id, wake, trial)
        for case_id in panel
        for wake in wakes
        for trial in trials
    }
    recorded = defaultdict(list)
    for run in runs:
        recorded[_key(run)].append(run)
    excluded = []
    usable = {}
    for key, copies in sorted(recorded.items()):
        if key not in expected:
            excluded.append({**_identity(key), "reason": "unexpected_run"})
            continue
        if len(copies) != 1:
            excluded.append(
                {**_identity(key), "reason": "duplicate_run", "count": len(copies)}
            )
            continue
        run = copies[0]
        meta = run.get("meta", {})
        actual = (
            run["memory"],
            meta.get("agent_model"),
            meta.get("user_model"),
            meta.get("case_sha256"),
        )
        wanted = (
            "crm",
            manifest["model"],
            manifest["user_model"],
            panel[key[0]]["case_sha256"],
        )
        if actual != wanted:
            excluded.append({**_identity(key), "reason": "metadata_mismatch"})
            continue
        usable[key] = _metrics(run)

    reviews = {}
    for review in simulator_reviews:
        key = _key(review)
        if (
            key not in expected
            or key in reviews
            or review["status"] not in _REVIEW_STATUSES
        ):
            raise ValueError(f"Invalid simulator review for {key}")
        reviews[key] = review
    simulator_rows = [
        {
            **_identity(key),
            "status": "pending_review",
            **reviews.get(key, {}),
            "case_resolution": usable[key]["case_resolution"],
        }
        for key in sorted(usable)
    ]
    comparisons = [_case_comparison(case, usable, wakes, trials) for case in cases]
    templates = sorted({row["template"] for row in cases})
    return {
        "complete": bool(expected) and set(usable) == expected and not excluded,
        "model": manifest["model"],
        "user_model": manifest["user_model"],
        "expected_runs": len(expected),
        "usable_runs": len(usable),
        "missing": [_identity(key) for key in sorted(expected - recorded.keys())],
        "excluded": excluded,
        "overall": _cohort(comparisons),
        "by_template": {
            template: _cohort(
                [row for row in comparisons if row["template"] == template]
            )
            for template in templates
        },
        "cases": comparisons,
        "simulator_review": {
            "status_counts": dict(Counter(row["status"] for row in simulator_rows)),
            "failed_run_status_counts": dict(
                Counter(
                    row["status"]
                    for row in simulator_rows
                    if row["case_resolution"] is not None and not row["case_resolution"]
                )
            ),
            "runs": simulator_rows,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Panel-run output directory")
    parser.add_argument(
        "--simulator-reviews",
        type=Path,
        help="JSON list of separately reviewed simulator labels",
    )
    args = parser.parse_args()
    reviews = _read(args.simulator_reviews) if args.simulator_reviews else ()
    manifest = _read(args.output / "manifest.json")
    runs = (
        _read(path)
        for path in sorted(args.output.glob("*.json"))
        if path.name.endswith(("--0.json", "--1.json", "--2.json"))
    )
    print(
        json.dumps(summarize_smoke(runs, manifest, simulator_reviews=reviews), indent=2)
    )


if __name__ == "__main__":
    main()
