"""Annualised cost measures and totals from borrower cash flows (PRD/07 §4, CTL-FIN-02).

- ``nominal_apr = r * 365``: actuarial APR, periodic rate x periods per year, no compounding
  (US Regulation Z / commercial-disclosure "estimated APR" style; REG-LND-01, 02).
- ``ear = (1 + r) ** 365 - 1``: effective annual rate, compounded, actual/365 (EU APRC
  method; REG-LND-03). EAR is the headline "annualised cost".
- ``simple_annualised = total_cost / amount_received * 365 / term_days``: readability only,
  labelled "simple, not compounded".

``r`` is the daily IRR from ``financing.irr``. Rates are quantized to 10 dp (HALF_EVEN).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from contracts.dto_financing import CashFlowPoint
from contracts.money import MONEY_CTX
from financing.irr import daily_irr

DAYS_PER_YEAR = 365
RATE_QUANTUM = Decimal("1e-10")


def q_rate(x: Decimal) -> Decimal:
    """Quantize a rate to 10 dp with HALF_EVEN."""
    return x.quantize(RATE_QUANTUM, context=MONEY_CTX)


def nominal_apr(r: Decimal) -> Decimal:
    """``r * 365`` (no compounding)."""
    return q_rate(MONEY_CTX.multiply(r, DAYS_PER_YEAR))


def ear(r: Decimal) -> Decimal:
    """``(1 + r) ** 365 - 1`` (compounded daily, actual/365)."""
    return q_rate(MONEY_CTX.subtract(MONEY_CTX.power(MONEY_CTX.add(1, r), DAYS_PER_YEAR), 1))


def simple_annualised(total_cost_cents: int, received_cents: int, term_days: int) -> Decimal:
    """``total_cost / amount_received * 365 / term_days``; 0 when nothing is received."""
    if received_cents <= 0 or term_days <= 0:
        return Decimal(0)
    x = MONEY_CTX.divide(
        MONEY_CTX.multiply(Decimal(total_cost_cents), DAYS_PER_YEAR),
        MONEY_CTX.multiply(Decimal(received_cents), term_days),
    )
    return q_rate(x)


@dataclass(frozen=True)
class FlowMetrics:
    """Totals in int cents and rates in Decimal for one option."""

    amount_received_cents: int  # sum of + flows
    total_repaid_cents: int  # sum of |- flows|
    total_cost_cents: int  # repaid - received = -sum(flows)
    term_days: int  # day of the last flow
    daily_irr: Decimal
    nominal_apr: Decimal
    ear: Decimal
    simple_annualised: Decimal


def measure(flows: Sequence[CashFlowPoint]) -> FlowMetrics:
    """Every measure of PRD/07 §4 for one flow list."""
    received = sum(f.amount_cents for f in flows if f.amount_cents > 0)
    repaid = -sum(f.amount_cents for f in flows if f.amount_cents < 0)
    cost = repaid - received
    term = max(f.day for f in flows)
    r = daily_irr(flows)
    return FlowMetrics(
        amount_received_cents=received,
        total_repaid_cents=repaid,
        total_cost_cents=cost,
        term_days=term,
        daily_irr=q_rate(r),
        nominal_apr=nominal_apr(r),
        ear=ear(r),
        simple_annualised=simple_annualised(cost, received, term),
    )
