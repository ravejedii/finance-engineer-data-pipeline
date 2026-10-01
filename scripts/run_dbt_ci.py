"""Run the complete dbt build, recovering only confirmed transient job failures.

Each failed node gets two retries, independent of later downstream failures.
The entire build/retry sequence has a ten-minute deadline. dbt retry preserves
the dependency graph and executes only unsuccessful nodes from the last pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from scripts.diagnose_bq_jobs import diagnose

TRANSFORM = Path(__file__).resolve().parents[1] / "transform"
MAX_RETRIES_PER_NODE = 2
BUILD_TIMEOUT_S = 600
SUCCESS_STATUSES = {"success", "pass", "warn", "no-op", "reused"}


def run_build(project_dir: Path = TRANSFORM) -> int:
    results_path = project_dir / "target" / "run_results.json"
    history = project_dir / "target" / "ci_attempts"
    # A local re-run must not read or upload results from a previous invocation.
    if history.exists():
        shutil.rmtree(history)
    failures = Counter()
    deadline = time.monotonic() + BUILD_TIMEOUT_S
    command = "build"
    attempt = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print("::error::dbt build exhausted its ten-minute recovery budget", flush=True)
            return 1
        # retry reads the previous results at startup; keep them in place but
        # reject stale results if the new process fails before writing its own.
        previous = results_path.read_bytes() if results_path.exists() else None
        attempt += 1
        print(f"::group::dbt {command}, pass {attempt}", flush=True)
        try:
            completed = subprocess.run(["dbt", command], cwd=project_dir, timeout=remaining)
        except subprocess.TimeoutExpired:
            print("::error::dbt build exhausted its ten-minute recovery budget", flush=True)
            return 1
        finally:
            print("::endgroup::", flush=True)
        if not results_path.exists() or results_path.read_bytes() == previous:
            print("::error::dbt did not produce fresh run results; refusing to retry", flush=True)
            return 1
        output_dir = history / f"{attempt:02d}_{command}"
        output_dir.mkdir(parents=True)
        shutil.copy2(results_path, output_dir / "run_results.json")
        try:
            results = json.loads(results_path.read_text())["results"]
            unsuccessful = [r for r in results if r["status"] not in SUCCESS_STATUSES]
        except (ValueError, KeyError, TypeError) as exc:
            print(f"::error::Invalid dbt run results: {exc}", flush=True)
            return 1
        if completed.returncode == 0 and not unsuccessful and results:
            print(f"dbt build passed after {attempt} pass(es)", flush=True)
            return 0
        failed = [r for r in unsuccessful if r["status"] != "skipped"]
        retryable = diagnose(results, output_dir)
        if not failed or any(r["unique_id"] not in retryable for r in failed):
            print("::error::dbt has a non-transient or unconfirmed failure; stopping", flush=True)
            return 1
        for result in failed:
            failures[result["unique_id"]] += 1
        exhausted = [r["unique_id"] for r in failed
                     if failures[r["unique_id"]] > MAX_RETRIES_PER_NODE]
        if exhausted:
            print(f"::error::Retry limit reached for {', '.join(exhausted)}", flush=True)
            return 1
        for result in failed:
            node = result["unique_id"]
            print(f"::warning::{node}: retry {failures[node]}/{MAX_RETRIES_PER_NODE} "
                  "after a completed transient BigQuery failure", flush=True)
        # A short delay also gives table-update rate limits time to recover.
        time.sleep(min(5, max(0, deadline - time.monotonic())))
        command = "retry"


if __name__ == "__main__":
    raise SystemExit(run_build())
