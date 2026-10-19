"""``Planner``: implements ``ForecastService`` and ``CashPlanService`` (PRD/06).

Pure and deterministic: no DB, no clock, no randomness; AS_OF comes from the inputs.
Constructed with no arguments (PRD/11 §2).
"""

from __future__ import annotations

from contracts.canonical import params_hash
from contracts.dto_planning import CashPlanDTO, ForecastDTO, ForecastInputs
from contracts.enums import Scenario
from planning import cashplan, gap
from planning.drivers import drivers
from planning.forecast import run_scenario


class Planner:
    def forecast(self, inputs: ForecastInputs) -> ForecastDTO:
        """LOW / BASE / HIGH 13-week forecasts, drivers, gaps, term and going-concern flag.
        ``inputs_hash = params_hash(inputs)`` (PLN-02)."""
        scenarios = {
            s: run_scenario(inputs, s) for s in (Scenario.LOW, Scenario.BASE, Scenario.HIGH)
        }
        base, low = scenarios[Scenario.BASE], scenarios[Scenario.LOW]
        return ForecastDTO(
            as_of=inputs.as_of,
            scenarios=scenarios,
            drivers=drivers(inputs, scenarios),
            recommended_gap_cents=gap.recommended_gap(low, gap.safety_buffer(inputs, base)),
            base_gap_cents=gap.base_gap(base),
            needed_term_days=gap.needed_term_days(base, inputs.as_of.toordinal()),
            going_concern_flag=gap.going_concern(base, low),
            inputs_hash=params_hash(inputs),
        )

    def plan(
        self, inputs: ForecastInputs, forecast: ForecastDTO, avg_daily_card_sales_cents: int
    ) -> CashPlanDTO:
        return cashplan.plan(inputs, forecast, avg_daily_card_sales_cents)
