"""Money and rate display helpers (PRD/12 §1, UI-09).

Display only: every amount shown in the UI is DTO int cents formatted with
``contracts.money.fmt``; rates are ``Decimal`` formatted with ``contracts.money.pct``.
Nothing here computes money and nothing here uses floats.
"""

from __future__ import annotations

from decimal import Decimal

from contracts.money import fmt, pct
from contracts.money import parse as parse_money

NA = "n/a"


def money(cents: int | None, currency: str = "USD") -> str:
    """``123456 -> "$1,234.56"``; ``None -> "n/a"``."""
    return NA if cents is None else fmt(cents, currency)


def md(text: str) -> str:
    """Escape ``$`` for Streamlit markdown, which renders ``$...$`` pairs as LaTeX math."""
    return text.replace("$", r"\$")


def money_md(cents: int | None, currency: str = "USD") -> str:
    """``money`` for text rendered as markdown (captions, alerts, write, markdown)."""
    return md(money(cents, currency))


def signed_money(cents: int | None, currency: str = "USD") -> str:
    """Like ``money`` with an explicit ``+`` for positive values (cash-flow columns)."""
    if cents is None:
        return NA
    return ("+" if cents > 0 else "") + fmt(cents, currency)


def rate(value: Decimal | None, dp: int = 2) -> str:
    """``Decimal("0.19564") -> "19.56%"``; ``None -> "n/a"``."""
    return NA if value is None else pct(value, dp)


def share(part: int, whole: int) -> int:
    """Whole-number percent ``0..100`` of two int cents values, for progress bars only.

    Formula: ``min(100, max(0, part * 100 // whole))``; ``whole <= 0 -> 0``.
    """
    if whole <= 0:
        return 0
    return min(100, max(0, part * 100 // whole))


def parse_amount(text: str) -> int:
    """User input ``"$1,234.56"`` or ``"1234.56"`` to int cents (``ValueError`` if invalid).

    Only positive amounts are accepted from forms.
    """
    cents = parse_money(text)
    if cents <= 0:
        raise ValueError("amount must be greater than zero")
    return cents


def cents_to_input(cents: int) -> str:
    """Prefill text for an amount input: ``123456 -> "1234.56"`` (no symbol, no commas)."""
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"


__all__ = [
    "NA",
    "cents_to_input",
    "md",
    "money",
    "money_md",
    "parse_amount",
    "rate",
    "share",
    "signed_money",
]
