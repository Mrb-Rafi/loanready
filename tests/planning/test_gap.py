"""T-PLN-06 proves PLN-04..PLN-07, CTL-FR-10, REG-ACC-07: gap round-up, safety buffer,
needed term, going-concern flag (crunch-variant numbers of PRD/15 §3.1)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from contracts.testing.builders import make_forecast_inputs_tiny
from planning.gap import roundup_100
from planning.service import Planner


def test_roundup_100() -> None:
    """T-PLN-06: up to the next whole 100.00; zero and negatives give 0."""
    assert [roundup_100(c) for c in (0, -5, 1, 10000, 10001, 45000)] == [
        0,
        0,
        10000,
        10000,
        20000,
        50000,
    ]
    assert roundup_100(Decimal("63538.46")) == 70000


def test_crunch_gaps_and_term() -> None:
    """T-PLN-06: base gap 500.00; buffer 1,500/13 = 115.38; recommended 700.00; term 21."""
    f = Planner().forecast(make_forecast_inputs_tiny(crunch=True))
    assert f.base_gap_cents == 50000
    assert f.recommended_gap_cents == 70000
    assert f.needed_term_days == 21
    assert not f.going_concern_flag


def test_explicit_buffer() -> None:
    """T-PLN-06 / PLN-04: an explicit safety buffer replaces the automatic one."""
    inputs = make_forecast_inputs_tiny(crunch=True).model_copy(update={"safety_buffer_cents": 0})
    assert Planner().forecast(inputs).recommended_gap_cents == 60000


def test_no_gap_minimum_term() -> None:
    """T-PLN-06: without a crossing the gaps are 0 and the term is the 14-day minimum."""
    f = Planner().forecast(make_forecast_inputs_tiny())
    assert (f.base_gap_cents, f.recommended_gap_cents, f.needed_term_days) == (0, 0, 14)


def test_going_concern_when_never_recovers() -> None:
    """T-PLN-06 / PLN-06, PLN-07: a gap BASE never recovers from -> term 91, flag set."""
    from contracts.dto_planning import BillForecastItem

    big = BillForecastItem(
        bill_id="huge",
        supplier_id="s",
        due_date=date(2026, 10, 20),
        outstanding_cents=10_000_000,
        category="5000",
        late_penalty=True,
        essential=False,
        grace_days=0,
    )
    inputs = make_forecast_inputs_tiny()
    inputs = inputs.model_copy(update={"open_bills": [*inputs.open_bills, big]})
    f = Planner().forecast(inputs)
    assert f.needed_term_days == 91 and f.going_concern_flag
