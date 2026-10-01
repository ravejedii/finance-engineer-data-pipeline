"""Deliberate data-quality problems, each logged with the exact rows affected.

Two kinds:
- System mismatches (planted in the truth before rendering): Kiln orders the
  processor never settled, and processor charges Kiln has no order for.
- File mess (applied to rendered rows): duplicates, late rows, restated rows,
  malformed rows, and one missing processor B batch file.

Everything lands in an issue log that becomes data/truth/expected_issues.json,
so tests can check that each issue is caught row by row, not just by count.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import datetime, time, timedelta

from generator.render import Rendered, b_payouts
from generator.rng import hex_id, stream
from generator.simulate import Order, Truth


def _n(total: int, rate: float, floor: int) -> int:
    return max(floor, round(total * rate))


def plant_system_mismatches(truth: Truth, seed: int) -> dict:
    rng = stream(seed, "mismatches")
    scale = truth.scale
    touched = {r.order_id for r in truth.refunds} | {d.order_id for d in truth.disputes}
    candidates = sorted(
        oid for oid, o in truth.orders.items()
        if oid not in touched and oid not in truth.boundary_order_ids
    )
    # Sample per processor so even the small scale has cases on both sides.
    missing, template_ids = [], []
    for proc in ("A", "B"):
        pool = [oid for oid in candidates if truth.orders[oid].processor == proc]
        picks = rng.sample(pool, _n(len(pool), 0.0005, 2) + _n(len(pool), 0.0003, 2))
        n_missing = _n(len(pool), 0.0005, 2)
        missing.extend(picks[:n_missing])
        template_ids.extend(picks[n_missing:])
    missing.sort()
    for oid in missing:
        truth.orders[oid].in_processor = False

    orphans = []
    for i, tid in enumerate(template_ids):
        t = truth.orders[tid]
        day = scale.start + timedelta(days=rng.randint(0, (scale.end - scale.start).days))
        orphan = Order(
            order_id=90_000_000 + i + 1, seller_id=t.seller_id, product_id=t.product_id,
            buyer_id=rng.randint(1, 5_000_000), buyer_country=t.buyer_country,
            currency=t.currency, amount_minor=t.amount_minor,
            platform_fee_minor=t.platform_fee_minor, plan_id=t.plan_id,
            created_at=datetime.combine(day, time()) + timedelta(seconds=rng.randint(0, 86399)),
            processor=t.processor,
            charge_ref=hex_id(rng, "ch_") if t.processor == "A"
            else str(rng.randint(10**15, 10**16 - 1)),
            order_type="one_time", in_app_db=False,
        )
        truth.orders[orphan.order_id] = orphan
        orphans.append(orphan)

    return {
        "orders_without_settlement": {
            "description": "Kiln app-DB orders marked paid that no processor ever settled.",
            "count": len(missing),
            "order_ids": missing,
            "by_processor": _count_by(truth.orders[o].processor for o in missing),
        },
        "settlements_without_order": {
            "description": "Processor charges whose Kiln order reference does not exist "
                           "in the app DB.",
            "count": len(orphans),
            "charge_refs": [o.charge_ref for o in orphans],
            "by_processor": _count_by(o.processor for o in orphans),
        },
    }


def _count_by(values) -> dict:
    out: dict = defaultdict(int)
    for v in values:
        out[f"processor_{v.lower()}"] += 1
    return dict(sorted(out.items()))


def apply_file_mess(rendered: Rendered, truth: Truth, seed: int) -> dict:
    rng = stream(seed, "file_mess")
    end = truth.scale.end
    issues: dict = {}

    # ---------------- Processor B ----------------
    batches = sorted(rendered.b_batch_days)
    first, last = batches[0], batches[-1]
    all_batches = list(range(first, last + 1))
    batch_day = {b: truth.scale.start + timedelta(days=b - 1) for b in all_batches}
    missing_batch = all_batches[len(all_batches) // 2]
    while batch_day[missing_batch].weekday() >= 5:  # a weekday, so it looks ordinary
        missing_batch += 1

    for row in rendered.b_rows:
        row["_file_batch"] = row["_batch"]
    safe = [r for r in rendered.b_rows
            if r["_batch"] not in (missing_batch - 1, missing_batch) and r["_batch"] < last]
    picks = rng.sample(safe, min(len(safe), _n(len(safe), 0.002, 2) + _n(len(safe), 0.001, 2)))
    n_late = _n(len(safe), 0.002, 2)
    late, dups = picks[:n_late], picks[n_late:]
    for row in late:
        row["_batch"] += 1
        row["_file_batch"] = row["_batch"]
        row["Batch Number"] = str(row["_batch"])
    issues["processor_b_late_rows"] = {
        "description": "Rows booked on one CET day that arrive in the next day's batch "
                       "(Batch Number is the later batch; Creation Date is the earlier day).",
        "count": len(late),
        "keys": sorted(_b_key(r) for r in late),
    }

    payouts = b_payouts(rendered.b_rows, batch_day, seed)
    for p in payouts:
        p["_file_batch"] = p["_batch"]
        rendered.bank_rows.append(p.pop("_bank"))
    rendered.b_rows.extend(payouts)

    dup_rows = []
    for row in dups:
        copy = dict(row)
        copy["_file_batch"] = row["_file_batch"] + 1
        dup_rows.append(copy)
    rendered.b_rows.extend(dup_rows)
    issues["processor_b_duplicate_rows"] = {
        "description": "Exact duplicates of a row, re-sent in the following batch file.",
        "count": len(dup_rows),
        "keys": sorted(_b_key(r) for r in dup_rows),
    }

    dropped = [r for r in rendered.b_rows if r["_file_batch"] == missing_batch]
    rendered.b_rows = [r for r in rendered.b_rows if r["_file_batch"] != missing_batch]
    dropped_payout = sum(r.get("_amount", 0) for r in dropped)
    issues["processor_b_missing_batch_file"] = {
        "description": "One batch file never arrived. Its payout still reached the EUR bank "
                       "account, so bank receipts exceed reported settlements for that day.",
        "count": 1,
        "batch_number": missing_batch,
        "batch_date_cet": batch_day[missing_batch].isoformat(),
        "rows_in_missing_file": len(dropped),
        "payout_eur_minor": dropped_payout,
    }

    exclude = {id(r) for r in dups} | {id(r) for r in dup_rows}
    b_candidates = [r for r in rendered.b_rows
                    if r["Type"] == "Settled" and id(r) not in exclude]
    bad_b = rng.sample(b_candidates, _n(len(b_candidates), 0.00005, 1))
    for row in bad_b:
        row["Net Credit (NC)"] = row["Net Credit (NC)"].replace(".", ",")
        row["_malformed"] = True
    issues["processor_b_malformed_rows"] = {
        "description": "Rows whose Net Credit uses a decimal comma ('12,34'). "
                       "They belong in the loader's exceptions table, not the floor.",
        "count": len(bad_b),
        "keys": sorted(_b_key(r) for r in bad_b),
    }

    # ---------------- Processor A ----------------
    rows = rendered.a_rows
    movable = [r for r in rows if r["_file_day"] < end - timedelta(days=5)]
    n_dup = _n(len(movable), 0.003, 3)
    n_late = _n(len(movable), 0.005, 3)
    n_restate = _n(len(movable), 0.002, 3)
    n_bad = _n(len(movable), 0.00002, 2)
    chosen = rng.sample(movable, n_dup + n_late + n_restate + n_bad)
    a_dup = chosen[:n_dup]
    a_late = chosen[n_dup:n_dup + n_late]
    a_restate = [r for r in chosen[n_dup + n_late:n_dup + n_late + n_restate]]
    a_bad = chosen[n_dup + n_late + n_restate:]

    extra = []
    for row in a_dup:
        copy = dict(row)
        copy["_file_day"] = row["_file_day"] + timedelta(days=1)
        extra.append(copy)
    issues["processor_a_duplicate_rows"] = {
        "description": "Exact duplicates of a balance transaction, re-sent in the next "
                       "day's file.",
        "count": len(a_dup),
        "balance_transaction_ids": sorted(r["balance_transaction_id"] for r in a_dup),
    }

    for row in a_late:
        row["_file_day"] = row["_file_day"] + timedelta(days=rng.randint(1, 3))
    issues["processor_a_late_rows"] = {
        "description": "Rows that first appear 1-3 daily files after their created_utc date.",
        "count": len(a_late),
        "balance_transaction_ids": sorted(r["balance_transaction_id"] for r in a_late),
    }

    restated = []
    for row in a_restate:
        correct = dict(row)
        correct["_file_day"] = row["_file_day"] + timedelta(days=rng.randint(1, 5))
        delta = rng.choice([-1, 1]) * rng.randint(1, 25)
        wrong_fee = max(0, row["_fee"] + delta)
        row["fee"] = _major(wrong_fee)
        row["net"] = _major(row["_gross"] - wrong_fee)
        restated.append({"balance_transaction_id": row["balance_transaction_id"],
                         "first_fee_minor": wrong_fee, "restated_fee_minor": row["_fee"],
                         "restated_in_file": correct["_file_day"].isoformat()})
        extra.append(correct)
    issues["processor_a_restated_rows"] = {
        "description": "Same balance_transaction_id re-sent in a later file with a corrected "
                       "fee and net. The later version is correct.",
        "count": len(restated),
        "rows": sorted(restated, key=lambda r: r["balance_transaction_id"]),
    }

    bad_ids = []
    for i, row in enumerate(a_bad):
        bad_ids.append(row["balance_transaction_id"])
        if i % 2 == 0:
            row["gross"] = "N/A"
        else:
            row["balance_transaction_id"] = ""
        row["_malformed"] = True
    issues["processor_a_malformed_rows"] = {
        "description": "Rows with a non-numeric gross ('N/A') or an empty "
                       "balance_transaction_id. They belong in the loader's exceptions table.",
        "count": len(a_bad),
        "original_balance_transaction_ids": sorted(bad_ids),
    }
    rows.extend(extra)

    # ---------------- Bank and period cutoff ----------------
    rendered.bank_rows = [r for r in rendered.bank_rows if r["_file_day"] <= end]
    return issues


def _b_key(row: dict) -> str:
    return f'{row["Psp Reference"]}|{row["Type"]}|{row["Modification Reference"]}'


def _major(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    minor = abs(minor)
    return f"{sign}{minor // 100}.{minor % 100:02d}"


def truth_facts(truth: Truth, rendered: Rendered) -> dict:
    """Things that are true of the business, not injected, but that tests should see."""
    scale, end = truth.scale, truth.scale.end
    paid_times: dict[int, list[datetime]] = defaultdict(list)
    for p in truth.seller_payouts:
        if p.status in ("paid", "reversed"):
            paid_times[p.seller_id].append(p.initiated_at)
    for times in paid_times.values():
        times.sort()

    def after_payout(order_id: int, when: datetime) -> bool:
        o = truth.orders[order_id]
        times = paid_times.get(o.seller_id, [])
        i = bisect.bisect_right(times, o.created_at)
        return i < len(times) and times[i] < when

    refunds_after = [r.refund_id for r in truth.refunds if after_payout(r.order_id, r.created_at)]
    disputes_after = [d.dispute_ref for d in truth.disputes
                      if after_payout(d.order_id, d.opened_at)]

    negative = {s: b for s, b in truth.seller_balance_end.items() if b < 0}
    def negative_for(days: int) -> int:
        cutoff = end - timedelta(days=days)
        return sum(1 for s in negative if truth.negative_since.get(s, end) <= cutoff)

    boundary = []
    for oid in truth.boundary_order_ids:
        o = truth.orders[oid]
        boundary.append({"order_id": oid, "processor": f"processor_{o.processor.lower()}",
                         "created_utc": o.created_at.isoformat(),
                         "utc_date": o.created_at.date().isoformat()})
    b_boundary = [r for r in rendered.b_rows
                  if r.get("_local_day") and r["_local_day"] != r["_utc"].date()]

    weekend_days = sum(1 for i in range((scale.end - scale.start).days + 1)
                       if (scale.start + timedelta(days=i)).weekday() >= 5)
    return {
        "refunds_after_seller_payout": {
            "description": "Refunds created after the seller was already paid for that order's "
                           "period; they reduce a balance that has already left Kiln.",
            "count": len(refunds_after)},
        "disputes_after_seller_payout": {
            "description": "Disputes opened after the seller had been paid.",
            "count": len(disputes_after)},
        "seller_payouts_failed": {
            "description": "Seller payouts rejected by the receiving bank and returned.",
            "count": sum(p.status == "failed" for p in truth.seller_payouts)},
        "seller_payouts_reversed": {
            "description": "Seller payouts that settled and were later recalled.",
            "count": sum(p.status == "reversed" for p in truth.seller_payouts)},
        "processor_a_payouts_failed": {
            "description": "Processor A payouts to Kiln that failed; funds returned to the "
                           "processor balance and never reached the bank.",
            "count": sum(r["status"] == "failed" for r in rendered.a_payout_rows)},
        "negative_seller_balances_at_end": {
            "description": "Sellers Kiln owes less than zero at period end (they owe Kiln).",
            "count": len(negative),
            "total_usd_minor": sum(negative.values()),
            "negative_for_at_least_days": {str(d): negative_for(d) for d in (30, 60, 90)}},
        "timezone_boundary_planted": {
            "description": "Orders at 23:30 UTC on a month-end: processor A reports them on "
                           "the UTC date, processor B on the next local (CET/CEST) date, "
                           "which is also the next month.",
            "count": len(boundary), "orders": boundary},
        "timezone_boundary_natural": {
            "description": "All processor B rows whose local date differs from the UTC date.",
            "count": len(b_boundary)},
        "fx_reference_weekend_gaps": {
            "description": "Reference rates publish on business days only; weekend dates have "
                           "no row for any currency (days x 5 currencies).",
            "count": weekend_days * 5},
    }
