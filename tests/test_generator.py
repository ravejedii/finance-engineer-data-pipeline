"""The generator's promises, checked against the files it actually writes.

Each issue in expected_issues.json is verified row by row here, so later
phases can trust it as the answer key.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from generator.__main__ import generate

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="session")
def out(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("small")
    generate("small", 42, path)
    return path


@pytest.fixture(scope="session")
def issues(out) -> dict:
    return json.loads((out / "truth" / "expected_issues.json").read_text())["issues"]


@pytest.fixture(scope="session")
def a_rows(out) -> list[dict]:
    rows = []
    for f in sorted((out / "raw" / "processor_a").glob("balance_transactions_*.csv")):
        file_day = f.stem.rsplit("_", 1)[1]
        rows += [{**r, "_file_day": file_day} for r in read(f)]
    return rows


@pytest.fixture(scope="session")
def b_files(out) -> dict[int, list[dict]]:
    files = {}
    for f in sorted((out / "raw" / "processor_b").glob("*.csv")):
        files[int(f.stem.rsplit("_", 1)[1])] = read(f)
    return files


def b_key(r: dict) -> str:
    return f'{r["Psp Reference"]}|{r["Type"]}|{r["Modification Reference"]}'


def digest(path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*")) if p.is_file()
    }


def test_same_seed_same_bytes(out, tmp_path):
    generate("small", 42, tmp_path)
    assert digest(tmp_path) == digest(out)


def test_different_seed_different_data(out, tmp_path):
    generate("small", 7, tmp_path)
    assert digest(tmp_path) != digest(out)


def test_processor_a_money_is_exact(a_rows):
    checked = 0
    for r in a_rows:
        if r["gross"] == "N/A" or not r["balance_transaction_id"]:
            continue
        for col in ("gross", "fee", "net"):
            assert re.fullmatch(r"-?\d+\.\d{2}", r[col]), (col, r[col])
        assert Decimal(r["gross"]) - Decimal(r["fee"]) == Decimal(r["net"])
        if r["customer_facing_currency"] == "jpy":
            assert re.fullmatch(r"-?\d+", r["customer_facing_amount"]), "JPY has no decimals"
        checked += 1
    assert checked > 500


def test_app_db_amounts_are_integers(out):
    for table, cols in {"orders": ["amount_minor", "platform_fee_minor"],
                        "refunds": ["amount_minor"],
                        "seller_payouts": ["amount_minor"]}.items():
        for r in read(out / "raw" / "app_db" / f"{table}.csv"):
            for c in cols:
                assert re.fullmatch(r"\d+", r[c]), (table, c, r[c])


def test_timestamps_in_app_db_are_utc(out):
    for r in read(out / "raw" / "app_db" / "orders.csv"):
        assert r["created_at"].endswith("Z")


def test_missing_b_batch_is_the_only_gap(b_files, issues):
    numbers = sorted(b_files)
    gaps = [n for n in range(numbers[0], numbers[-1] + 1) if n not in b_files]
    assert gaps == [issues["processor_b_missing_batch_file"]["batch_number"]]


def test_missing_b_batch_payout_still_reached_bank(out, issues):
    missing = issues["processor_b_missing_batch_file"]
    ref = f'PAYOUT-{missing["batch_number"]:04d}'
    bank = [r for f in (out / "raw" / "bank").glob("*.csv") for r in read(f)
            if r["end_to_end_reference"] == ref]
    assert len(bank) == 1
    assert Decimal(bank[0]["amount"]) * 100 == missing["payout_eur_minor"]


def test_processor_a_duplicates_late_and_restated(a_rows, issues):
    by_id = defaultdict(list)
    for r in a_rows:
        if r["balance_transaction_id"]:
            by_id[r["balance_transaction_id"]].append(r)

    def strip(r):
        return {k: v for k, v in r.items() if k != "_file_day"}

    dups = sorted(i for i, rs in by_id.items()
                  if len(rs) == 2 and strip(rs[0]) == strip(rs[1]))
    restated = sorted(i for i, rs in by_id.items()
                      if len(rs) == 2 and strip(rs[0]) != strip(rs[1]))
    late = sorted(i for i, rs in by_id.items()
                  if len(rs) == 1 and rs[0]["_file_day"] > rs[0]["created_utc"][:10])

    assert dups == issues["processor_a_duplicate_rows"]["balance_transaction_ids"]
    assert restated == [r["balance_transaction_id"]
                        for r in issues["processor_a_restated_rows"]["rows"]]
    assert late == issues["processor_a_late_rows"]["balance_transaction_ids"]
    assert max(len(rs) for rs in by_id.values()) == 2


def test_processor_a_restated_rows_differ_only_in_fee_and_net(a_rows, issues):
    by_id = defaultdict(list)
    for r in a_rows:
        by_id[r["balance_transaction_id"]].append(r)
    for item in issues["processor_a_restated_rows"]["rows"]:
        first, second = sorted(by_id[item["balance_transaction_id"]],
                               key=lambda r: r["_file_day"])
        changed = {k for k in first if first[k] != second[k]}
        assert changed == {"fee", "net", "_file_day"}
        assert Decimal(second["fee"]) * 100 == item["restated_fee_minor"]


def test_processor_a_malformed_rows(a_rows, issues):
    bad = [r for r in a_rows if r["gross"] == "N/A" or not r["balance_transaction_id"]]
    assert len(bad) == issues["processor_a_malformed_rows"]["count"]


def test_processor_b_duplicates_and_late(b_files, issues):
    rows = [r for rs in b_files.values() for r in rs if r["Type"] != "MerchantPayout"]
    counts = Counter(b_key(r) for r in rows)
    assert sorted(k for k, n in counts.items() if n == 2) == \
        issues["processor_b_duplicate_rows"]["keys"]
    assert max(counts.values()) == 2

    late_keys = set(issues["processor_b_late_rows"]["keys"])
    for batch, rs in b_files.items():
        for r in rs:
            if b_key(r) in late_keys:
                assert int(r["Batch Number"]) == batch


def test_processor_b_malformed_rows_use_decimal_comma(b_files, issues):
    bad = [b_key(r) for rs in b_files.values() for r in rs if "," in r["Net Credit (NC)"]]
    assert sorted(bad) == issues["processor_b_malformed_rows"]["keys"]


def test_processor_b_batches_net_to_their_payout(b_files, issues):
    """Each batch's MerchantPayout equals the net of its unique rows (plus any
    negative carry). Checked only where the chain isn't broken by the missing file."""
    missing = issues["processor_b_missing_batch_file"]["batch_number"]
    seen, carry, checked = set(), 0, 0
    for batch in sorted(b_files):
        if batch == missing + 1:
            carry = None  # carry after the missing batch is unknowable from files alone
        net = 0
        payout = None
        for r in b_files[batch]:
            if r["Type"] == "MerchantPayout":
                payout = Decimal(r["Net Debit (NC)"])
                continue
            if int(r["Batch Number"]) != batch or b_key(r) in seen:
                continue  # a duplicate re-sent from an earlier batch
            seen.add(b_key(r))
            credit = Decimal((r["Net Credit (NC)"] or "0").replace(",", "."))
            debit = Decimal(r["Net Debit (NC)"] or "0")
            net += credit - debit
        if carry is not None:
            if payout is None:
                carry += net
            else:
                assert payout == net + carry, batch
                carry, checked = 0, checked + 1
        elif payout is not None:
            carry = 0
    assert checked > 50


