"""Scenario overlay. Every stochastic stage of the simulation passes its
baseline parameters through apply(); this module may return them unchanged
or adjusted. Kept separate so the baseline model reads cleanly on its own.
"""

from __future__ import annotations

from decimal import Decimal


def _ramp(progress: float, begin: float, top: float) -> float:
    if progress <= begin:
        return 1.0
    return 1.0 + (top - 1.0) * (progress - begin) / (1.0 - begin)


def apply(stage: str, params: dict, ctx: dict) -> dict:
    p = ctx.get("progress", 0.0)
    out = dict(params)

    if stage == "dispute" and ctx.get("buyer_country") == "BR":
        out["rate"] = params["rate"] * _ramp(p, 0.45, 7.0)

    elif stage == "seller":
        if 0.25 <= p <= 0.40 and params["plan_id"] == 1:
            out["segment"] = "k7"
            out["activity"] = params["activity"] * 3.0

    elif stage == "product" and ctx.get("segment") == "k7":
        out["price_usd_minor"] = ctx["rng"].randint(99, 299)

    elif stage == "fx" and ctx.get("processor") == "B" and ctx.get("currency") == "GBP":
        if p > 0.60:
            out["spread"] = Decimal("0.020")

    return out
