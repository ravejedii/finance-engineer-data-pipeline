"""Idempotent file loader.

Unit of work: the file. Each file is fingerprinted (SHA-256). A file whose
fingerprint is already in the manifest is skipped. A known file name with a
new fingerprint replaces that file's rows as one unit. Values land verbatim
as text; the only rows kept out of raw are ones that cannot be split into
the contracted columns, and those go to the exceptions table with a reason.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from loader.sources import SOURCES, Source, sanitize

__all__ = ["load", "verify", "sanitize", "FileResult", "Warehouse"]


class Warehouse(Protocol):
    def manifest_checksums(self) -> dict[str, str]: ...

    def write_batch(self, source: Source, file_names: list[str], rows: list[dict],
                    exceptions: list[dict], manifest_rows: list[dict]) -> None: ...

    def latest_manifest(self) -> dict[str, dict]: ...

    def row_counts(self, source: Source) -> dict[str, int]: ...

    def exception_counts(self) -> dict[str, int]: ...

    def finish(self) -> None:
        """Flush anything write_batch deferred. Called once, after every source."""


@dataclass(frozen=True)
class FileResult:
    file_name: str
    source: str
    status: str  # loaded | replaced | skipped | rejected
    row_count: int = 0
    exception_count: int = 0


def load(raw_dir: Path, warehouse: Warehouse, start: date | None = None,
         end: date | None = None, run_id: str | None = None,
         workers: int = 1) -> list[FileResult]:
    """Load every file under raw_dir, or only dated files within [start, end].

    Sources are independent (each writes its own table in its own transaction),
    so with workers > 1 their warehouse writes run in parallel.
    """
    raw_dir = Path(raw_dir)
    load_id = run_id or uuid.uuid4().hex
    loaded_at = datetime.now(timezone.utc).isoformat()
    ranged = start is not None or end is not None
    known = warehouse.manifest_checksums()
    results: list[FileResult] = []
    writes: list[tuple] = []

    for source in SOURCES:
        batch_files, batch_rows, batch_exc, batch_manifest = [], [], [], []
        for path in sorted(raw_dir.glob(source.pattern)):
            file_name = path.relative_to(raw_dir).as_posix()
            data = path.read_bytes()
            checksum = hashlib.sha256(data).hexdigest()
            text, decode_error = _decode(data)
            file_date = source.file_date(path, text) if text is not None else None

            if ranged and (file_date is None
                           or (start and file_date < start) or (end and file_date > end)):
                continue
            if known.get(file_name) == checksum:
                results.append(FileResult(file_name, source.table, "skipped"))
                continue

            meta = {"_source_file": file_name, "_load_id": load_id, "_loaded_at": loaded_at}
            if decode_error:
                rows, exceptions, reason = [], [], decode_error
            else:
                rows, exceptions, reason = _parse(source, text, meta)
            if reason:
                exceptions = [{**meta, "source": source.table, "line_number": 1,
                               "reason": reason, "raw_line": ""}]
            status = "rejected" if reason else ("replaced" if file_name in known else "loaded")

            batch_files.append(file_name)
            batch_rows += rows
            batch_exc += exceptions
            batch_manifest.append({
                "load_id": load_id, "file_name": file_name, "source": source.table,
                "file_date": file_date, "checksum_sha256": checksum, "size_bytes": len(data),
                "row_count": len(rows), "exception_count": 0 if reason else len(exceptions),
                "status": "rejected" if reason else "loaded", "reason": reason or "",
                "loaded_at": loaded_at,
            })
            results.append(FileResult(file_name, source.table, status, len(rows),
                                      0 if reason else len(exceptions)))

        if batch_files:
            writes.append((source, batch_files, batch_rows, batch_exc, batch_manifest))

    if workers > 1 and len(writes) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in [pool.submit(warehouse.write_batch, *w) for w in writes]:
                future.result()  # re-raise the first failure
    else:
        for w in writes:
            warehouse.write_batch(*w)
    warehouse.finish()
    return results


def _decode(data: bytes) -> tuple[str | None, str | None]:
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, f"file is not valid UTF-8: {exc}"


def _parse(source: Source, text: str, meta: dict) -> tuple[list[dict], list[dict], str | None]:
    lines = text.splitlines()
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    expected = list(source.columns)
    if header != expected:
        got = header or []
        missing = [c for c in expected if c not in got]
        unexpected = [c for c in got if c not in expected]
        return [], [], (f"header mismatch; missing={missing} unexpected={unexpected}"
                        if missing or unexpected else "header columns out of order")

    names = [sanitize(c) for c in expected]
    rows, exceptions = [], []
    for fields in reader:
        line_number = reader.line_num
        if len(fields) != len(names):
            exceptions.append({
                **meta, "source": source.table, "line_number": line_number,
                "reason": f"expected {len(names)} fields, got {len(fields)}",
                "raw_line": lines[line_number - 1] if line_number <= len(lines) else "",
            })
            continue
        rows.append({**dict(zip(names, fields)), **meta, "_source_line": line_number})
    return rows, exceptions, None


def verify(warehouse: Warehouse) -> list[str]:
    """Every loaded file's rows and exceptions in the warehouse match its manifest entry."""
    manifest = warehouse.latest_manifest()
    exc = warehouse.exception_counts()
    problems = []
    for source in SOURCES:
        counts = warehouse.row_counts(source)
        for file_name, m in sorted(manifest.items()):
            if m["source"] != source.table or m["status"] != "loaded":
                continue
            rows, errs = counts.get(file_name, 0), exc.get(file_name, 0)
            if rows != m["row_count"] or errs != m["exception_count"]:
                problems.append(
                    f"{file_name}: warehouse has {rows} rows / {errs} exceptions, "
                    f"manifest says {m['row_count']} / {m['exception_count']}"
                )
        for file_name in sorted(set(counts) - set(manifest)):
            problems.append(f"{file_name}: rows in {source.table} with no manifest entry")
    return problems
