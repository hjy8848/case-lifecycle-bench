"""Run a fixed memory comparison or the paired CRM pilot smoke test."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

SETTINGS = ("crm", "full-history", "ledger")
SMOKE_WAKES = ("poll", "push")
SMOKE_TRIALS = 3
PILOT_TEMPLATES = {
    "refund_silent_failure": "t1",
    "refund_timeout_committed": "t2",
    "cancel_partial_success": "t3",
    "cross_channel_revisit": "t9",
}


@dataclass(frozen=True)
class Job:
    case: dict
    memory: str
    wake: str
    trial: int | None = None

    @property
    def filename(self):
        identifier = self.case["case_id"]
        if self.trial is None:
            return f"{identifier}--{self.memory}.json"
        return f"{identifier}--{self.wake}--{self.trial}.json"

    @property
    def label(self):
        return self.filename.removesuffix(".json")


def _smoke_panel(pilot):
    records = pilot["results"]
    if len(records) != 30:
        raise ValueError("The official smoke protocol expects 30 pilot cases")
    panel = []
    for record in records:
        case = record["case"]
        if not all(
            record["wake_modes"][wake]["evaluation"]["metrics"]["case_resolution"]
            for wake in SMOKE_WAKES
        ):
            raise ValueError(
                f"Pilot case {case['case_id']} failed offline Oracle validation"
            )
        if any(
            event.get("duty") and not event.get("notify_agent")
            for event in case["events"]
        ):
            raise ValueError(
                f"Regenerate the pilot: {case['case_id']} has a silent duty event"
            )
        panel.append(
            {
                "case_id": case["case_id"],
                "template": PILOT_TEMPLATES[case["template"]],
                "order_id": case["retail_source"]["order_id"],
                "case_sha256": hashlib.sha256(
                    json.dumps(case, sort_keys=True).encode()
                ).hexdigest(),
            }
        )
    if len({row["order_id"] for row in panel}) != len(panel):
        raise ValueError("The smoke protocol expects unique pilot orders")
    return {"cases": panel}


def _write(path, result):
    partial = path.with_suffix(".partial")
    partial.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    partial.replace(path)


def _validate(result, job, args):
    expected = (
        job.case["case_id"],
        job.memory,
        job.wake,
        args.model,
        args.user_model,
        job.trial,
    )
    meta = result.get("meta", {})
    actual = (
        result.get("case_id"),
        result.get("memory"),
        result.get("wake"),
        meta.get("agent_model"),
        meta.get("user_model"),
        result.get("trial"),
    )
    if actual != expected or "run" not in result or "evaluation" not in result:
        raise ValueError(f"Unexpected model-run metadata for {job.label}")
    if job.trial is not None and meta.get("case_sha256") != job.case["case_sha256"]:
        raise ValueError(f"Pilot case hash differs from the run for {job.label}")


def _run(job, path, args):
    command = [
        sys.executable,
        "-m",
        "case_lifecycle_bench",
        "--case",
        job.case["template"],
        "--retail-order",
        job.case["order_id"],
        "--memory",
        job.memory,
        "--wake",
        job.wake,
        "--model",
        args.model,
        "--user-model",
        args.user_model,
        "--trace",
    ]
    if job.trial is not None:
        command.extend(("--expect-case-sha256", job.case["case_sha256"]))
    completed = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace"
    )
    if completed.returncode:
        error = completed.stderr.strip().splitlines()[-1:] or ["No diagnostic output"]
        raise RuntimeError(f"{job.label}: {error[0]}")
    result = json.loads(completed.stdout)
    if job.trial is not None:
        result["trial"] = job.trial
    _validate(result, job, args)
    _write(path, result)
    return result["evaluation"]["metrics"]["case_resolution"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--panel", type=Path, help="Run the memory comparison panel")
    source.add_argument(
        "--pilot", type=Path, help="Run the 30-case official CRM smoke protocol"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--user-model")
    parser.add_argument("--wake", choices=("poll", "push"), default="poll")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and count jobs without API calls",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    args.user_model = args.user_model or args.model
    if args.pilot and args.user_model == args.model:
        parser.error("The official smoke protocol requires a separate --user-model")
    source_file = args.pilot or args.panel
    dataset = json.loads(source_file.read_text(encoding="utf-8-sig"))
    try:
        panel = _smoke_panel(dataset) if args.pilot else dataset
    except (KeyError, ValueError) as error:
        parser.error(str(error))
    manifest = {
        "model": args.model,
        "user_model": args.user_model,
        "wake": args.wake,
        "settings": list(SETTINGS),
        "panel": panel,
    }
    if args.pilot:
        manifest.pop("wake")
        manifest.update(
            protocol="smoke",
            settings=["crm"],
            wakes=list(SMOKE_WAKES),
            trials=SMOKE_TRIALS,
        )

    planned = []
    seen = set()
    for case in panel["cases"]:
        identifier = case["case_id"]
        if identifier in seen or Path(identifier).name != identifier:
            parser.error(f"Invalid or repeated case ID: {identifier}")
        seen.add(identifier)
        if args.pilot:
            planned.extend(
                Job(case, "crm", wake, trial)
                for trial in range(SMOKE_TRIALS)
                for wake in SMOKE_WAKES
            )
        else:
            planned.extend(Job(case, memory, args.wake) for memory in SETTINGS)
    if args.dry_run:
        print(
            f"{len(panel['cases'])} cases, {len(planned)} model runs, {args.workers} workers"
        )
        return
    if not all(
        os.environ.get(key)
        for key in ("CASE_LIFECYCLE_API_BASE", "CASE_LIFECYCLE_API_KEY")
    ):
        parser.error("Set CASE_LIFECYCLE_API_BASE and CASE_LIFECYCLE_API_KEY")

    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            parser.error("Output directory belongs to a different comparison")
    else:
        _write(manifest_path, manifest)

    jobs = []
    done_count = 0
    for job in planned:
        path = args.output / job.filename
        if not path.exists():
            jobs.append((job, path))
            continue
        _validate(json.loads(path.read_text(encoding="utf-8")), job, args)
        done_count += 1

    total = done_count + len(jobs)
    print(f"{done_count}/{total} runs already saved", flush=True)
    pending_jobs = iter(jobs)
    failed = False
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        in_flight = {}

        def submit(entry):
            job, path = entry
            in_flight[pool.submit(_run, job, path, args)] = job

        for _ in range(min(args.workers, len(jobs))):
            submit(next(pending_jobs))
        while in_flight:
            completed, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in completed:
                job = in_flight.pop(future)
                try:
                    resolved = future.result()
                except (ValueError, RuntimeError) as error:
                    print(error, file=sys.stderr, flush=True)
                    failed = True
                else:
                    done_count += 1
                    print(
                        f"{done_count}/{total} {job.label} resolved={resolved}",
                        flush=True,
                    )
            if not failed:
                for _ in completed:
                    job = next(pending_jobs, None)
                    if job is not None:
                        submit(job)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
