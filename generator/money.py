"""Integer minor-unit money helpers. Rounding is always banker's (half-even)."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from generator.config import MINOR_UNITS

_ONE = Decimal(1)


def round_half_even(value: Decimal) -> int:
    return int(value.quantize(_ONE, rounding=ROUND_HALF_EVEN))


def to_major(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-MINOR_UNITS[currency])


def format_major(minor: int, currency: str) -> str:
    """12345 USD -> '123.45'; 1234 JPY -> '1234'. How processor reports print money."""
    exp = MINOR_UNITS[currency]
    return f"{to_major(minor, currency):.{exp}f}"


def convert_minor(minor: int, from_ccy: str, to_ccy: str, to_per_from: Decimal) -> int:
    """Convert minor units using a rate quoted as units of to_ccy per 1 unit of from_ccy."""
    if from_ccy == to_ccy:
        return minor
    major = to_major(minor, from_ccy) * to_per_from
    return round_half_even(major.scaleb(MINOR_UNITS[to_ccy]))


def apply_bps(minor: int, bps: int) -> int:
    return round_half_even(Decimal(minor) * bps / 10000)