def test_every_paid_processor_a_payout_hits_the_bank(out):
    payouts = [r for f in (out / "raw" / "processor_a").glob("payouts_*.csv") for r in read(f)]
    bank = {r["end_to_end_reference"]: r for f in (out / "raw" / "bank").glob("*.csv")
            for r in read(f)}
    paid = [p for p in payouts if p["status"] == "paid"]
    assert paid
    for p in paid:
        assert bank[p["payout_id"]]["amount"] == p["amount"]
    failed = {p["payout_id"] for p in payouts if p["status"] == "failed"}
    assert failed and not failed & set(bank)


def test_system_mismatches(out, a_rows, b_files, issues):
    orders = {r["order_id"]: r for r in read(out / "raw" / "app_db" / "orders.csv")}
    a_refs = {r["source_id"] for r in a_rows}
    b_refs = {r["Psp Reference"] for rs in b_files.values() for r in rs}
    for oid in issues["orders_without_settlement"]["order_ids"]:
        ref = orders[str(oid)]["processor_charge_ref"]
        assert ref not in a_refs and ref not in b_refs

    a_order_refs = {r["source_id"]: r["payment_metadata[kiln_order_id]"] for r in a_rows}
    b_order_refs = {r["Psp Reference"]: r["Merchant Reference"]
                    for rs in b_files.values() for r in rs}
    for ref in issues["settlements_without_order"]["charge_refs"]:
        kiln_ref = a_order_refs.get(ref) or b_order_refs.get(ref)
        assert kiln_ref is not None, "orphan charge must appear in a processor file"
        assert kiln_ref.removeprefix("KILN-") not in orders


def test_timezone_boundary_orders(a_rows, b_files, issues, out):
    orders = {r["order_id"]: r for r in read(out / "raw" / "app_db" / "orders.csv")}
    for item in issues["timezone_boundary_planted"]["orders"]:
        ref = orders[str(item["order_id"])]["processor_charge_ref"]
        if item["processor"] == "processor_a":
            row = next(r for r in a_rows if r["source_id"] == ref)
            assert row["created_utc"][:10] == item["utc_date"]
        else:
            row = next(r for rs in b_files.values() for r in rs
                       if r["Psp Reference"] == ref and r["Type"] == "Settled")
            local_date = row["Creation Date"][:10]
            assert local_date > item["utc_date"]
            assert local_date[:7] != item["utc_date"][:7], "should cross a month boundary"


def test_fx_reference_rates_skip_weekends(out, issues):
    files = sorted((out / "raw" / "fx").glob("*.csv"))
    assert all(len(read(f)) == 5 for f in files)
    meta = json.loads((out / "truth" / "expected_issues.json").read_text())["meta"]
    from datetime import date
    days = (date.fromisoformat(meta["end"]) - date.fromisoformat(meta["start"])).days + 1
    assert (days - len(files)) * 5 == issues["fx_reference_weekend_gaps"]["count"]


def test_seller_payout_returns_hit_the_bank(out):
    payouts = read(out / "raw" / "app_db" / "seller_payouts.csv")
    bank = Counter(r["end_to_end_reference"] for f in (out / "raw" / "bank").glob("*.csv")
                   for r in read(f))
    returned = [p for p in payouts if p["returned_at"]]
    assert returned
    for p in returned:
        assert bank[f'SP-{p["payout_id"]}'] == 2  # the payout and its return


def test_small_scale_exercises_every_issue(issues):
    for key, item in issues.items():
        assert item["count"] > 0, key


def test_data_issues_doc_matches_small_scale(issues):
    doc = (ROOT / "DATA_ISSUES.md").read_text()
    rows = re.findall(r"^\| `(\w+)` \| (\d[\d,]*) \|", doc, flags=re.M)
    assert rows, "DATA_ISSUES.md table not found"
    documented = {k: int(v.replace(",", "")) for k, v in rows}
    assert documented == {k: v["count"] for k, v in issues.items()}
