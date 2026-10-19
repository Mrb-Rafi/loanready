"""Daily internal rate of return by bisection on ``Decimal`` (PRD/07 §4).

Flows are borrower-perspective ``CashFlowPoint``s: ``+`` received, ``-`` paid, ``day`` counted
from AS_OF (day 0). All maths runs in ``MONEY_CTX`` (prec 34, HALF_EVEN).

    NPV(r) = sum(amount_t / (1 + r) ** day_t)

Sign convention: for a borrower ``NPV(0) = -total_cost <= 0`` and NPV *increases* towards
the amount received as ``r`` grows. Bisection on the daily rate in ``[0, 1]``: if
``NPV(m) < 0`` the rate is too low (cost not yet discounted away), so ``lo = m``; else
``hi = m``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from contracts.dto_financing import CashFlowPoint
from contracts.errors import InvalidParams
from contracts.money import MONEY_CTX, from_cents

TOLERANCE = Decimal("1e-12")
MAX_ITERATIONS = 200
_ONE = Decimal(1)


def npv(flows: Sequence[CashFlowPoint], rate: Decimal) -> Decimal:
    """Net present value in currency units at daily ``rate`` (borrower perspective)."""
    base = MONEY_CTX.add(_ONE, rate)
    total = Decimal(0)
    for f in flows:
        disc = MONEY_CTX.power(base, f.day)
        total = MONEY_CTX.add(total, MONEY_CTX.divide(from_cents(f.amount_cents), disc))
    return total


def total_cost_cents(flows: Sequence[CashFlowPoint]) -> int:
    """``total_cost = sum(|payments|) - sum(receipts) = -sum(flows)``, in cents."""
    return -sum(f.amount_cents for f in flows)


def daily_irr(flows: Sequence[CashFlowPoint]) -> Decimal:
    """Daily IRR ``r`` with ``NPV(r) = 0``, by bisection to ``hi - lo < 1e-12``.

    Returns 0 when the total cost is 0. Raises ``InvalidParams`` for an empty flow list, a
    negative cost (borrower gains money) or a cost beyond 100% per day.
    """
    if not flows:
        raise InvalidParams("IRR needs at least one cash flow")
    cost = total_cost_cents(flows)
    if cost == 0:
        return Decimal(0)
    if cost < 0:
        raise InvalidParams("flows have a negative total cost; IRR is not defined here")
    lo, hi = Decimal(0), Decimal(1)
    if npv(flows, hi) < 0:
        raise InvalidParams("cost beyond 100% per day; IRR outside the bisection bracket")
    for _ in range(MAX_ITERATIONS):
        if hi - lo < TOLERANCE:
            break
        mid = MONEY_CTX.divide(MONEY_CTX.add(lo, hi), 2)
        if npv(flows, mid) < 0:
            lo = mid
        else:
            hi = mid
    return MONEY_CTX.divide(MONEY_CTX.add(lo, hi), 2)
