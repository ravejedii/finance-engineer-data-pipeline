"""Write rendered rows to disk the way each source would deliver them."""

from __future__ import annotations

import csv
import json
import shutil
from datetime import timedelta
from pathlib import Path

from generator.render import A_COLUMNS, A_PAYOUT_COLUMNS, B_COLUMNS, BANK_COLUMNS, Rendered
from generator.simulate import Truth

FX_COLUMNS = ["rate_date", "base_currency", "quote_currency", "rate"]


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_all(out_dir: Path, truth: Truth, rendered: Rendered, issues: dict, meta: dict) -> dict:
    raw, truth_dir = out_dir / "raw", out_dir / "truth"
    for d in (raw, truth_dir):
        if d.exists():
            shutil.rmtree(d)
    scale = truth.scale
    files = 0

    by_day: dict = {}
    for row in rendered.a_rows:
        by_day.setdefault(row["_file_day"], []).append(row)
    payouts_by_day: dict = {}
    for row in rendered.a_payout_rows:
        payouts_by_day.setdefault(row["_file_day"], []).append(row)
    bank_by_day: dict = {}
    for row in rendered.bank_rows:
        bank_by_day.setdefault(row["_file_day"], []).append(row)
    fx_by_day: dict = {}
    for row in rendered.fx_rows:
        fx_by_day.setdefault(row["rate_date"], []).append(row)

    day = scale.start
    while day <= scale.end:
        stamp = day.isoformat()
        # Processor A delivers a file every day, header-only when nothing happened.
        rows = sorted(by_day.get(day, []), key=lambda r: r["created_utc"])
        _write(raw / "processor_a" / f"balance_transactions_{stamp}.csv", A_COLUMNS, rows)
        _write(raw / "processor_a" / f"payouts_{stamp}.csv", A_PAYOUT_COLUMNS,
               payouts_by_day.get(day, []))
        files += 2
        if day.weekday() < 5:
            _write(raw / "bank" / f"bank_statement_{stamp}.csv", BANK_COLUMNS,
                   sorted(bank_by_day.get(day, []), key=lambda r: r["bank_transaction_id"]))
            _write(raw / "fx" / f"reference_rates_{stamp}.csv", FX_COLUMNS,
                   fx_by_day.get(stamp, []))
            files += 2
        day += timedelta(days=1)

    missing = issues["processor_b_missing_batch_file"]["batch_number"]
    b_by_batch: dict = {}
    for row in rendered.b_rows:
        b_by_batch.setdefault(row["_file_batch"], []).append(row)
    last_batch = max(rendered.b_batch_days)
    for batch in range(1, last_batch + 1):
        if batch == missing:
            continue
        _write(raw / "processor_b" / f"settlement_detail_report_batch_{batch:04d}.csv",
               B_COLUMNS, b_by_batch.get(batch, []))
        files += 1

    for table, rows in rendered.app_db.items():
        columns = list(rows[0]) if rows else []
        _write(raw / "app_db" / f"{table}.csv", columns, rows)
        files += 1

    truth_dir.mkdir(parents=True, exist_ok=True)
    (truth_dir / "expected_issues.json").write_text(
        json.dumps({"meta": meta, "issues": issues}, indent=2, sort_keys=True, default=str) + "\n"
    )
    return {"files": files}
