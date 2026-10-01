"""Ground truth: what actually happened at Kiln, before any report is rendered.

The simulation walks one UTC day at a time and records business events
(sellers, products, orders, refunds, disputes, seller payouts). Processor
reports and the app database extract are rendered from this truth later,
and the mess is injected after that. Because the truth is known, every
reconciliation and data-quality test downstream has a correct answer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from generator import config as C
from generator import scenarios
from generator.fx import FxRates
from generator.money import apply_bps, convert_minor, round_half_even
from generator.rng import hex_id, poisson, stream, weighted_choice

_ADJ = ["Quiet", "Amber", "Copper", "Lunar", "Folded", "Paper", "Wild", "Slow", "Bright", "Salt"]
_NOUN = ["Fern", "Anvil", "Harbor", "Loom", "Orchard", "Signal", "Ledger", "Canvas", "Meadow", "Kettle"]
_KIND = ["Studio", "Press", "Works", "Lab", "Collective", "Guild"]
_PRODUCT = ["Preset Pack", "Course", "E-book", "Template Kit", "Font", "Sample Library",
            "Membership", "Newsletter", "Brush Set", "Workshop"]


@dataclass
class Seller:
    seller_id: int
    name: str
    country: str
    created_at: datetime
    plan_id: int
    activity: float
    churn_day: date
    segment: str | None = None
    product_ids: list[int] = field(default_factory=list)


@dataclass
class Product:
    product_id: int
    seller_id: int
    name: str
    product_type: str  # one_time | subscription
    price_usd_minor: int
    created_at: datetime


@dataclass
class Order:
    order_id: int
    seller_id: int
    product_id: int
    buyer_id: int
    buyer_country: str
    currency: str
    amount_minor: int
    platform_fee_minor: int
    plan_id: int
    created_at: datetime
    processor: str  # A | B
    charge_ref: str
    order_type: str  # one_time | subscription_initial | subscription_renewal
    in_app_db: bool = True  # False: processor charge with no Kiln order
    in_processor: bool = True  # False: Kiln order the processor never settled


@dataclass
class Refund:
    refund_id: int
    order_id: int
    amount_minor: int
    currency: str
    created_at: datetime
    processor_ref: str


@dataclass
class Dispute:
    dispute_ref: str
    order_id: int
    amount_minor: int
    currency: str
    opened_at: datetime
    outcome: str | None  # won | lost | None (still open at period end)
    resolved_at: datetime | None


@dataclass
class SellerPayout:
    payout_id: int
    seller_id: int
    amount_minor: int
    currency: str
    status: str  # paid | failed | reversed
    initiated_at: datetime
    settled_at: datetime | None
    returned_at: datetime | None


@dataclass
class PlanChange:
    seller_id: int
    plan_id: int
    effective_from: datetime


@dataclass
class Truth:
    scale: C.Scale
    fx: FxRates
    sellers: dict[int, Seller] = field(default_factory=dict)
    products: dict[int, Product] = field(default_factory=dict)
    orders: dict[int, Order] = field(default_factory=dict)
    refunds: list[Refund] = field(default_factory=list)
    disputes: list[Dispute] = field(default_factory=list)
    seller_payouts: list[SellerPayout] = field(default_factory=list)
    plan_changes: list[PlanChange] = field(default_factory=list)
    seller_balance_end: dict[int, int] = field(default_factory=dict)
    negative_since: dict[int, date] = field(default_factory=dict)
    boundary_order_ids: list[int] = field(default_factory=list)


def _at(day: date, seconds: int) -> datetime:
    return datetime.combine(day, time()) + timedelta(seconds=seconds)


def _usd(minor: int, ccy: str, fx: FxRates, day: date) -> int:
    """Kiln's booking conversion: amount / (units per USD) at the booking rate."""
    if ccy == "USD":
        return minor
    rate = fx.booking(day, ccy)
    return convert_minor(minor, ccy, "USD", Decimal(1) / rate)


