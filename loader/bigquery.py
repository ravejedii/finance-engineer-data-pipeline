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


def source_schema(source: Source) -> list[bigquery.SchemaField]:
    return [bigquery.SchemaField(sanitize(c), "STRING") for c in source.columns] + META_FIELDS


class BigQueryWarehouse:
    def __init__(self, project: str, dataset: str, location: str = "US") -> None:
        self.client = bigquery.Client(project=project, location=location)
        self.project, self.dataset, self.location = project, dataset, location
        self._ensure()

    def _ref(self, table: str) -> str:
        return f"{self.project}.{self.dataset}.{table}"

    def _ensure(self) -> None:
        ds = bigquery.Dataset(f"{self.project}.{self.dataset}")
        ds.location = self.location
        self.client.create_dataset(ds, exists_ok=True)
        for source in SOURCES:
            table = bigquery.Table(self._ref(source.table), schema=source_schema(source))
            table.clustering_fields = ["_source_file"]
            self.client.create_table(table, exists_ok=True)
        exc = bigquery.Table(self._ref("load_exceptions"), schema=EXCEPTION_FIELDS)
        exc.clustering_fields = ["_source_file"]
        self.client.create_table(exc, exists_ok=True)
        self.client.create_table(
            bigquery.Table(self._ref("load_manifest"), schema=MANIFEST_FIELDS), exists_ok=True
        )

    def _query(self, sql: str) -> list[bigquery.Row]:
        return list(self.client.query(sql).result())

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
        job.result()
        return ref

    def write_batch(self, source: Source, file_names: list[str], rows: list[dict],
                    exceptions: list[dict], manifest_rows: list[dict]) -> None:
        for name in file_names:
            if not _SAFE_FILE.match(name):
                raise ValueError(f"unsafe file name for SQL: {name!r}")
        files = ", ".join(f"'{name}'" for name in file_names)
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
            self.client.query("\n".join(statements)).result()
        finally:
            for ref in scratch:
                self.client.delete_table(ref, not_found_ok=True)


def _jsonable(row: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}
