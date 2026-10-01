"""Loader contract, written before the loader.

The loader's job is narrow: land every raw row exactly once, as text, with
lineage columns; record every file in a manifest; send rows that can't be
parsed into an exceptions table with a reason. It never casts, dedupes or
fixes data — that is staging's job.

These tests run against MemoryWarehouse, so they need no credentials. The
BigQuery implementation is exercised end to end by the CI build job.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from generator.__main__ import generate
from loader.core import load, sanitize
from loader.memory import MemoryWarehouse


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("gen")
    generate("small", 42, out)
    return out


@pytest.fixture
def raw(generated, tmp_path) -> Path:
    """A private, mutable copy of the generated raw files."""
    dst = tmp_path / "raw"
    shutil.copytree(generated / "raw", dst)
    return dst


def data_lines(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as fh:
        return sum(1 for _ in csv.reader(fh)) - 1


def all_files(raw: Path) -> list[Path]:
    return sorted(p for p in raw.rglob("*.csv"))


# ---------------------------------------------------------------- exactly once


def test_every_data_line_lands_in_raw_or_exceptions(raw):
    wh = MemoryWarehouse()
    load(raw, wh)
    expected = sum(data_lines(p) for p in all_files(raw))
    landed = sum(len(rows) for rows in wh.tables.values()) + len(wh.exceptions)
    assert landed == expected


def test_rerun_is_a_no_op(raw):
    wh = MemoryWarehouse()
    load(raw, wh)
    before = (wh.snapshot(), len(wh.manifest))
    results = load(raw, wh)
    assert {r.status for r in results} == {"skipped"}
    assert (wh.snapshot(), len(wh.manifest)) == before


def test_changed_file_replaces_only_its_own_rows(raw):
    wh = MemoryWarehouse()
    load(raw, wh)
    target = raw / "processor_a" / "balance_transactions_2025-07-10.csv"
    other_before = [r for r in wh.tables["processor_a_balance_transactions"]
                    if r["_source_file"] != "processor_a/" + target.name]
    lines = target.read_text().splitlines()
    target.write_text("\n".join(lines + [lines[-1]]) + "\n")  # the processor re-sends a longer file

    results = load(raw, wh)
    changed = [r for r in results if r.status == "replaced"]
    assert [r.file_name for r in changed] == ["processor_a/" + target.name]
    rows = [r for r in wh.tables["processor_a_balance_transactions"]
            if r["_source_file"] == "processor_a/" + target.name]
    assert len(rows) == len(lines)  # old rows gone, new file's rows present once
    other_after = [r for r in wh.tables["processor_a_balance_transactions"]
                   if r["_source_file"] != "processor_a/" + target.name]
    assert other_after == other_before


# ---------------------------------------------------------------- manifest


def test_manifest_records_every_file_with_checksum_and_counts(raw):
    wh = MemoryWarehouse()
    load(raw, wh)
    by_name = {m["file_name"]: m for m in wh.manifest}
    files = all_files(raw)
    assert len(by_name) == len(files)
    for path in files:
        m = by_name[str(path.relative_to(raw))]
        assert m["checksum_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert m["row_count"] + m["exception_count"] == data_lines(path)
        assert m["status"] == "loaded"
        assert m["load_id"] and m["loaded_at"]


# ---------------------------------------------------------------- exceptions


def test_short_row_goes_to_exceptions_with_reason(raw):
    target = raw / "bank" / "bank_statement_2025-07-10.csv"
    lines = target.read_text().splitlines()
    lines.insert(2, "BT000000000001,KILN-USD-0001,2025-07-10")  # 3 of 9 fields
    target.write_text("\n".join(lines) + "\n")

    wh = MemoryWarehouse()
    load(raw, wh)
    exc = [e for e in wh.exceptions if e["_source_file"] == "bank/" + target.name]
    assert len(exc) == 1
    assert exc[0]["line_number"] == 3
    assert "expected 9 fields, got 3" in exc[0]["reason"]
    assert exc[0]["raw_line"] == "BT000000000001,KILN-USD-0001,2025-07-10"
    rows = [r for r in wh.tables["bank_statements"] if r["_source_file"] == "bank/" + target.name]
    assert len(rows) == len(lines) - 2  # header and the bad line excluded


def test_header_drift_rejects_the_whole_file(raw):
    target = raw / "fx" / "reference_rates_2025-07-10.csv"
    text = target.read_text().replace("quote_currency", "quote_ccy", 1)
    target.write_text(text)

    wh = MemoryWarehouse()
    results = load(raw, wh)
    result = next(r for r in results if r.file_name == "fx/" + target.name)
    assert result.status == "rejected"
    assert not [r for r in wh.tables["fx_reference_rates"]
                if r["_source_file"] == "fx/" + target.name]
    m = next(m for m in wh.manifest if m["file_name"] == "fx/" + target.name)
    assert m["status"] == "rejected"
    assert "quote_ccy" in m["reason"]


def test_generated_truncated_rows_land_in_exceptions(raw, generated):
    issues = json.loads((generated / "truth" / "expected_issues.json").read_text())["issues"]
    expected = issues["processor_a_truncated_rows"]
    wh = MemoryWarehouse()
    load(raw, wh)
    exc = [e for e in wh.exceptions if e["source"] == "processor_a_balance_transactions"]
    assert len(exc) == expected["count"]
    for e in exc:
        assert "expected 14 fields" in e["reason"]


# ---------------------------------------------------------------- raw stays raw


def test_values_are_landed_verbatim_as_text(raw):
    wh = MemoryWarehouse()
    load(raw, wh)
    a = wh.tables["processor_a_balance_transactions"]
    assert any(r["gross"] == "N/A" for r in a), "malformed values must survive to staging"
    b = wh.tables["processor_b_settlement_details"]
    assert any("," in r["net_credit_nc"] for r in b)
    assert all(isinstance(v, str) for r in a for k, v in r.items() if not k.startswith("_"))


def test_column_names_are_sanitized_for_bigquery():
    assert sanitize("Gross Debit (GC)") == "gross_debit_gc"
    assert sanitize("payment_metadata[kiln_order_id]") == "payment_metadata_kiln_order_id"
    assert sanitize("Psp Reference") == "psp_reference"
    assert sanitize("TimeZone") == "timezone"


def test_lineage_columns_on_every_row(raw):
    wh = MemoryWarehouse()
    load(raw, wh)
    for rows in wh.tables.values():
        for r in rows:
            assert r["_source_file"] and r["_load_id"] and r["_loaded_at"]
            assert isinstance(r["_source_line"], int) and r["_source_line"] >= 2


# ---------------------------------------------------------------- backfill


def test_backfill_range_equals_loading_day_by_day(raw):
    one_shot, daily = MemoryWarehouse(), MemoryWarehouse()
    load(raw, one_shot, start=date(2025, 7, 1), end=date(2025, 7, 7))
    for day in range(1, 8):
        d = date(2025, 7, day)
        load(raw, daily, start=d, end=d)
    assert one_shot.snapshot(ignore_load_ids=True) == daily.snapshot(ignore_load_ids=True)


def test_backfill_only_touches_files_in_range(raw):
    wh = MemoryWarehouse()
    load(raw, wh, start=date(2025, 7, 1), end=date(2025, 7, 7))
    dates = {m["file_date"] for m in wh.manifest}
    assert dates and all(date(2025, 7, 1) <= d <= date(2025, 7, 7) for d in dates)


# ---------------------------------------------------------------- verify


def test_verify_passes_after_a_clean_load(raw):
    from loader.core import verify
    wh = MemoryWarehouse()
    load(raw, wh)
    assert verify(wh) == []


def test_verify_catches_rows_that_went_missing(raw):
    from loader.core import verify
    wh = MemoryWarehouse()
    load(raw, wh)
    victim = wh.tables["bank_statements"].pop()
    problems = verify(wh)
    assert len(problems) == 1
    assert victim["_source_file"] in problems[0]


def test_bigquery_schemas_avoid_reserved_column_prefixes():
    """BigQuery rejects field names starting with these (case-insensitive) prefixes.
    CI found this the hard way with `_source_file`; this keeps it from coming back."""
    from loader.bigquery import EXCEPTION_FIELDS, MANIFEST_FIELDS, source_schema
    from loader.sources import SOURCES

    reserved = ("_PARTITION", "_TABLE_", "_FILE_", "_ROW_TIMESTAMP", "__ROOT__",
                "_COLIDENTIFIER", "__DREMEL_PK_MERGED_STRUCT_")
    schemas = [source_schema(s) for s in SOURCES] + [EXCEPTION_FIELDS, MANIFEST_FIELDS]
    for schema in schemas:
        for field in schema:
            assert not field.name.upper().startswith(reserved), field.name


def test_parallel_load_matches_sequential(raw):
    sequential, parallel = MemoryWarehouse(), MemoryWarehouse()
    load(raw, sequential)
    load(raw, parallel, workers=8)
    assert sequential.snapshot(ignore_load_ids=True) == parallel.snapshot(ignore_load_ids=True)