def simulate(seed: int, scale: C.Scale) -> Truth:
    fx = FxRates(seed, scale.start, scale.end)
    truth = Truth(scale=scale, fx=fx)
    span = (scale.end - scale.start).days

    r_seller = stream(seed, "sellers")
    r_order = stream(seed, "orders")
    r_after = stream(seed, "post_order")
    r_payout = stream(seed, "seller_payouts")
    r_ids = stream(seed, "ids")

    counters = defaultdict(int)

    def next_id(kind: str) -> int:
        counters[kind] += 1
        return counters[kind]

    def progress(d: date) -> float:
        return max(0.0, min(1.0, (d - scale.start).days / span))

    changes_by_seller: dict[int, list[PlanChange]] = defaultdict(list)

    def current_plan(seller: Seller, ts: datetime) -> int:
        plan = seller.plan_id
        for change in changes_by_seller.get(seller.seller_id, ()):
            if change.effective_from <= ts:
                plan = change.plan_id
        return plan

    def make_seller(created_at: datetime) -> Seller:
        base = {
            "plan_id": weighted_choice(r_seller, C.PLAN_WEIGHTS),
            "activity": r_seller.lognormvariate(0, 0.9) * scale.mean_orders_per_seller_day / 1.5,
            "segment": None,
        }
        params = scenarios.apply("seller", base, {"progress": progress(created_at.date())})
        lifetime = int(r_seller.expovariate(1 / scale.seller_mean_lifetime_days))
        seller = Seller(
            seller_id=next_id("seller"),
            name=f"{r_seller.choice(_ADJ)} {r_seller.choice(_NOUN)} {r_seller.choice(_KIND)}",
            country=weighted_choice(r_seller, C.SELLER_COUNTRY_WEIGHTS),
            created_at=created_at,
            plan_id=params["plan_id"],
            activity=params["activity"],
            churn_day=created_at.date() + timedelta(days=max(lifetime, 14)),
            segment=params["segment"],
        )
        truth.sellers[seller.seller_id] = seller
        for _ in range(r_seller.randint(1, 4)):
            ptype = (
                "subscription" if r_seller.random() < C.SUBSCRIPTION_PRODUCT_SHARE else "one_time"
            )
            pbase = {"price_usd_minor": r_seller.choice([499, 900, 1500, 2500, 4900, 9900])}
            pparams = scenarios.apply(
                "product", pbase,
                {"segment": seller.segment, "rng": r_seller, "progress": progress(created_at.date())},
            )
            product = Product(
                product_id=next_id("product"), seller_id=seller.seller_id,
                name=f"{r_seller.choice(_ADJ)} {r_seller.choice(_PRODUCT)}",
                product_type=ptype, price_usd_minor=pparams["price_usd_minor"],
                created_at=created_at,
            )
            truth.products[product.product_id] = product
            seller.product_ids.append(product.product_id)
        if r_seller.random() < C.PLAN_CHANGE_RATE_PER_SELLER:
            earliest = max(created_at.date(), scale.start) + timedelta(days=30)
            if earliest < scale.end:
                eff_day = earliest + timedelta(days=r_seller.randint(0, (scale.end - earliest).days))
                new_plan = r_seller.choice([p for p in C.PLANS if p != seller.plan_id])
                change = PlanChange(
                    seller.seller_id, new_plan, _at(eff_day, r_seller.randint(0, 86399))
                )
                truth.plan_changes.append(change)
                changes_by_seller[seller.seller_id].append(change)
        return seller

    # Existing sellers joined during the year before the window opens.
    for _ in range(scale.initial_sellers):
        joined = scale.start - timedelta(days=r_seller.randint(1, 365))
        make_seller(_at(joined, r_seller.randint(0, 86399)))

    renewals: dict[date, list[tuple[int, int, str]]] = defaultdict(list)
    # Per-seller ledger of what Kiln owes, in USD minor units.
    available: dict[int, int] = defaultdict(int)
    held: dict[date, list[tuple[int, int]]] = defaultdict(list)
    credits_due: dict[date, list[tuple[int, int]]] = defaultdict(list)
    refunds_due: dict[date, list[Refund]] = defaultdict(list)
    disputes_open_due: dict[date, list[Dispute]] = defaultdict(list)
    disputes_resolve_due: dict[date, list[Dispute]] = defaultdict(list)

    def route(country: str) -> str:
        europe = country in C.EU_COUNTRIES or country == "GB"
        share_b = C.PROC_B_SHARE_EUROPE if europe else C.PROC_B_SHARE_OTHER
        return "B" if r_order.random() < share_b else "A"

    def charge_ref(processor: str) -> str:
        if processor == "A":
            return hex_id(r_ids, "ch_")
        return str(r_ids.randint(10**15, 10**16 - 1))

    def place_order(seller: Seller, product: Product, buyer_id: int, country: str,
                    ts: datetime, order_type: str, processor: str | None = None) -> Order:
        ccy = C.COUNTRY_CURRENCY[country]
        day = ts.date()
        # Localized price: the USD list price converted at the booking rate.
        amount = convert_minor(product.price_usd_minor, "USD", ccy, fx.booking(day, ccy))
        amount = max(amount, 1)
        plan = C.PLANS[current_plan(seller, ts)]
        fee = apply_bps(amount, plan.fee_bps) + plan.fixed_fee_minor[ccy]
        fee = min(fee, amount)
        processor = processor or route(country)
        order = Order(
            order_id=next_id("order"), seller_id=seller.seller_id, product_id=product.product_id,
            buyer_id=buyer_id, buyer_country=country, currency=ccy, amount_minor=amount,
            platform_fee_minor=fee, plan_id=plan.plan_id, created_at=ts, processor=processor,
            charge_ref=charge_ref(processor), order_type=order_type,
        )
        truth.orders[order.order_id] = order
        net_usd = _usd(amount, ccy, fx, day) - _usd(fee, ccy, fx, day)
        held[day + timedelta(days=C.SELLER_PAYOUT_HOLD_DAYS)].append((seller.seller_id, net_usd))
        _schedule_after(order)
        return order

    def _schedule_after(order: Order) -> None:
        day = order.created_at.date()
        ctx = {"progress": progress(day), "buyer_country": order.buyer_country,
               "processor": order.processor}
        refunded = 0
        rparams = scenarios.apply("refund", {"rate": C.REFUND_RATE}, ctx)
        if r_after.random() < rparams["rate"]:
            partial = r_after.random() < C.REFUND_PARTIAL_SHARE
            refunded = (
                max(1, round_half_even(Decimal(order.amount_minor) * Decimal(r_after.randint(20, 80)) / 100))
                if partial else order.amount_minor
            )
            rday = day + timedelta(days=min(int(r_after.expovariate(1 / 4)), 14))
            ts = max(order.created_at + timedelta(minutes=5), _at(rday, r_after.randint(0, 86399)))
            refund = Refund(
                refund_id=next_id("refund"), order_id=order.order_id, amount_minor=refunded,
                currency=order.currency, created_at=ts,
                processor_ref=hex_id(r_ids, "re_") if order.processor == "A"
                else str(r_ids.randint(10**15, 10**16 - 1)),
            )
            if ts.date() <= scale.end:
                refunds_due[ts.date()].append(refund)
            else:
                refunded = 0
        remaining = order.amount_minor - refunded
        dparams = scenarios.apply("dispute", {"rate": scale.dispute_rate}, ctx)
        if remaining > 0 and r_after.random() < dparams["rate"]:
            oday = day + timedelta(days=r_after.randint(15, 75))
            opened = _at(oday, r_after.randint(0, 86399))
            won = r_after.random() < C.DISPUTE_WIN_RATE
            resolved = opened + timedelta(days=r_after.randint(30, 75))
            dispute = Dispute(
                dispute_ref=hex_id(r_ids, "dp_") if order.processor == "A"
                else str(r_ids.randint(10**15, 10**16 - 1)),
                order_id=order.order_id, amount_minor=remaining, currency=order.currency,
                opened_at=opened, outcome=None, resolved_at=None,
            )
            if oday <= scale.end:
                disputes_open_due[oday].append(dispute)
                if resolved.date() <= scale.end:
                    dispute.outcome = "won" if won else "lost"
                    dispute.resolved_at = resolved
                    disputes_resolve_due[resolved.date()].append(dispute)

    def dispute_fee_usd(order: Order, day: date) -> int:
        if order.processor == "A":
            return C.PROC_A_DISPUTE_FEE_USD_MINOR
        return _usd(C.PROC_B_CHARGEBACK_FEE_EUR_MINOR, "EUR", fx, day)

    # Planted timezone-boundary pair: 23:30 UTC on the last day of a winter month
    # (00:30 CET the next day), one order per processor.
    boundary_day = _last_winter_month_end(scale)

    day = scale.start
    while day <= scale.end:
        if day > scale.start:
            for _ in range(poisson(r_seller, scale.new_sellers_per_day)):
                make_seller(_at(day, r_seller.randint(0, 86399)))

        weekday_factor = 0.85 if day.weekday() >= 5 else 1.0
        season = 1.3 if day.month in (11, 12) else 1.0
        for seller in list(truth.sellers.values()):
            if seller.created_at.date() > day or day >= seller.churn_day:
                continue
            for _ in range(poisson(r_order, seller.activity * weekday_factor * season)):
                product = truth.products[r_order.choice(seller.product_ids)]
                country = weighted_choice(r_order, C.BUYER_COUNTRY_WEIGHTS)
                ts = _at(day, r_order.randint(0, 86399))
                if ts < seller.created_at:
                    continue
                buyer = r_order.randint(1, 5_000_000)
                is_sub = product.product_type == "subscription"
                otype = "subscription_initial" if is_sub else "one_time"
                place_order(seller, product, buyer, country, ts, otype)
                if is_sub:
                    nxt = day + timedelta(days=30)
                    while nxt <= scale.end and r_order.random() > C.SUBSCRIPTION_CANCEL_RATE:
                        renewals[nxt].append((product.product_id, buyer, country))
                        nxt += timedelta(days=30)

        for product_id, buyer, country in renewals.pop(day, []):
            product = truth.products[product_id]
            seller = truth.sellers[product.seller_id]
            if day >= seller.churn_day:
                continue
            place_order(seller, product, buyer, country, _at(day, r_order.randint(0, 86399)),
                        "subscription_renewal")

        if day == boundary_day:
            for proc in ("A", "B"):
                seller = next(s for s in truth.sellers.values()
                              if s.created_at.date() < day < s.churn_day)
                product = truth.products[seller.product_ids[0]]
                country = "US" if proc == "A" else "DE"
                ts = _at(day, 23 * 3600 + 30 * 60)
                order = place_order(seller, product, 4_999_999, country, ts, "one_time", proc)
                truth.boundary_order_ids.append(order.order_id)

        # Balance movements that happen today.
        for seller_id, amt in held.pop(day, []):
            available[seller_id] += amt
        for seller_id, amt in credits_due.pop(day, []):
            available[seller_id] += amt
        for refund in refunds_due.pop(day, []):
            order = truth.orders[refund.order_id]
            truth.refunds.append(refund)
            available[order.seller_id] -= _usd(refund.amount_minor, refund.currency, fx, day)
        for dispute in disputes_open_due.pop(day, []):
            order = truth.orders[dispute.order_id]
            truth.disputes.append(dispute)
            available[order.seller_id] -= (
                _usd(dispute.amount_minor, dispute.currency, fx, day) + dispute_fee_usd(order, day)
            )
        for dispute in disputes_resolve_due.pop(day, []):
            if dispute.outcome == "won":
                order = truth.orders[dispute.order_id]
                available[order.seller_id] += _usd(dispute.amount_minor, dispute.currency, fx, day)

        if day.weekday() == C.SELLER_PAYOUT_WEEKDAY:
            for seller_id in sorted(available):
                amt = available[seller_id]
                if amt < C.SELLER_PAYOUT_MIN_USD_MINOR:
                    continue
                initiated = _at(day, 6 * 3600 + r_payout.randint(0, 3599))
                roll = r_payout.random()
                status, settled, returned = "paid", initiated + timedelta(days=1), None
                if roll < C.SELLER_PAYOUT_FAIL_RATE:
                    status, settled, returned = "failed", None, initiated + timedelta(days=3)
                elif roll < C.SELLER_PAYOUT_FAIL_RATE + C.SELLER_PAYOUT_REVERSE_RATE:
                    status, returned = "reversed", initiated + timedelta(days=10)
                payout = SellerPayout(
                    payout_id=next_id("seller_payout"), seller_id=seller_id, amount_minor=amt,
                    currency="USD", status=status, initiated_at=initiated, settled_at=settled,
                    returned_at=returned,
                )
                truth.seller_payouts.append(payout)
                available[seller_id] -= amt
                if returned is not None:
                    if returned.date() <= scale.end:
                        credits_due[returned.date()].append((seller_id, amt))
                    else:
                        payout.returned_at = None

        for seller_id, bal in available.items():
            if bal < 0:
                truth.negative_since.setdefault(seller_id, day)
            else:
                truth.negative_since.pop(seller_id, None)
        day += timedelta(days=1)

    # Held funds not yet released at period end still belong to the seller.
    for rows in held.values():
        for seller_id, amt in rows:
            available[seller_id] += amt
    truth.seller_balance_end = dict(available)
    return truth


def _last_winter_month_end(scale: C.Scale) -> date:
    """The latest month-end before the window's last day that falls in CET (not CEST)."""
    day = scale.end - timedelta(days=1)
    while day >= scale.start:
        nxt = day + timedelta(days=1)
        if nxt.day == 1 and day.month in (1, 2, 11, 12):
            return day
        day -= timedelta(days=1)
    # No winter month-end in range: fall back to the latest earlier month-end (CEST).
    day = scale.end - timedelta(days=1)
    while (day + timedelta(days=1)).day != 1:
        day -= timedelta(days=1)
    return day
