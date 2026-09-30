"""Load raw files into BigQuery.

    uv run python -m loader load                          # everything under data/raw
    uv run python -m loader load --start 2025-07-01 --end 2025-07-31   # backfill a range
    uv run python -m loader verify                        # warehouse counts == manifest

Reads GCP_PROJECT_ID, DBT_DATASET (default kiln_dev) and BQ_LOCATION (default US).
Raw tables land in the <DBT_DATASET>_raw dataset.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from loader.core import load, verify


def _warehouse():
    from loader.bigquery import BigQueryWarehouse

    project = os.environ.get("GCP_PROJECT_ID")
    if not project:
        sys.exit("GCP_PROJECT_ID is not set")
    dataset = os.environ.get("DBT_DATASET", "kiln_dev") + "_raw"
    return BigQueryWarehouse(project, dataset, os.environ.get("BQ_LOCATION", "US"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_load = sub.add_parser("load", help="load new or changed files")
    p_load.add_argument("--raw", type=Path, default=Path("data/raw"))
    p_load.add_argument("--start", type=date.fromisoformat)
    p_load.add_argument("--end", type=date.fromisoformat)
    p_load.add_argument("--expect-noop", action="store_true",
                        help="fail if any file is loaded (proves re-runs are idempotent)")
    sub.add_parser("verify", help="check warehouse row counts against the manifest")
    args = parser.parse_args()

    warehouse = _warehouse()
    if args.command == "load":
        results = load(args.raw, warehouse, args.start, args.end)
        statuses = Counter(r.status for r in results)
        rows = sum(r.row_count for r in results)
        exceptions = sum(r.exception_count for r in results)
        print(f"files: {dict(sorted(statuses.items()))}  rows: {rows:,}  exceptions: {exceptions}")
        for r in results:
            if r.status == "rejected":
                print(f"REJECTED {r.file_name}")
        if args.expect_noop and set(statuses) - {"skipped"}:
            sys.exit("expected a no-op re-run, but files were loaded")
    else:
        problems = verify(warehouse)
        for p in problems:
            print(p)
        if problems:
            sys.exit(f"{len(problems)} file(s) don't match the manifest")
        print("verify: every loaded file matches its manifest entry")


if __name__ == "__main__":
    main()
