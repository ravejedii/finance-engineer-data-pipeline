"""After a failed dbt run, show what each failed BigQuery job was doing.

Reads transform/target/run_results.json, finds the BigQuery job ID in each
error, and prints the job's timeline: when it was created, when it started
running (if ever), when it ended, and its final state. A job that was created
but never started was queued, not slow.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from google.cloud import bigquery

RESULTS = Path(__file__).resolve().parents[1] / "transform" / "target" / "run_results.json"


def main() -> None:
    if not RESULTS.exists():
        print("no run_results.json; nothing to diagnose")
        return
    results = json.loads(RESULTS.read_text())["results"]
    failed = [r for r in results if r["status"] in ("error", "fail")]
    client = bigquery.Client(project=os.environ["GCP_PROJECT_ID"],
                             location=os.environ.get("BQ_LOCATION", "US"))
    for r in failed:
        match = re.search(r"Job ID: ([\w-]+)", r.get("message") or "")
        print(f"{r['unique_id']}: {r['status']} after {r['execution_time']:.0f}s")
        if not match:
            continue
        try:
            job = client.get_job(match.group(1))
        except Exception as exc:  # diagnostics must never mask the real failure
            print(f"  could not fetch job: {exc}")
            continue
        queued = (job.started - job.created).total_seconds() if job.started else None
        ran = (job.ended - job.started).total_seconds() if job.started and job.ended else None
        print(f"  job {job.job_id}: state={job.state} created={job.created} "
              f"started={job.started} ended={job.ended}")
        print(f"  queued_s={queued} running_s={ran} slot_ms={job.slot_millis} "
              f"error={job.error_result}")


if __name__ == "__main__":
    main()
