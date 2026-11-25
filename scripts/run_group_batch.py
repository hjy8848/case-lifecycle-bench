"""Run many (template, group, seed, wake) jobs through one gateway with a fixed number of parallel workers.

    python scripts/run_group_batch.py --output runs/ledger-dev --model dashscope/qwen3.7-plus \
        --seeds 400 401 402 --groups E0 E1 E2 E3 A1 A2 --workers 8

Needs CASE_LIFECYCLE_API_BASE and CASE_LIFECYCLE_API_KEY (dot-source scripts/litellm.ps1). Result files are
kept per group in <output>/<group>/ and reused, so the script can be stopped and started again. Jobs are
ordered seed by seed so that a partial run is balanced across templates and groups. A failed job is tried
again up to three times after a pause.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from case_lifecycle_bench.runnable_templates import ALL  # noqa: E402
from case_lifecycle_bench.template_run import GROUPS, run_one  # noqa: E402

LOCK = threading.Lock()


def log(output, text):
    with LOCK, open(output / "progress.log", "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def job(output, name, group, seed, wake, args, base):
    directory = output / group
    directory.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(4):
        try:
            run_one(
                name, seed, wake, args.model, args.user_model or args.model, base, directory,
                enable_thinking=False, llm_customer=True, group=group,
            )
            log(output, f"ok {group} {name} {seed} {wake}")
            return None
        except Exception as error:  # noqa: BLE001 - a failed job must not stop the batch
            last = f"{type(error).__name__}: {error}"
            log(output, f"retry {attempt} {group} {name} {seed} {wake}: {last}")
            time.sleep(60 * (attempt + 1))
    return {"group": group, "template": name, "seed": seed, "wake": wake, "error": last}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--user-model")
    parser.add_argument("--seeds", type=int, nargs="+", default=[400, 401, 402])
    parser.add_argument("--groups", nargs="+", default=list(GROUPS), choices=GROUPS)
    parser.add_argument("--wakes", nargs="+", default=["poll", "push"])
    parser.add_argument("--templates", nargs="+", default=list(ALL))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    base = os.environ["CASE_LIFECYCLE_API_BASE"]
    args.output.mkdir(parents=True, exist_ok=True)
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    (args.output / "PROVENANCE.json").write_text(
        json.dumps({"commit": commit, "tree_clean": not dirty, "model": args.model, "groups": args.groups,
                    "seeds": args.seeds, "customer": "llm", "backend": "gateway", "workers": args.workers}, indent=1),
        encoding="utf-8",
    )
    jobs = [
        (name, group, seed, wake)
        for seed in args.seeds for name in args.templates for wake in args.wakes for group in args.groups
    ]
    done = {
        (g, f.name) for g in args.groups for f in (args.output / g).glob("*--*--*--*.json")
    } if (args.output).exists() else set()
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(job, args.output, name, group, seed, wake, args, base) for name, group, seed, wake in jobs]
        for future in as_completed(futures):
            failure = future.result()
            if failure:
                failures.append(failure)
    (args.output / "failures.json").write_text(json.dumps(failures, indent=1), encoding="utf-8")
    log(args.output, f"finished, {len(failures)} failed jobs")
    print(json.dumps({"jobs": len(jobs), "failed": len(failures)}))


if __name__ == "__main__":
    main()
