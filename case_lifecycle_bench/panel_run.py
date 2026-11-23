"""Run the three memory settings on every case in a fixed comparison panel."""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

SETTINGS = ("crm", "full-history", "ledger")


def _write(path, result):
    partial = path.with_suffix(".partial")
    partial.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    partial.replace(path)


def _validate(result, case, memory, args):
    expected = (case["case_id"], memory, args.wake, args.model, args.user_model)
    meta = result.get("meta", {})
    actual = (
        result.get("case_id"),
        result.get("memory"),
        result.get("wake"),
        meta.get("agent_model"),
        meta.get("user_model"),
    )
    if actual != expected or "run" not in result or "evaluation" not in result:
        raise ValueError(
            f"Unexpected model-run metadata for {case['case_id']} / {memory}"
        )


def _run(case, memory, path, args):
    command = [
        sys.executable,
        "-m",
        "case_lifecycle_bench",
        "--case",
        case["template"],
        "--retail-order",
        case["order_id"],
        "--memory",
        memory,
        "--wake",
        args.wake,
        "--model",
        args.model,
        "--user-model",
        args.user_model,
        "--trace",
    ]
    completed = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace"
    )
    if completed.returncode:
        error = completed.stderr.strip().splitlines()[-1:] or ["No diagnostic output"]
        raise RuntimeError(f"{case['case_id']} / {memory}: {error[0]}")
    result = json.loads(completed.stdout)
    _validate(result, case, memory, args)
    _write(path, result)
    return result["evaluation"]["metrics"]["case_resolution"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--user-model")
    parser.add_argument("--wake", choices=("poll", "push"), default="poll")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if not all(
        os.environ.get(key)
        for key in ("CASE_LIFECYCLE_API_BASE", "CASE_LIFECYCLE_API_KEY")
    ):
        parser.error("Set CASE_LIFECYCLE_API_BASE and CASE_LIFECYCLE_API_KEY")
    args.user_model = args.user_model or args.model
    panel = json.loads(args.panel.read_text(encoding="utf-8-sig"))
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "model": args.model,
        "user_model": args.user_model,
        "wake": args.wake,
        "settings": list(SETTINGS),
        "panel": panel,
    }
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            parser.error("Output directory belongs to a different comparison")
    else:
        _write(manifest_path, manifest)

    jobs = []
    done_count = 0
    seen = set()
    for case in panel["cases"]:
        identifier = case["case_id"]
        if identifier in seen or Path(identifier).name != identifier:
            parser.error(f"Invalid or repeated case ID: {identifier}")
        seen.add(identifier)
        for memory in SETTINGS:
            path = args.output / f"{identifier}--{memory}.json"
            if not path.exists():
                jobs.append((case, memory, path))
                continue
            _validate(json.loads(path.read_text(encoding="utf-8")), case, memory, args)
            done_count += 1

    total = done_count + len(jobs)
    print(f"{done_count}/{total} runs already saved", flush=True)
    pending_jobs = iter(jobs)
    failed = False
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        in_flight = {}

        def submit(job):
            case, memory, path = job
            in_flight[pool.submit(_run, case, memory, path, args)] = job

        for _ in range(min(args.workers, len(jobs))):
            submit(next(pending_jobs))
        while in_flight:
            completed, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in completed:
                case, memory, _ = in_flight.pop(future)
                try:
                    resolved = future.result()
                except (ValueError, RuntimeError) as error:
                    print(error, file=sys.stderr, flush=True)
                    failed = True
                else:
                    done_count += 1
                    print(
                        f"{done_count}/{total} {case['case_id']} {memory} resolved={resolved}",
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
