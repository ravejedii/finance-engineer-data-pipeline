"""Render the truth into the files each system would actually deliver.

Rows carry private bookkeeping keys prefixed with "_" (file day, batch,
amounts in minor units) that the mess step uses; writers drop them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from generator import config as C
from generator import scenarios
from generator.money import apply_bps, convert_minor, format_major
from generator.rng import hex_id, stream
from generator.simulate import Order, Truth

CET = ZoneInfo(C.PROC_B_TIMEZONE)
UTC = ZoneInfo("UTC")

A_COLUMNS = [
    "balance_transaction_id", "created_utc", "available_on_utc", "currency", "gross", "fee",
    "net", "reporting_category", "source_id", "description", "customer_facing_amount",
    "customer_facing_currency", "automatic_payout_id", "payment_metadata[kiln_order_id]",
]
A_PAYOUT_COLUMNS = [
    "payout_id", "created_utc", "arrival_date", "amount", "currency", "status", "failure_code",
    "status_changed_utc",
]
B_COLUMNS = [
    "Company Account", "Merchant Account", "Psp Reference", "Merchant Reference",
    "Payment Method", "Creation Date", "TimeZone", "Type", "Modification Reference",
    "Gross Currency", "Gross Debit (GC)", "Gross Credit (GC)", "Exchange Rate", "Net Currency",
    "Net Debit (NC)", "Net Credit (NC)", "Commission (NC)", "Markup (NC)", "Scheme Fees (NC)",
    "Interchange (NC)", "Batch Number",
]
BANK_COLUMNS = [
    "bank_transaction_id", "account_number", "booking_date", "value_date", "amount", "currency",
    "counterparty_name", "end_to_end_reference", "description",
]
BANK_USD = "KILN-USD-0001"
BANK_EUR = "KILN-EUR-0001"


@dataclass
class Rendered:
    a_rows: list[dict] = field(default_factory=list)
    a_payout_rows: list[dict] = field(default_factory=list)
    b_rows: list[dict] = field(default_factory=list)
    bank_rows: list[dict] = field(default_factory=list)
    fx_rows: list[dict] = field(default_factory=list)
    app_db: dict[str, list[dict]] = field(default_factory=dict)
    b_batch_days: dict[int, date] = field(default_factory=dict)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _iso(dt: datetime | None) -> str:
    return "" if dt is None else dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def next_business_day(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def business_day_on_or_after(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def render(truth: Truth, seed: int) -> Rendered:
    out = Rendered()
    scale, fx = truth.scale, truth.fx
    span = (scale.end - scale.start).days
    r_ids = stream(seed, "render_ids")
    r_pay = stream(seed, "processor_payouts")

    def progress(d: date) -> float:
        return max(0.0, min(1.0, (d - scale.start).days / span))

    def spread(processor: str, ccy: str, day: date, default):
        ctx = {"processor": processor, "currency": ccy, "progress": progress(day)}
        return scenarios.apply("fx", {"spread": default}, ctx)["spread"]

    settled_orders = [o for o in truth.orders.values() if o.in_processor]
    refunds = [r for r in truth.refunds if truth.orders[r.order_id].in_processor]
    disputes = [d for d in truth.disputes if truth.orders[d.order_id].in_processor]

    # ---------------- Processor A: balance transactions ----------------
    a_events: list[tuple[datetime, dict]] = []
    ready_on: dict[date, list[dict]] = defaultdict(list)

    def a_txn(ts: datetime, gross: int, fee: int, category: str, source: str,
              order: Order | None, cf_minor: int | None, desc: str) -> dict:
        avail = ts.date() + timedelta(days=C.PROC_A_AVAILABILITY_DAYS)
        row = {
            "balance_transaction_id": hex_id(r_ids, "txn_"),
            "created_utc": _ts(ts),
            "available_on_utc": _ts(datetime.combine(avail, time())),
            "currency": "usd",
            "gross": format_major(gross, "USD"),
            "fee": format_major(fee, "USD"),
            "net": format_major(gross - fee, "USD"),
            "reporting_category": category,
            "source_id": source,
            "description": desc,
            "customer_facing_amount": "" if cf_minor is None
            else format_major(cf_minor, order.currency),
            "customer_facing_currency": "" if order is None else order.currency.lower(),
            "automatic_payout_id": "",
            "payment_metadata[kiln_order_id]": "" if order is None else str(order.order_id),
            "_gross": gross, "_fee": fee, "_available_on": avail, "_created": ts,
        }
        a_events.append((ts, row))
        if category != "payout":
            ready_on[avail].append(row)
        return row

    def a_rate(ccy: str, day: date):
        return fx.proc_a_usd_per_unit(day, ccy, spread("A", ccy, day, C.PROC_A_FX_SPREAD))

    def to_usd_a(minor: int, ccy: str, day: date) -> int:
        return minor if ccy == "USD" else convert_minor(minor, ccy, "USD", a_rate(ccy, day))

    for o in settled_orders:
        if o.processor != "A":
            continue
        gross = to_usd_a(o.amount_minor, o.currency, o.created_at.date())
        fee = apply_bps(gross, C.PROC_A_FEE_BPS) + C.PROC_A_FIXED_FEE_USD_MINOR
        a_txn(o.created_at, gross, fee, "charge", o.charge_ref, o, o.amount_minor,
              "Kiln order")
    for r in refunds:
        o = truth.orders[r.order_id]
        if o.processor != "A":
            continue
        gross = -to_usd_a(r.amount_minor, r.currency, r.created_at.date())
        a_txn(r.created_at, gross, 0, "refund", r.processor_ref, o, -r.amount_minor,
              "REFUND FOR CHARGE")
    for d in disputes:
        o = truth.orders[d.order_id]
        if o.processor != "A":
            continue
        gross = -to_usd_a(d.amount_minor, d.currency, d.opened_at.date())
        a_txn(d.opened_at, gross, C.PROC_A_DISPUTE_FEE_USD_MINOR, "dispute", d.dispute_ref, o,
              -d.amount_minor, "Chargeback withdrawal")
        if d.outcome == "won":
            gross = to_usd_a(d.amount_minor, d.currency, d.resolved_at.date())
            a_txn(d.resolved_at, gross, 0, "dispute_reversal", d.dispute_ref, o, d.amount_minor,
                  "Chargeback reversal")

    # Daily automatic payouts of the available balance, with occasional failures.
    # Rows that became available earlier (before the window) are swept on day one.
    eligible: list[dict] = []
    for avail in sorted(d for d in ready_on if d < scale.start):
        eligible.extend(ready_on.pop(avail))
    day = scale.start
    returns_due: dict[date, list[tuple[str, int]]] = defaultdict(list)
    while day <= scale.end:
        for payout_id, amt in returns_due.pop(day, []):
            ts = datetime.combine(day, time(4, 0))
            a_txn(ts, amt, 0, "payout_reversal", payout_id, None, None, "PAYOUT FAILURE")
        eligible.extend(ready_on.pop(day, []))
        amount = sum(row["_gross"] - row["_fee"] for row in eligible)
        if amount > 0:
            payout_id = hex_id(r_ids, "po_")
            created = datetime.combine(day, time(3, 0))
            for row in eligible:
                row["automatic_payout_id"] = payout_id
            eligible = []
            prow = a_txn(created, -amount, 0, "payout", payout_id, None, None,
                         "AUTOMATIC PAYOUT")
            prow["automatic_payout_id"] = payout_id
            arrival = next_business_day(day)
            failed = r_pay.random() < scale.proc_a_payout_fail_rate
            base = {"payout_id": payout_id, "created_utc": _ts(created),
                    "arrival_date": arrival.isoformat(), "amount": format_major(amount, "USD"),
                    "currency": "usd", "failure_code": ""}
            out.a_payout_rows.append({**base, "status": "in_transit",
                                      "status_changed_utc": _ts(created), "_file_day": day})
            if failed:
                fail_day = day + timedelta(days=2)
                if fail_day <= scale.end:
                    out.a_payout_rows.append({
                        **base, "status": "failed", "failure_code": "account_closed",
                        "status_changed_utc": _ts(datetime.combine(fail_day, time(4, 0))),
                        "_file_day": fail_day,
                    })
                    returns_due[fail_day].append((payout_id, amount))
            elif arrival <= scale.end:
                out.a_payout_rows.append({
                    **base, "status": "paid",
                    "status_changed_utc": _ts(datetime.combine(arrival, time(9, 0))),
                    "_file_day": arrival,
                })
                out.bank_rows.append(_bank(r_ids, BANK_USD, arrival, amount, "USD",
                                           "PROCESSOR A PAYOUTS", payout_id,
                                           "PROCESSOR A TRANSFER"))
        day += timedelta(days=1)

    for _, row in sorted(a_events, key=lambda e: (e[0], e[1]["balance_transaction_id"])):
        if row["_created"].date() <= scale.end:
            row["_file_day"] = row["_created"].date()
            out.a_rows.append(row)

    # ---------------- Processor B: settlement detail, daily CET batches ----------------
    def b_rate(ccy: str, day: date):
        return fx.proc_b_eur_per_unit(day, ccy, spread("B", ccy, day, C.PROC_B_FX_SPREAD))

    def local(ts: datetime) -> datetime:
        return ts.replace(tzinfo=UTC).astimezone(CET)

    def b_row(ts: datetime, psp: str, order: Order, rtype: str, modref: str,
              gross_minor: int, gross_side: str, net_minor: int, net_side: str,
              rate, fees: tuple[int, int, int, int] = (0, 0, 0, 0)) -> dict:
        loc = local(ts)
        commission, markup, scheme, interchange = fees
        return {
            "Company Account": "KilnPlatform", "Merchant Account": "KilnMarketplaceEU",
            "Psp Reference": psp,
            "Merchant Reference": f"KILN-{order.order_id}" if order else "",
            "Payment Method": "visa" if int(psp) % 3 else "mc",
            "Creation Date": loc.strftime("%Y-%m-%d %H:%M:%S"),
            "TimeZone": loc.tzname(),
            "Type": rtype, "Modification Reference": modref,
            "Gross Currency": order.currency if order else "EUR",
            "Gross Debit (GC)": format_major(gross_minor, order.currency if order else "EUR")
            if gross_side == "debit" and order else "",
            "Gross Credit (GC)": format_major(gross_minor, order.currency if order else "EUR")
            if gross_side == "credit" and order else "",
            "Exchange Rate": f"{rate}" if rate is not None else "",
            "Net Currency": "EUR",
            "Net Debit (NC)": format_major(net_minor, "EUR") if net_side == "debit" else "",
            "Net Credit (NC)": format_major(net_minor, "EUR") if net_side == "credit" else "",
            "Commission (NC)": format_major(commission, "EUR") if commission else "",
            "Markup (NC)": format_major(markup, "EUR") if markup else "",
            "Scheme Fees (NC)": format_major(scheme, "EUR") if scheme else "",
            "Interchange (NC)": format_major(interchange, "EUR") if interchange else "",
            "_net_signed": net_minor if net_side == "credit" else -net_minor,
            "_local_day": loc.date(), "_utc": ts,
        }

    b_events: list[dict] = []
    for o in settled_orders:
        if o.processor != "B":
            continue
        rate = b_rate(o.currency, o.created_at.date())
        gross_eur = convert_minor(o.amount_minor, o.currency, "EUR", rate)
        fees = (C.PROC_B_COMMISSION_EUR_MINOR, apply_bps(gross_eur, C.PROC_B_MARKUP_BPS),
                apply_bps(gross_eur, C.PROC_B_SCHEME_BPS),
                apply_bps(gross_eur, C.PROC_B_INTERCHANGE_BPS))
        b_events.append(b_row(o.created_at, o.charge_ref, o, "Settled", o.charge_ref,
                              o.amount_minor, "credit", gross_eur - sum(fees), "credit",
                              rate, fees))
    for r in refunds:
        o = truth.orders[r.order_id]
        if o.processor != "B":
            continue
        rate = b_rate(r.currency, r.created_at.date())
        eur = convert_minor(r.amount_minor, r.currency, "EUR", rate)
        b_events.append(b_row(r.created_at, o.charge_ref, o, "Refunded", r.processor_ref,
                              r.amount_minor, "debit", eur, "debit", rate))
    for d in disputes:
        o = truth.orders[d.order_id]
        if o.processor != "B":
            continue
        rate = b_rate(d.currency, d.opened_at.date())
        eur = convert_minor(d.amount_minor, d.currency, "EUR", rate)
        b_events.append(b_row(d.opened_at, o.charge_ref, o, "Chargeback", d.dispute_ref,
                              d.amount_minor, "debit", eur, "debit", rate))
        fee_row = b_row(d.opened_at, o.charge_ref, o, "Fee", f"CBFEE-{d.dispute_ref}",
                        0, "none", C.PROC_B_CHARGEBACK_FEE_EUR_MINOR, "debit", None)
        fee_row["Gross Currency"] = "EUR"
        b_events.append(fee_row)
        if d.outcome == "won":
            rate = b_rate(d.currency, d.resolved_at.date())
            eur = convert_minor(d.amount_minor, d.currency, "EUR", rate)
            b_events.append(b_row(d.resolved_at, o.charge_ref, o, "ChargebackReversed",
                                  d.dispute_ref, d.amount_minor, "credit", eur, "credit", rate))

    for row in b_events:
        batch = (row["_local_day"] - scale.start).days + 1
        row["Batch Number"] = str(batch)
        row["_batch"] = batch
        out.b_batch_days[batch] = row["_local_day"]
    b_events.sort(key=lambda r: (r["_batch"], r["_utc"], r["Psp Reference"], r["Type"]))
    out.b_rows = b_events

    # ---------------- Seller payouts through the USD bank account ----------------
    for p in truth.seller_payouts:
        seller = truth.sellers[p.seller_id]
        ref = f"SP-{p.payout_id}"
        out.bank_rows.append(_bank(r_ids, BANK_USD, p.initiated_at.date(), -p.amount_minor, "USD",
                                   seller.name.upper(), ref, "SELLER PAYOUT"))
        if p.returned_at is not None:
            reason = "RETURNED - ACCOUNT INVALID" if p.status == "failed" else "PAYMENT RECALLED"
            out.bank_rows.append(_bank(r_ids, BANK_USD, p.returned_at.date(), p.amount_minor,
                                       "USD", seller.name.upper(), ref, reason))

    out.fx_rows = fx.published_rows()
    out.app_db = _app_db(truth)
    return out


def b_payouts(rows: list[dict], batch_days: dict[int, date], seed: int) -> list[dict]:
    """One MerchantPayout per batch for the batch's positive net; negatives carry forward.

    Computed after late rows have moved batches, because money settles in the
    batch a row actually lands in.
    """
    r_ids = stream(seed, "b_payout_ids")
    by_batch: dict[int, int] = defaultdict(int)
    for row in rows:
        by_batch[row["_batch"]] += row["_net_signed"]
    payouts, carry = [], 0
    for batch in sorted(batch_days):
        total = by_batch.get(batch, 0) + carry
        if total <= 0:
            carry = total
            continue
        carry = 0
        day = batch_days[batch]
        close = datetime.combine(day, time(23, 59, 59))
        payouts.append({
            "Company Account": "KilnPlatform", "Merchant Account": "KilnMarketplaceEU",
            "Psp Reference": "", "Merchant Reference": "", "Payment Method": "",
            "Creation Date": close.strftime("%Y-%m-%d %H:%M:%S"),
            "TimeZone": close.replace(tzinfo=CET).tzname(),
            "Type": "MerchantPayout", "Modification Reference": f"PAYOUT-{batch:04d}",
            "Gross Currency": "EUR", "Gross Debit (GC)": format_major(total, "EUR"),
            "Gross Credit (GC)": "", "Exchange Rate": "", "Net Currency": "EUR",
            "Net Debit (NC)": format_major(total, "EUR"), "Net Credit (NC)": "",
            "Commission (NC)": "", "Markup (NC)": "", "Scheme Fees (NC)": "",
            "Interchange (NC)": "", "Batch Number": str(batch),
            "_batch": batch, "_net_signed": -total, "_amount": total,
            "_bank": _bank(r_ids, BANK_EUR, next_business_day(day), total, "EUR",
                           "PROCESSOR B SETTLEMENT", f"PAYOUT-{batch:04d}",
                           f"SETTLEMENT BATCH {batch}"),
        })
    return payouts


def _bank(r_ids, account: str, value_day: date, amount_minor: int, ccy: str,
          counterparty: str, ref: str, desc: str) -> dict:
    booking = business_day_on_or_after(value_day)
    return {
        "bank_transaction_id": f"BT{r_ids.getrandbits(40):012d}",
        "account_number": account, "booking_date": booking.isoformat(),
        "value_date": value_day.isoformat(), "amount": format_major(amount_minor, ccy),
        "currency": ccy, "counterparty_name": counterparty, "end_to_end_reference": ref,
        "description": desc, "_file_day": booking,
    }


def _app_db(truth: Truth) -> dict[str, list[dict]]:
    end = datetime.combine(truth.scale.end, time(23, 59, 59))
    refunded: dict[int, int] = defaultdict(int)
    for r in truth.refunds:
        refunded[r.order_id] += r.amount_minor

    sellers, changes = [], []
    for s in truth.sellers.values():
        churned = datetime.combine(s.churn_day, time()) if s.churn_day <= truth.scale.end else None
        plan_now = s.plan_id
        changes.append({"seller_id": s.seller_id, "plan_id": s.plan_id,
                        "effective_from": _iso(s.created_at)})
        for c in truth.plan_changes:
            if c.seller_id == s.seller_id and c.effective_from <= end:
                plan_now = c.plan_id
                changes.append({"seller_id": s.seller_id, "plan_id": c.plan_id,
                                "effective_from": _iso(c.effective_from)})
        sellers.append({
            "seller_id": s.seller_id, "name": s.name, "country": s.country,
            "plan_id": plan_now, "status": "churned" if churned else "active",
            "created_at": _iso(s.created_at), "churned_at": _iso(churned),
        })

    orders = []
    for o in truth.orders.values():
        if not o.in_app_db:
            continue
        r = refunded.get(o.order_id, 0)
        status = "paid" if r == 0 else ("refunded" if r >= o.amount_minor else "partially_refunded")
        orders.append({
            "order_id": o.order_id, "seller_id": o.seller_id, "product_id": o.product_id,
            "buyer_id": o.buyer_id, "buyer_country": o.buyer_country, "currency": o.currency,
            "amount_minor": o.amount_minor, "platform_fee_minor": o.platform_fee_minor,
            "plan_id": o.plan_id,
            "processor": "processor_a" if o.processor == "A" else "processor_b",
            "processor_charge_ref": o.charge_ref, "order_type": o.order_type,
            "status": status, "created_at": _iso(o.created_at),
        })

    return {
        "plans": [{"plan_id": p.plan_id, "name": p.name, "fee_bps": p.fee_bps}
                  for p in C.PLANS.values()],
        "plan_fixed_fees": [{"plan_id": p.plan_id, "currency": c, "fixed_fee_minor": v}
                            for p in C.PLANS.values() for c, v in p.fixed_fee_minor.items()],
        "sellers": sellers,
        "seller_plan_changes": changes,
        "products": [{"product_id": p.product_id, "seller_id": p.seller_id, "name": p.name,
                      "product_type": p.product_type, "price_usd_minor": p.price_usd_minor,
                      "created_at": _iso(p.created_at)} for p in truth.products.values()],
        "orders": orders,
        "refunds": [{"refund_id": r.refund_id, "order_id": r.order_id,
                     "amount_minor": r.amount_minor, "currency": r.currency,
                     "processor_refund_ref": r.processor_ref, "created_at": _iso(r.created_at)}
                    for r in truth.refunds if truth.orders[r.order_id].in_app_db],
        "seller_payouts": [{"payout_id": p.payout_id, "seller_id": p.seller_id,
                            "amount_minor": p.amount_minor, "currency": p.currency,
                            "status": p.status, "initiated_at": _iso(p.initiated_at),
                            "settled_at": _iso(p.settled_at),
                            "returned_at": _iso(p.returned_at)}
                           for p in truth.seller_payouts],
    }
