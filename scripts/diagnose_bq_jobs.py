"""Log failed dbt jobs and identify completed, transient BigQuery failures."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from google.cloud import bigquery

RESULTS = Path(__file__).resolve().parents[1] / "transform" / "target" / "run_results.json"
TRANSIENT_REASONS = {"backendError", "internalError", "rateLimitExceeded", "jobRateLimitExceeded"}


def retryable_job(job) -> bool:
    """Never resubmit an unfinished job or a user cancellation."""
    if job.state != "DONE" or not job.error_result:
        return False
    error = job.error_result
    return error.get("reason") in TRANSIENT_REASONS or (
        error.get("reason") == "stopped"
        and "job timed out" in error.get("message", "").lower()
    )


def diagnose(results: list[dict], output_dir: Path) -> set[str]:
    """Return only node IDs whose jobs are confirmed safe to retry.

    Missing metrics stay unknown. Diagnostic/API errors must preserve the
    original failure rather than accidentally treating it as retryable.
    """
    failed = [r for r in results if r["status"] in {"error", "fail", "runtime error", "partial success"}]
    retryable = set()
    client = None
    for result in failed:
        node = result["unique_id"]
        print(f"{node}: {result['status']} after {result.get('execution_time', 0):.1f}s", flush=True)
        match = re.search(r"Job ID: ([\w-]+)", result.get("message") or "")
        # An assertion failure is never a transient warehouse error.
        if result["status"] != "error" or not match:
            continue
        try:
            if client is None:
                client = bigquery.Client(project=os.environ["GCP_PROJECT_ID"],
                                         location=os.environ.get("BQ_LOCATION", "US"))
            job = client.get_job(match.group(1), timeout=10, retry=None)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"{job.job_id}.json").write_text(json.dumps(job.to_api_repr(), indent=2))
            queued = (job.started - job.created).total_seconds() if job.started else None
            ran = (job.ended - job.started).total_seconds() if job.started and job.ended else None
            print(f"  job {job.project}:{job.location}.{job.job_id}: state={job.state} "
                  f"queued_s={queued} running_s={ran} slot_ms={job.slot_millis} "
                  f"error={job.error_result}", flush=True)
            if retryable_job(job):
                retryable.add(node)
        except Exception as exc:
            print(f"  could not diagnose job: {exc}", flush=True)
    return retryable


def main() -> None:
    if not RESULTS.exists():
        print("no run_results.json; nothing to diagnose")
        return
    diagnose(json.loads(RESULTS.read_text())["results"], RESULTS.parent / "job_diagnostics")


if __name__ == "__main__":
    main()
