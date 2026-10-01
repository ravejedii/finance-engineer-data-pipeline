"""Static configuration: scales, currencies, plans, processor pricing.

Every money constant is an integer in minor units or a Decimal rate.
No floats touch money anywhere in the generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# ISO 4217 minor-unit exponents. JPY has no minor unit.
MINOR_UNITS: dict[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "BRL": 2, "CAD": 2, "JPY": 0}
CURRENCIES = tuple(MINOR_UNITS)
FOREIGN_CURRENCIES = tuple(c for c in CURRENCIES if c != "USD")

# Kiln's functional currency. Seller balances and seller payouts are in USD.
FUNCTIONAL_CURRENCY = "USD"

# Buyer country -> presentment currency, and the share of buyers from it.
COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD", "DE": "EUR", "FR": "EUR", "NL": "EUR", "ES": "EUR", "IT": "EUR",
    "GB": "GBP", "BR": "BRL", "CA": "CAD", "JP": "JPY",
}
BUYER_COUNTRY_WEIGHTS: dict[str, int] = {
    "US": 45, "DE": 6, "FR": 5, "NL": 3, "ES": 3, "IT": 3, "GB": 10, "BR": 9, "CA": 8, "JP": 8,
}
EU_COUNTRIES = frozenset({"DE", "FR", "NL", "ES", "IT"})
SELLER_COUNTRY_WEIGHTS: dict[str, int] = {"US": 55, "GB": 12, "DE": 8, "CA": 8, "BR": 9, "JP": 8}

# Opening reference rates, quoted as units of currency per 1 USD, with daily volatility.
FX_OPENING: dict[str, tuple[Decimal, float]] = {
    "EUR": (Decimal("0.925"), 0.0040),
    "GBP": (Decimal("0.790"), 0.0045),
    "BRL": (Decimal("5.050"), 0.0090),
    "CAD": (Decimal("1.360"), 0.0030),
    "JPY": (Decimal("150.25"), 0.0050),
}


@dataclass(frozen=True)
class Plan:
    plan_id: int
    name: str
    fee_bps: int  # percentage fee in basis points
    fixed_fee_minor: dict[str, int]  # per presentment currency


_FIXED_30C = {"USD": 30, "EUR": 25, "GBP": 20, "BRL": 150, "CAD": 40, "JPY": 40}
_NO_FIXED = {c: 0 for c in CURRENCIES}

PLANS: dict[int, Plan] = {
    1: Plan(1, "starter", 1000, _NO_FIXED),
    2: Plan(2, "creator", 700, _FIXED_30C),
    3: Plan(3, "pro", 350, _FIXED_30C),
}
PLAN_WEIGHTS = {1: 50, 2: 35, 3: 15}

# Processor A: percentage plus fixed fee, settles everything in USD.
PROC_A_FEE_BPS = 290
PROC_A_FIXED_FEE_USD_MINOR = 30
PROC_A_DISPUTE_FEE_USD_MINOR = 1500
PROC_A_FX_SPREAD = Decimal("0.010")
PROC_A_SETTLEMENT_CURRENCY = "USD"
PROC_A_AVAILABILITY_DAYS = 2

# Processor B: interchange-plus pricing, settles everything in EUR, daily batches.
PROC_B_INTERCHANGE_BPS = 120
PROC_B_SCHEME_BPS = 20
PROC_B_MARKUP_BPS = 60
PROC_B_COMMISSION_EUR_MINOR = 11
PROC_B_CHARGEBACK_FEE_EUR_MINOR = 1500
PROC_B_FX_SPREAD = Decimal("0.006")
PROC_B_SETTLEMENT_CURRENCY = "EUR"
PROC_B_TIMEZONE = "Europe/Amsterdam"  # reported as CET / CEST

# Seller payouts: weekly on Mondays, after a hold period, above a minimum.
SELLER_PAYOUT_WEEKDAY = 0
SELLER_PAYOUT_HOLD_DAYS = 7
SELLER_PAYOUT_MIN_USD_MINOR = 1000


@dataclass(frozen=True)
class Scale:
    name: str
    start: date
    end: date  # inclusive
    initial_sellers: int
    new_sellers_per_day: float
    mean_orders_per_seller_day: float
    # Rare events are dialed up at small scale so four months of CI data still
    # contains every case the tests must see (failed payouts, bad-debt aging).
    dispute_rate: float = 0.005
    seller_mean_lifetime_days: int = 320
    proc_a_payout_fail_rate: float = 0.010


SCALES: dict[str, Scale] = {
    "small": Scale("small", date(2025, 6, 1), date(2025, 9, 30), 60, 0.4, 0.35,
                   dispute_rate=0.020, seller_mean_lifetime_days=90,
                   proc_a_payout_fail_rate=0.030),
    "full": Scale("full", date(2024, 4, 1), date(2025, 9, 30), 900, 3.2, 0.42),
}

# Event rates (baseline, before any scenario overlay).
REFUND_RATE = 0.040
REFUND_PARTIAL_SHARE = 0.30
DISPUTE_WIN_RATE = 0.35
SUBSCRIPTION_PRODUCT_SHARE = 0.25
SUBSCRIPTION_CANCEL_RATE = 0.12
PLAN_CHANGE_RATE_PER_SELLER = 0.08
SELLER_PAYOUT_FAIL_RATE = 0.015
SELLER_PAYOUT_REVERSE_RATE = 0.005

# Routing: EU and GB buyers mostly go to processor B, everyone else mostly to A.
PROC_B_SHARE_EUROPE = 0.80
PROC_B_SHARE_OTHER = 0.05
