"""T-PLN-07 proves PLN-03: crossing week, top outflows, delayed inflows, facts strings."""

from __future__ import annotations

from datetime import date

from contracts.enums import Scenario
from contracts.testing.builders import make_forecast_inputs_tiny
from planning.service import Planner


def test_crunch_drivers() -> None:
    """T-PLN-07: crossing W2; BILL-2 5,000 is the main outflow; INV-1 is late money."""
    d = Planner().forecast(make_forecast_inputs_tiny(crunch=True)).drivers
    assert d.scenario == Scenario.BASE and d.crossing_week == 2
    assert d.top_outflows[0].ref_id == "bil_tiny_2"
    assert d.top_outflows[0].amount_cents == 500000
    assert [o.ref_id for o in d.top_outflows] == ["bil_tiny_2", "cogs"]
    assert len(d.top_outflows) <= 3
    assert [i.ref_id for i in d.delayed_inflows] == ["inv_tiny_1"]
    assert d.delayed_inflows[0].expected_date == date(2026, 10, 29)
    assert d.facts == {
        "week": "2",
        "week_start": "22 Oct 2026",
        "main_outflow": "bill bil_tiny_2 from cpt_tiny_mills",
        "main_outflow_amount": "$5,000.00",
        "delayed_count": "1",
        "delayed_total": "$600.00",
    }


def test_no_crossing_no_drivers() -> None:
    """T-PLN-07: no negative week in BASE or LOW -> empty drivers."""
    d = Planner().forecast(make_forecast_inputs_tiny()).drivers
    assert d.crossing_week is None and d.top_outflows == [] and d.facts == {}
