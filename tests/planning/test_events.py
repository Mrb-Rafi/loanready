"""T-PLN-04 proves PRD/06 §3 step 1: event uplift applies to every week the event overlaps
by at least one day; overlapping factors multiply."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from contracts.dto_planning import EventUplift
from contracts.enums import Scenario
from contracts.testing.builders import make_forecast_inputs_tiny
from planning.forecast import uplift
from planning.service import Planner


def ev(name: str, start: date, end: date, factor: str) -> EventUplift:
    return EventUplift(name=name, start=start, end=end, factor=Decimal(factor))


def test_overlap_rules() -> None:
    """T-PLN-04: one-day overlap counts; no overlap = 1; two events multiply."""
    w_start, w_end = date(2026, 10, 29), date(2026, 11, 4)
    assert uplift(
        [ev("a", date(2026, 11, 4), date(2026, 11, 10), "1.30")], w_start, w_end
    ) == Decimal("1.30")
    assert uplift([ev("a", date(2026, 11, 5), date(2026, 11, 10), "1.30")], w_start, w_end) == 1
    both = [
        ev("a", date(2026, 10, 30), date(2026, 10, 31), "1.15"),
        ev("b", date(2026, 11, 1), date(2026, 11, 2), "1.20"),
    ]
    assert uplift(both, w_start, w_end) == Decimal("1.15") * Decimal("1.20")


def test_uplift_changes_sales_and_cogs() -> None:
    """T-PLN-04: festival week (W4) sales x 1.30, COGS follows sales."""
    inputs = make_forecast_inputs_tiny().model_copy(
        update={"events": [ev("Festival", date(2026, 11, 5), date(2026, 11, 11), "1.30")]}
    )
    base = Planner().forecast(inputs).scenarios[Scenario.BASE]
    w4 = base.weeks[3]
    assert (w4.in_sales_cents, w4.out_cogs_cents) == (143000, 42900)
    assert base.weeks[2].in_sales_cents == 110000
    assert any("Festival" in a for a in base.assumptions)
