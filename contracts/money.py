"""Money helpers (CON-01).

Convention: money crosses module boundaries and is stored as ``int`` cents. Inside a
calculation use ``Decimal``. Rounding is ROUND_HALF_EVEN and happens only in
``to_cents``, ``q2`` and ``fmt``. Floats are rejected everywhere.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

CENT = Decimal("0.01")
HUNDRED = Decimal(100)

# Explicit context for heavy maths (IRR etc.); never mutate the global decimal context.
MONEY_CTX = Context(prec=34, rounding=ROUND_HALF_EVEN)

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CNY": "¥"}
_PARSE_RE = re.compile(r"^(-)?\s*([$€£¥])?\s*(-)?\s*([0-9][0-9,]*)(?:\.([0-9]{1,2}))?$")


def _reject_float(value: object) -> None:
    if isinstance(value, (float, bool)):
        raise TypeError(f"money must not be {type(value).__name__}; use Decimal, int or str")


def to_cents(amount: Decimal | int | str) -> int:
    """Convert an amount in currency units to int cents.

    Formula: cents = quantize(amount, 0.01, HALF_EVEN) * 100. ``int`` input is whole units
    (``to_cents(12) == 1200``). Floats and bools raise ``TypeError``.
    """
    _reject_float(amount)
    if isinstance(amount, int):
        return amount * 100
    if isinstance(amount, str):
        try:
            amount = Decimal(amount.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal amount: {amount!r}") from exc
    if not isinstance(amount, Decimal):
        raise TypeError(f"unsupported money type: {type(amount).__name__}")
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    return int(q2(amount) * HUNDRED)


def from_cents(cents: int) -> Decimal:
    """Exact Decimal with 2 dp: ``from_cents(123456) == Decimal("1234.56")``."""
    _reject_float(cents)
    if not isinstance(cents, int):
        raise TypeError("cents must be int")
    return (Decimal(cents) / HUNDRED).quantize(CENT)


def q2(x: Decimal) -> Decimal:
    """Quantize to 2 dp with ROUND_HALF_EVEN."""
    _reject_float(x)
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_EVEN, context=MONEY_CTX)


def fmt(cents: int, currency: str = "USD") -> str:
    """Display format: ``"$1,234.56"``; negatives ``"-$12.00"``; unknown currency ``"XYZ 1.00"``."""
    value = from_cents(cents)
    sign = "-" if value < 0 else ""
    body = f"{abs(value):,.2f}"
    symbol = _SYMBOLS.get(currency)
    if symbol is None:
        return f"{sign}{currency} {body}"
    return f"{sign}{symbol}{body}"


def parse(text: str) -> int:
    """Parse ``"$1,234.56"``, ``"1234.56"``, ``"-$12"`` into int cents; raise ``ValueError``.

    More than two decimal places is rejected (no silent rounding of user input).
    """
    if not isinstance(text, str):
        raise TypeError("parse expects str")
    match = _PARSE_RE.match(text.strip())
    if match is None:
        raise ValueError(f"not a money amount: {text!r}")
    neg1, _symbol, neg2, whole, frac = match.groups()
    if neg1 and neg2:
        raise ValueError(f"not a money amount: {text!r}")
    whole_digits = whole.replace(",", "")
    if "," in whole and not re.fullmatch(r"[0-9]{1,3}(,[0-9]{3})*", whole):
        raise ValueError(f"bad thousands separators: {text!r}")
    cents = int(whole_digits) * 100 + int((frac or "0").ljust(2, "0"))
    return -cents if (neg1 or neg2) else cents


def sum_cents(values: Iterable[int]) -> int:
    """Sum of int cents; rejects floats."""
    total = 0
    for v in values:
        _reject_float(v)
        if not isinstance(v, int):
            raise TypeError("sum_cents expects int cents")
        total += v
    return total


def pct(x: Decimal, dp: int = 2) -> str:
    """Rate as percent string: ``pct(Decimal("0.19564")) == "19.56%"`` (HALF_EVEN)."""
    _reject_float(x)
    q = (Decimal(x) * HUNDRED).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_EVEN)
    return f"{q}%"
