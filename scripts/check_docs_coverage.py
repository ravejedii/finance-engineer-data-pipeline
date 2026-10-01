"""Fail if any mart model, or any column a mart model actually has, lacks a description.

Run after `dbt docs generate`: it compares the warehouse columns in
target/catalog.json against the descriptions in target/manifest.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "transform" / "target"


def main() -> None:
    manifest = json.loads((TARGET / "manifest.json").read_text())
    catalog = json.loads((TARGET / "catalog.json").read_text())
    problems, checked = [], 0
    for node_id, node in manifest["nodes"].items():
        if node["resource_type"] != "model" or "/marts/" not in node["original_file_path"]:
            continue
        checked += 1
        if not node.get("description", "").strip():
            problems.append(f"{node['name']}: model has no description")
        documented = {name for name, col in node["columns"].items()
                      if col.get("description", "").strip()}
        actual = set(catalog["nodes"].get(node_id, {}).get("columns", {}))
        for column in sorted(c.lower() for c in actual):
            if column not in documented:
                problems.append(f"{node['name']}.{column}: no description")
    for p in problems:
        print(p)
    print(f"docs coverage: {checked} mart models checked, {len(problems)} gaps")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
