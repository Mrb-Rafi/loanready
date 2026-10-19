"""Scenario parameters (PRD/06 §2, REG-AI-05: ranges, not certainty).

- weekly POS sales: LOW P25 of the last 12 weeks; BASE median of the last 8; HIGH P75 of 12.
- invoice not yet due: LOW due + P75 lateness; BASE due + median lateness; HIGH due date.
- invoice overdue at AS_OF: LOW max(AS_OF + 28, due + P75); BASE max(AS_OF + 14,
  due + median); HIGH AS_OF + 3.
- overdue more than 60 days: excluded in LOW and BASE; HIGH AS_OF + 3.
- no lateness history: LOW 30 days late; BASE 14 days late; HIGH on time.

Lateness values are clamped to >= 0 before percentiles and floored to whole days.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal

from contracts.dto_planning import InvoiceForecastItem
from contracts.enums import Scenario
from contracts.money import to_cents
from planning.stats import percentile

OVERDUE_EXCLUDED_DAYS = 60


def scenario_sales_cents(weekly_totals: list[int], scenario: Scenario) -> int:
    """Weekly sales for the scenario, rounded HALF_EVEN to whole cents via
    ``contracts.money.to_cents``. 0 when there is no history."""
    if not weekly_totals:
        return 0
    if scenario == Scenario.LOW:
        value = percentile(weekly_totals[-12:], 25)
    elif scenario == Scenario.BASE:
        value = percentile(weekly_totals[-8:], 50)
    else:
        value = percentile(weekly_totals[-12:], 75)
    return to_cents(value / Decimal(100))


def lateness_days(history: list[int], scenario: Scenario) -> int:
    hist = [max(0, d) for d in history]
    if scenario == Scenario.HIGH:
        return 0
    if not hist:
        return 30 if scenario == Scenario.LOW else 14
    p = 75 if scenario == Scenario.LOW else 50
    return int(percentile(hist, p).to_integral_value(rounding=ROUND_FLOOR))


def collection_date(inv: InvoiceForecastItem, scenario: Scenario, as_of: date) -> date | None:
    """Expected payment date, or None when the invoice is assumed not collected."""
    overdue_days = (as_of - inv.due_date).days
    late = timedelta(days=lateness_days(inv.lateness_history_days, scenario))
    if overdue_days <= 0:
        return inv.due_date + late
    if scenario == Scenario.HIGH:
        return as_of + timedelta(days=3)
    if overdue_days > OVERDUE_EXCLUDED_DAYS:
        return None
    floor = as_of + timedelta(days=28 if scenario == Scenario.LOW else 14)
    return max(floor, inv.due_date + late)


def clamp_cogs_ratio(ratio: Decimal) -> tuple[Decimal, bool]:
    """Clamp to [0, 0.95] (PLN-01); returns (ratio, clamped?)."""
    clamped = min(max(ratio, Decimal(0)), Decimal("0.95"))
    return clamped, clamped != ratio
