"""FX rates.

- Reference rates: a seeded random walk, quoted as units of currency per 1 USD
  (the way most public reference feeds quote USD/JPY 150.25). Published on
  business days only; weekend dates have no row.
- Processor rates: each processor converts at its own daily rate, which is
  the reference rate for that calendar day worsened by the processor's spread.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from generator.config import FX_OPENING, PROC_A_FX_SPREAD, PROC_B_FX_SPREAD
from generator.rng import stream

_RATE_DP = Decimal("0.000001")
_PROC_DP = Decimal("0.0000000001")


class FxRates:
    def __init__(self, seed: int, start: date, end: date) -> None:
        self.start, self.end = start, end
        # Every calendar day has an underlying market rate; only business days publish.
        self._daily: dict[tuple[date, str], Decimal] = {}
        rng = stream(seed, "fx")
        for ccy, (opening, vol) in FX_OPENING.items():
            level = float(opening)
            day = start
            while day <= end:
                self._daily[(day, ccy)] = Decimal(str(level)).quantize(_RATE_DP)
                level *= math.exp(rng.gauss(0.0, vol))
                day += timedelta(days=1)

    def market(self, day: date, ccy: str) -> Decimal:
        """Units of ccy per 1 USD on a calendar day (weekends included)."""
        return Decimal(1) if ccy == "USD" else self._daily[(day, ccy)]

    def published_rows(self) -> list[dict]:
        rows = []
        for (day, ccy), rate in sorted(self._daily.items()):
            if day.weekday() < 5:
                rows.append(
                    {"rate_date": day.isoformat(), "base_currency": "USD",
                     "quote_currency": ccy, "rate": f"{rate:.6f}"}
                )
        return rows

    def booking(self, day: date, ccy: str) -> Decimal:
        """Kiln's booking rate: the latest *published* rate on or before the day."""
        if ccy == "USD":
            return Decimal(1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        if day < self.start:
            day = self.start
            while day.weekday() >= 5:
                day += timedelta(days=1)
        return self._daily[(day, ccy)]

    def proc_a_usd_per_unit(self, day: date, ccy: str, spread: Decimal = PROC_A_FX_SPREAD) -> Decimal:
        return (Decimal(1) / self.market(day, ccy) * (1 - spread)).quantize(_PROC_DP)

    def proc_b_eur_per_unit(self, day: date, ccy: str, spread: Decimal = PROC_B_FX_SPREAD) -> Decimal:
        if ccy == "EUR":
            return Decimal(1)
        return (self.market(day, "EUR") / self.market(day, ccy) * (1 - spread)).quantize(_PROC_DP)
