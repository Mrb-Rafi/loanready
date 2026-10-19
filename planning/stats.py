"""Deterministic Decimal statistics (PRD/06 §2)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal

from contracts.money import MONEY_CTX


def percentile(values: Sequence[int | Decimal], p: int | Decimal) -> Decimal:
    """Linear interpolation (numpy "linear"): sorted x[0..n-1], h = (n - 1) p / 100,
    result x[floor h] + (h - floor h)(x[floor h + 1] - x[floor h]). Raises on empty input."""
    xs = sorted(Decimal(v) for v in values)
    if not xs:
        raise ValueError("percentile of an empty sequence")
    h = Decimal(len(xs) - 1) * Decimal(p) / Decimal(100)
    lo = int(h.to_integral_value(rounding=ROUND_FLOOR))
    if lo + 1 >= len(xs):
        return xs[lo]
    return xs[lo] + (h - lo) * (xs[lo + 1] - xs[lo])


def median(values: Sequence[int | Decimal]) -> Decimal:
    return percentile(values, 50)


def mean(values: Sequence[int | Decimal]) -> Decimal:
    if not values:
        raise ValueError("mean of an empty sequence")
    return sum((Decimal(v) for v in values), Decimal(0)) / Decimal(len(values))


def coefficient_of_variation(values: Sequence[int | Decimal]) -> Decimal:
    """Population standard deviation / mean (0 when the mean is 0)."""
    m = mean(values)
    if m == 0:
        return Decimal(0)
    var = sum(((Decimal(v) - m) ** 2 for v in values), Decimal(0)) / Decimal(len(values))
    return var.sqrt(MONEY_CTX) / abs(m)
