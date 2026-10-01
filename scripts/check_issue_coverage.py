"""Prove every planted data issue is caught, against the generator's answer key.

Reads data/truth/expected_issues.json (written by the generator) and queries the
warehouse built by `dbt build`. Each check names the issue, what was expected,
and what the warehouse shows. Exits non-zero if any check fails.

    uv run python scripts/check_issue_coverage.py

Reads GCP_PROJECT_ID, DBT_DATASET (default kiln_dev) and BQ_LOCATION (default US).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 300


class Checker:
    def __init__(self) -> None:
        self.project = os.environ["GCP_PROJECT_ID"]
        base = os.environ.get("DBT_DATASET", "kiln_dev")
        self.ds = {layer: f"{self.project}.{base}_{layer}"
                   for layer in ("raw", "staging", "intermediate", "marts")}
        self.client = bigquery.Client(project=self.project,
                                      location=os.environ.get("BQ_LOCATION", "US"))
        self.results: list[tuple[str, bool, str]] = []

    def q(self, sql: str, **params) -> list[bigquery.Row]:
        query_params = []
        for name, value in params.items():
            if isinstance(value, list):
                kind = "INT64" if value and isinstance(value[0], int) else "STRING"
                query_params.append(bigquery.ArrayQueryParameter(name, kind, value))
            else:
                kind = "INT64" if isinstance(value, int) else "STRING"
                query_params.append(bigquery.ScalarQueryParameter(name, kind, value))
        config = bigquery.QueryJobConfig(query_parameters=query_params)
        return list(self.client.query(sql, job_config=config).result(timeout=TIMEOUT_S))

    def scalar(self, sql: str, **params):
        return self.q(sql, **params)[0][0]

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))


def b_key(key: str) -> str:
    """The staging settlement_line_id for a 'psp|type|modref' key."""
    return hashlib.md5(key.encode()).hexdigest()


def main() -> None:
    truth = json.loads((ROOT / "data/truth/expected_issues.json").read_text())
    issues, meta = truth["issues"], truth["meta"]
    c = Checker()
    raw, stg, int_, marts = c.ds["raw"], c.ds["staging"], c.ds["intermediate"], c.ds["marts"]

    # ---- Processor A file mess -------------------------------------------------
    ids = issues["processor_a_duplicate_rows"]["balance_transaction_ids"]
    raw_n = c.scalar(f"select count(*) from `{raw}.processor_a_balance_transactions` "
                     "where balance_transaction_id in unnest(@ids)", ids=ids)
    stg_n = c.scalar(f"select count(*) from `{stg}.stg_processor_a__balance_transactions` "
                     "where balance_transaction_id in unnest(@ids)", ids=ids)
    c.check("processor_a_duplicate_rows", raw_n == 2 * len(ids) and stg_n == len(ids),
            f"{len(ids)} duplicated ids: {raw_n} raw rows -> {stg_n} staging rows")

    ids = issues["processor_a_late_rows"]["balance_transaction_ids"]
    stg_n = c.scalar(f"select count(*) from `{stg}.stg_processor_a__balance_transactions` "
                     "where balance_transaction_id in unnest(@ids)", ids=ids)
    c.check("processor_a_late_rows", stg_n == len(ids),
            f"{stg_n}/{len(ids)} late rows present in staging")

    rows = issues["processor_a_restated_rows"]["rows"]
    got = {r["balance_transaction_id"]: r["fee_minor"] for r in c.q(
        f"select balance_transaction_id, fee_minor from "
        f"`{stg}.stg_processor_a__balance_transactions` "
        "where balance_transaction_id in unnest(@ids)",
        ids=[r["balance_transaction_id"] for r in rows])}
    correct = sum(got.get(r["balance_transaction_id"]) == r["restated_fee_minor"] for r in rows)
    c.check("processor_a_restated_rows", correct == len(rows),
            f"{correct}/{len(rows)} restated rows carry the corrected fee")

    expected = issues["processor_a_malformed_rows"]["count"]
    got_n = c.scalar(f"select count(*) from `{stg}.stg_exceptions` "
                     "where source_table = 'processor_a_balance_transactions'")
    c.check("processor_a_malformed_rows", got_n == expected,
            f"{got_n} staging exceptions, expected {expected}")

    expected = issues["processor_a_truncated_rows"]["count"]
    got_n = c.scalar(f"select count(*) from `{raw}.load_exceptions` "
                     "where source = 'processor_a_balance_transactions'")
    c.check("processor_a_truncated_rows", got_n == expected,
            f"{got_n} loader exceptions, expected {expected}")

    # ---- Processor B file mess -------------------------------------------------
    keys = [b_key(k) for k in issues["processor_b_duplicate_rows"]["keys"]]
    stg_n = c.scalar(f"select count(*) from `{stg}.stg_processor_b__settlement_details` "
                     "where settlement_line_id in unnest(@keys)", keys=keys)
    c.check("processor_b_duplicate_rows", stg_n == len(keys),
            f"{len(keys)} duplicated lines -> {stg_n} staging rows")

    keys = [b_key(k) for k in issues["processor_b_late_rows"]["keys"]]
    stg_n = c.scalar(f"select count(*) from `{stg}.stg_processor_b__settlement_details` "
                     "where settlement_line_id in unnest(@keys)", keys=keys)
    c.check("processor_b_late_rows", stg_n == len(keys),
            f"{stg_n}/{len(keys)} late lines present in staging")

    keys = [b_key(k) for k in issues["processor_b_malformed_rows"]["keys"]]
    got_n = c.scalar(f"select count(*) from `{stg}.stg_exceptions` "
                     "where record_key in unnest(@keys)", keys=keys)
    c.check("processor_b_malformed_rows", got_n == len(keys),
            f"{got_n}/{len(keys)} decimal-comma lines in staging exceptions")

    missing = issues["processor_b_missing_batch_file"]
    gaps = [r[0] for r in c.q(f"select missing_batch_number from "
                              f"`{marts}.fct_processor_b_batch_gaps`")]
    ref = f"PAYOUT-{missing['batch_number']:04d}"
    status = c.q(f"select recon_status from `{marts}.fct_payout_reconciliation` "
                 "where payout_reference = @ref", ref=ref)
    status = status[0][0] if status else None
    c.check("processor_b_missing_batch_file",
            gaps == [missing["batch_number"]] and status == "bank_receipt_without_report",
            f"gaps={gaps}, {ref} recon_status={status}")

    # ---- System mismatches -----------------------------------------------------
    # Every unsettled order must be explained: planted, or its charge sat in the
    # missing file or in a row that went to exceptions.
    planted = set(issues["orders_without_settlement"]["order_ids"])
    explained = planted | set(missing["charge_order_ids"])
    for key in ("processor_a_malformed_rows", "processor_a_truncated_rows",
                "processor_b_malformed_rows"):
        explained |= set(issues[key]["charge_order_ids"])
    flagged = {r[0] for r in c.q(f"select order_id from `{marts}.fct_order_settlement_matches` "
                                 "where match_status = 'order_without_settlement'")}
    c.check("orders_without_settlement", flagged == explained,
            f"{len(flagged)} flagged = {len(planted)} planted + {len(explained - planted)} "
            f"whose charge was in the missing file or an exception row"
            + ("" if flagged == explained else
               f"; unexplained={sorted(flagged - explained)} missed={sorted(explained - flagged)}"))

    refs = issues["settlements_without_order"]["charge_refs"]
    got_n = c.scalar(f"select count(*) from `{marts}.fct_order_settlement_matches` "
                     "where charge_reference in unnest(@refs) "
                     "and match_status = 'settlement_without_order'", refs=refs)
    c.check("settlements_without_order", got_n == len(refs),
            f"{got_n}/{len(refs)} orphan charges flagged")

    mismatches = c.scalar(f"select count(*) from `{marts}.fct_order_settlement_matches` "
                          "where match_status = 'amount_mismatch'")
    c.check("no_unexplained_amount_mismatches", mismatches == 0,
            f"{mismatches} amount mismatches")

    # ---- Time and FX -----------------------------------------------------------
    planted = issues["timezone_boundary_planted"]["orders"]
    ok = 0
    for item in planted:
        got = c.q(f"select event_date from `{int_}.int_settlement_events` "
                  "where kiln_order_id = @oid and event_type = 'charge'", oid=item["order_id"])
        ok += bool(got) and got[0][0].isoformat() == item["utc_date"]
    c.check("timezone_boundary_planted", ok == len(planted),
            f"{ok}/{len(planted)} boundary orders land on their UTC date in both processors")

    expected = issues["fx_reference_weekend_gaps"]["count"]
    got_n = c.scalar(
        f"select count(*) from `{int_}.int_fx_rates_daily` "
        "where currency != 'USD' and not is_published "
        f"and rate_date between date('{meta['start']}') and date('{meta['end']}')"
    )
    c.check("fx_reference_weekend_gaps", got_n == expected,
            f"{got_n} forward-filled currency-days, expected {expected}")

    # ---- Payouts ---------------------------------------------------------------
    expected = issues["processor_a_payouts_failed"]["count"]
    got_n = c.scalar(f"select count(*) from `{marts}.fct_payout_reconciliation` "
                     "where processor = 'processor_a' and recon_status = 'failed_and_returned'")
    c.check("processor_a_payouts_failed", got_n == expected,
            f"{got_n} failed payouts reconciled as returned, expected {expected}")

    # Returns dated after the period end haven't happened yet in this data.
    expected = (issues["seller_payouts_failed"]["returned_in_period"]
                + issues["seller_payouts_reversed"]["returned_in_period"])
    got_n = c.scalar(f"select count(distinct journal_entry_id) from "
                     f"`{marts}.fct_ledger_entries` "
                     "where source_event_type = 'seller_payout_return'")
    c.check("seller_payouts_failed_and_reversed", got_n == expected,
            f"{got_n} payout returns posted, expected {expected}")

    # ---- Report ----------------------------------------------------------------
    width = max(len(name) for name, _, _ in c.results)
    for name, ok, detail in c.results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = [name for name, ok, _ in c.results if not ok]
    print(f"\n{len(c.results) - len(failed)}/{len(c.results)} issue checks passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
