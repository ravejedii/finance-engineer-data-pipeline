"""Configure a local Metabase OSS for Kiln: admin user, BigQuery connection,
and the two dashboards in metabase/dashboards.py. Idempotent: re-running
replaces the Kiln dashboards and their questions instead of duplicating them.

    uv run python -m metabase.setup

Reads (nothing is ever written to the repo):
    MB_URL               default http://localhost:3000
    MB_ADMIN_EMAIL       admin login; created on first run
    MB_ADMIN_PASSWORD    prompted for if unset (never pass it on the command line)
    KILN_METABASE_KEY    path to the metabase-reader service-account key,
                         default ~/.kiln/metabase-reader.json
    GCP_PROJECT_ID       default finance-engineer-data-pipeline
    KILN_DATASET         default kiln (dashboards read <KILN_DATASET>_marts)
"""

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from metabase.dashboards import DASHBOARDS

DB_NAME = "Kiln BigQuery"
GRID_WIDTH = 24


class Metabase:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.session = None

    def call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.session:
            request.add_header("X-Metabase-Session", self.session)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise SystemExit(f"{method} {path} -> HTTP {error.code}: {detail}") from None
        return json.loads(payload) if payload else None

    def wait_until_up(self, timeout_s: int = 300) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.url + "/api/health", timeout=5) as response:
                    if json.loads(response.read()).get("status") == "ok":
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
                pass
            time.sleep(3)
        raise SystemExit(f"Metabase at {self.url} did not become healthy in {timeout_s}s")

    def login(self, email: str, password: str) -> None:
        properties = self.call("GET", "/api/session/properties")
        if not properties.get("has-user-setup"):
            print("First run: creating the admin user")
            result = self.call("POST", "/api/setup", {
                "token": properties["setup-token"],
                "user": {"first_name": "Kiln", "last_name": "Admin", "email": email,
                         "password": password, "site_name": "Kiln"},
                "prefs": {"site_name": "Kiln", "site_locale": "en", "allow_tracking": False},
            })
            self.session = result["id"]
            return
        self.session = self.call("POST", "/api/session",
                                 {"username": email, "password": password})["id"]


def ensure_bigquery(mb: Metabase, key_path: Path, project: str, dataset: str) -> int:
    for database in mb.call("GET", "/api/database")["data"]:
        if database["name"] == DB_NAME:
            print(f"BigQuery connection exists (id {database['id']})")
            return database["id"]
    if not key_path.is_file():
        raise SystemExit(f"Service-account key not found at {key_path}. "
                         "Run scripts/setup_metabase_reader.sh first.")
    print(f"Connecting Metabase to BigQuery dataset {project}.{dataset}_marts")
    database = mb.call("POST", "/api/database", {
        "engine": "bigquery-cloud-sdk",
        "name": DB_NAME,
        "details": {
            "project-id": project,
            "service-account-json": key_path.read_text(),
            "dataset-filters-type": "inclusion",
            "dataset-filters-patterns": f"{dataset}_marts",
        },
    })
    return database["id"]


def layout(cards: list[dict]) -> list[tuple[int, int]]:
    """Left-to-right, top-to-bottom placement on Metabase's 24-column grid."""
    positions, row, col, row_height = [], 0, 0, 0
    for card in cards:
        width, height = card["size"]
        if col + width > GRID_WIDTH:
            row, col, row_height = row + row_height, 0, 0
        positions.append((row, col))
        col += width
        row_height = max(row_height, height)
    return positions


def replace_dashboard(mb: Metabase, spec: dict, database_id: int, marts: str) -> int:
    for existing in mb.call("GET", "/api/dashboard"):
        if existing["name"] == spec["name"] and not existing.get("archived"):
            old = mb.call("GET", f"/api/dashboard/{existing['id']}")
            for dashcard in old.get("dashcards", []):
                if dashcard.get("card_id"):
                    mb.call("PUT", f"/api/card/{dashcard['card_id']}", {"archived": True})
            mb.call("PUT", f"/api/dashboard/{existing['id']}", {"archived": True})
            print(f"Archived previous '{spec['name']}' (id {existing['id']})")

    dashboard = mb.call("POST", "/api/dashboard",
                        {"name": spec["name"], "description": spec["description"]})
    dashcards = []
    for index, (card, (row, col)) in enumerate(zip(spec["cards"], layout(spec["cards"]))):
        created = mb.call("POST", "/api/card", {
            "name": card["name"],
            "display": card["display"],
            "visualization_settings": card.get("settings", {}),
            "dataset_query": {
                "type": "native",
                "database": database_id,
                "native": {"query": card["sql"].strip().replace("{m}", marts)},
            },
        })
        width, height = card["size"]
        dashcards.append({"id": -(index + 1), "card_id": created["id"], "row": row,
                          "col": col, "size_x": width, "size_y": height})
    mb.call("PUT", f"/api/dashboard/{dashboard['id']}", {"dashcards": dashcards})
    print(f"Created '{spec['name']}': {mb.url}/dashboard/{dashboard['id']} "
          f"({len(dashcards)} questions)")
    return dashboard["id"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-id", type=int,
                        help="use this existing Metabase database instead of creating "
                             "the BigQuery connection (for testing the API flow)")
    args = parser.parse_args(argv)

    email = os.environ.get("MB_ADMIN_EMAIL") or input("Metabase admin email: ").strip()
    password = os.environ.get("MB_ADMIN_PASSWORD") or getpass.getpass("Metabase admin password: ")
    project = os.environ.get("GCP_PROJECT_ID", "finance-engineer-data-pipeline")
    dataset = os.environ.get("KILN_DATASET", "kiln")
    key_path = Path(os.environ.get("KILN_METABASE_KEY",
                                   Path.home() / ".kiln" / "metabase-reader.json")).expanduser()

    mb = Metabase(os.environ.get("MB_URL", "http://localhost:3000"))
    mb.wait_until_up()
    mb.login(email, password)
    database_id = args.database_id or ensure_bigquery(mb, key_path, project, dataset)
    marts = f"`{project}`.{dataset}_marts"
    for spec in DASHBOARDS:
        replace_dashboard(mb, spec, database_id, marts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
