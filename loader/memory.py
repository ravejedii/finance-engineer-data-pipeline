"""In-memory warehouse with the same replace-by-file semantics as BigQuery.
Used by tests so the loader's logic is checked without credentials."""

from __future__ import annotations

import json
import threading
from collections import defaultdict

from loader.sources import Source

_RUN_KEYS = {"_load_id", "_loaded_at", "load_id", "loaded_at"}


class MemoryWarehouse:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = defaultdict(list)
        self.exceptions: list[dict] = []
        self.manifest: list[dict] = []
        self._lock = threading.Lock()

    def manifest_checksums(self) -> dict[str, str]:
        return {m["file_name"]: m["checksum_sha256"] for m in self.manifest}

    def write_batch(self, source: Source, file_names: list[str], rows: list[dict],
                    exceptions: list[dict], manifest_rows: list[dict]) -> None:
        with self._lock:
            self._write(source, set(file_names), rows, exceptions, manifest_rows)

    def _write(self, source: Source, files: set[str], rows: list[dict],
               exceptions: list[dict], manifest_rows: list[dict]) -> None:
        table = self.tables[source.table]
        table[:] = [r for r in table if r["_source_file"] not in files]
        table.extend(rows)
        self.exceptions[:] = [e for e in self.exceptions if e["_source_file"] not in files]
        self.exceptions.extend(exceptions)
        self.manifest.extend(manifest_rows)

    def finish(self) -> None:
        """Nothing is deferred in memory."""

    def latest_manifest(self) -> dict[str, dict]:
        return {m["file_name"]: m for m in self.manifest}

    def row_counts(self, source: Source) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for r in self.tables[source.table]:
            counts[r["_source_file"]] += 1
        return dict(counts)

    def exception_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for e in self.exceptions:
            counts[e["_source_file"]] += 1
        return dict(counts)

    def snapshot(self, ignore_load_ids: bool = False) -> str:
        def clean(r: dict) -> dict:
            return {k: v for k, v in r.items() if not (ignore_load_ids and k in _RUN_KEYS)}

        latest = {m["file_name"]: m for m in self.manifest}
        state = {
            "tables": {t: sorted(json.dumps(clean(r), sort_keys=True, default=str) for r in rows)
                       for t, rows in self.tables.items() if rows},
            "exceptions": sorted(json.dumps(clean(e), sort_keys=True, default=str)
                                 for e in self.exceptions),
            "manifest": sorted(json.dumps(clean(m), sort_keys=True, default=str)
                               for m in latest.values()),
        }
        return json.dumps(state, sort_keys=True)
