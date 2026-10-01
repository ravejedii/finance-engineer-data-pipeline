"""BigQuery warehouse: same replace-by-file semantics as MemoryWarehouse.

Each source's new files are written in one step:
  1. load jobs put the rows, exceptions and manifest entries into scratch
     tables (load jobs are free; streaming inserts are not);
  2. one multi-statement transaction deletes those files' old rows, inserts
     the new ones, and appends the manifest. Readers see all of a batch or
     none of it.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import date

from google.cloud import bigquery

from loader.sources import SOURCES, Source, sanitize

META_FIELDS = [
    bigquery.SchemaField("_source_file", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("_source_line", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("_load_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("_loaded_at", "TIMESTAMP", mode="REQUIRED"),
]
EXCEPTION_FIELDS = [
    bigquery.SchemaField("_source_file", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("_load_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("_loaded_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("line_number", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("reason", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("raw_line", "STRING"),
]
MANIFEST_FIELDS = [
    bigquery.SchemaField("load_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("file_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("file_date", "DATE"),
    bigquery.SchemaField("checksum_sha256", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("size_bytes", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("row_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("exception_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("reason", "STRING"),
    bigquery.SchemaField("loaded_at", "TIMESTAMP", mode="REQUIRED"),
]
_SAFE_FILE = re.compile(r"^[A-Za-z0-9_./-]+$")
# Every BigQuery wait is bounded: a stuck job raises instead of hanging CI.
JOB_TIMEOUT_S = 300


def source_schema(source: Source) -> list[bigquery.SchemaField]:
    return [bigquery.SchemaField(sanitize(c), "STRING") for c in source.columns] + META_FIELDS


class BigQueryWarehouse:
    def __init__(self, project: str, dataset: str, location: str = "US") -> None:
        self.client = bigquery.Client(project=project, location=location)
        self.project, self.dataset, self.location = project, dataset, location
        # Every source's transaction also writes load_manifest and load_exceptions.
        # Concurrent transactions on one table abort each other, so scratch loads
        # (the slow part) run in parallel and the short transactions run one at a time.
        self._commit_lock = threading.Lock()
        # Fast-path manifest and exception rows from every source, written in two
        # appends by finish(). BigQuery rate-limits updates to a single table, so
        # 14 parallel sources each appending to load_manifest get rejected (429).
        self._pending_exceptions: list[dict] = []
        self._pending_manifest: list[dict] = []
        self._ensure()

    def _ref(self, table: str) -> str:
        return f"{self.project}.{self.dataset}.{table}"

    def _ensure(self) -> None:
        ds = bigquery.Dataset(f"{self.project}.{self.dataset}")
        ds.location = self.location
        self.client.create_dataset(ds, exists_ok=True, timeout=60)
        for source in SOURCES:
            table = bigquery.Table(self._ref(source.table), schema=source_schema(source))
            table.clustering_fields = ["_source_file"]
            self.client.create_table(table, exists_ok=True, timeout=60)
        exc = bigquery.Table(self._ref("load_exceptions"), schema=EXCEPTION_FIELDS)
        exc.clustering_fields = ["_source_file"]
        self.client.create_table(exc, exists_ok=True, timeout=60)
        self.client.create_table(
            bigquery.Table(self._ref("load_manifest"), schema=MANIFEST_FIELDS), exists_ok=True,
            timeout=60,
        )

    def _query(self, sql: str) -> list[bigquery.Row]:
        return list(self.client.query(sql).result(timeout=JOB_TIMEOUT_S))

    def latest_manifest(self) -> dict[str, dict]:
        rows = self._query(f"""
            select * from `{self._ref("load_manifest")}`
            where true
            qualify row_number() over (partition by file_name order by loaded_at desc) = 1
        """)
        return {r["file_name"]: dict(r.items()) for r in rows}

    def manifest_checksums(self) -> dict[str, str]:
        return {name: m["checksum_sha256"] for name, m in self.latest_manifest().items()}

    def row_counts(self, source: Source) -> dict[str, int]:
        rows = self._query(
            f"select _source_file, count(*) as n from `{self._ref(source.table)}` group by _source_file"
        )
        return {r["_source_file"]: r["n"] for r in rows}

    def exception_counts(self) -> dict[str, int]:
        rows = self._query(
            f"select _source_file, count(*) as n from `{self._ref('load_exceptions')}` "
            "group by _source_file"
        )
        return {r["_source_file"]: r["n"] for r in rows}

    def _scratch(self, rows: list[dict], schema: list[bigquery.SchemaField]) -> str:
        ref = self._ref(f"scratch_{uuid.uuid4().hex[:12]}")
        job = self.client.load_table_from_json(
            [_jsonable(r) for r in rows], ref,
            job_config=bigquery.LoadJobConfig(
                schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            ),
        )
        job.result(timeout=JOB_TIMEOUT_S)
        return ref

    def _append(self, table: str, rows: list[dict], schema: list[bigquery.SchemaField]) -> None:
        if not rows:
            return
        self.client.load_table_from_json(
            [_jsonable(r) for r in rows], self._ref(table),
            job_config=bigquery.LoadJobConfig(
                schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            ),
        ).result(timeout=JOB_TIMEOUT_S)

    def _files_present(self, source: Source, files_sql: str) -> set[str]:
        rows = self._query(f"""
            select distinct _source_file from `{self._ref(source.table)}`
            where _source_file in ({files_sql})
            union distinct
            select distinct _source_file from `{self._ref('load_exceptions')}`
            where _source_file in ({files_sql})
        """)
        return {r["_source_file"] for r in rows}

    def write_batch(self, source: Source, file_names: list[str], rows: list[dict],
                    exceptions: list[dict], manifest_rows: list[dict]) -> None:
        for name in file_names:
            if not _SAFE_FILE.match(name):
                raise ValueError(f"unsafe file name for SQL: {name!r}")
        files = ", ".join(f"'{name}'" for name in file_names)
        started = time.monotonic()
        if not self._files_present(source, files):
            # Fast path: none of these files has rows yet, so nothing needs deleting.
            # Free load jobs append directly. The manifest goes last: if a run dies
            # midway, the next run finds rows with no manifest entry, treats the
            # files as present, and replaces them through the transaction below.
            self._append(source.table, rows, source_schema(source))
            with self._commit_lock:
                self._pending_exceptions.extend(exceptions)
                self._pending_manifest.extend(manifest_rows)
            self._log(source, file_names, rows, "append", started)
            return
        self._replace(source, files, rows, exceptions, manifest_rows)
        self._log(source, file_names, rows, "replace", started)

    def finish(self) -> None:
        """Write the deferred exceptions, then the manifest (last, so an interrupted
        run leaves rows without a manifest entry and the next run replaces them)."""
        self._append("load_exceptions", self._pending_exceptions, EXCEPTION_FIELDS)
        self._append("load_manifest", self._pending_manifest, MANIFEST_FIELDS)
        self._pending_exceptions, self._pending_manifest = [], []

    @staticmethod
    def _log(source: Source, file_names: list[str], rows: list[dict], mode: str,
             started: float) -> None:
        print(f"  {source.table}: {len(file_names)} files, {len(rows):,} rows, {mode}, "
              f"{time.monotonic() - started:.1f}s", flush=True)

    def _replace(self, source: Source, files: str, rows: list[dict],
                 exceptions: list[dict], manifest_rows: list[dict]) -> None:
        """Some of these files were loaded before: delete and re-insert atomically."""
        scratch = []
        try:
            statements = [
                "begin transaction;",
                f"delete from `{self._ref(source.table)}` where _source_file in ({files});",
                f"delete from `{self._ref('load_exceptions')}` where _source_file in ({files});",
            ]
            for target, batch, schema in (
                (source.table, rows, source_schema(source)),
                ("load_exceptions", exceptions, EXCEPTION_FIELDS),
                ("load_manifest", manifest_rows, MANIFEST_FIELDS),
            ):
                if not batch:
                    continue
                ref = self._scratch(batch, schema)
                scratch.append(ref)
                cols = ", ".join(f.name for f in schema)
                statements.append(
                    f"insert into `{self._ref(target)}` ({cols}) select {cols} from `{ref}`;"
                )
            statements.append("commit transaction;")
            with self._commit_lock:
                self.client.query("\n".join(statements)).result(timeout=JOB_TIMEOUT_S)
        finally:
            for ref in scratch:
                self.client.delete_table(ref, not_found_ok=True)


def _jsonable(row: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}
