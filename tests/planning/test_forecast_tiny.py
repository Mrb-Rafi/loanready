"""T-PLN-02 proves PLN step 1-7, PRD-13: the tiny-fixture 13-week tables of PRD/15 §3.1."""

from __future__ import annotations

from contracts.enums import Scenario
from contracts.testing.builders import make_forecast_inputs_tiny
from planning.service import Planner

LOW = [3745, 4480, 4715, 6050, 6785, 7520, 7755, 8490, 9225, 9960, 10695, 10930, 11665]
BASE = [3780, 4550, 5420, 6190, 6960, 7730, 8000, 8770, 9540, 10310, 11080, 11350, 12120]
HIGH = [4415, 5220, 5525, 6330, 7135, 7940, 8245, 9050, 9855, 10660, 11465, 11770, 12575]


def _closings(f: object, s: Scenario) -> list[int]:
    return [w.closing_cents for w in f.scenarios[s].weeks]  # type: ignore[attr-defined]


def test_golden_tables() -> None:
    """T-PLN-02: LOW / BASE / HIGH closings for all 13 weeks, exact to the cent."""
    f = Planner().forecast(make_forecast_inputs_tiny())
    assert _closings(f, Scenario.LOW) == [x * 100 for x in LOW]
    assert _closings(f, Scenario.BASE) == [x * 100 for x in BASE]
    assert _closings(f, Scenario.HIGH) == [x * 100 for x in HIGH]
    assert all(f.scenarios[s].first_negative_week is None for s in Scenario)
    assert f.base_gap_cents == 0 and f.recommended_gap_cents == 0


def test_rows_add_up_and_components() -> None:
    """T-PLN-02: sales 1,100 BASE; COGS 330; INV-1 in W3; BILL-1 in W1; rent W3, W7, W12."""
    base = Planner().forecast(make_forecast_inputs_tiny()).scenarios[Scenario.BASE]
    opening = 381000
    for w in base.weeks:
        assert w.opening_cents == opening
        out = (
            w.out_cogs_cents
            + w.out_bills_cents
            + w.out_rent_cents
            + w.out_wages_cents
            + w.out_utilities_cents
            + w.out_loan_cents
            + w.out_other_cents
        )
        assert w.net_cents == w.in_sales_cents + w.in_collections_cents - out
        assert w.closing_cents == opening + w.net_cents
        opening = w.closing_cents
        assert (w.in_sales_cents, w.out_cogs_cents) == (110000, 33000)
    assert [w.week for w in base.weeks if w.in_collections_cents] == [3]
    assert [w.week for w in base.weeks if w.out_bills_cents] == [1]
    assert [w.week for w in base.weeks if w.out_rent_cents] == [3, 7, 12]


def test_crunch_variant() -> None:
    """T-PLN-02 crunch: BASE W2 -450.00, W3 420.00; LOW W2 -520, W3 -285, W4 1,050."""
    f = Planner().forecast(make_forecast_inputs_tiny(crunch=True))
    base, low = _closings(f, Scenario.BASE), _closings(f, Scenario.LOW)
    assert (base[1], base[2]) == (-45000, 42000)
    assert (low[1], low[2], low[3]) == (-52000, -28500, 105000)
    assert f.scenarios[Scenario.BASE].first_negative_week == 2
    assert f.scenarios[Scenario.LOW].min_closing_cents == -52000
