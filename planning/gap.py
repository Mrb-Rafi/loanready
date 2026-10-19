"""Recommended gap, needed term and going-concern flag (PRD/06 §5; PLN-04..PLN-07,
REG-ACC-07, CTL-FR-10).

- safety buffer = inputs.safety_buffer_cents, else one week of fixed costs =
  (rent + wages + utilities over the 13 BASE weeks) / 13;
- recommended gap = roundup_100(-min LOW closing + buffer) if LOW ever goes negative,
  else 0; base gap = roundup_100(max(0, -min BASE closing)) with no buffer;
- needed term = days from AS_OF to the end of the first week after the BASE crossing in
  which BASE closing is >= 0 again, clamped to [14, 91]; 91 if BASE never recovers;
- going concern flag = LOW closing negative in week 13, or BASE never recovers.
``roundup_100`` rounds up to the next whole 100.00 (10,000 cents).
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from contracts.dto_planning import ForecastInputs, ScenarioForecast

STEP = 10000
MIN_TERM, MAX_TERM = 14, 91


def roundup_100(cents: int | Decimal) -> int:
    """Round up to the next multiple of 10,000 cents (0 stays 0)."""
    value = Decimal(cents)
    if value <= 0:
        return 0
    return int((value / STEP).to_integral_value(rounding=ROUND_CEILING)) * STEP


def safety_buffer(inputs: ForecastInputs, base: ScenarioForecast) -> Decimal:
    if inputs.safety_buffer_cents is not None:
        return Decimal(inputs.safety_buffer_cents)
    fixed = sum(w.out_rent_cents + w.out_wages_cents + w.out_utilities_cents for w in base.weeks)
    return Decimal(fixed) / Decimal(len(base.weeks))


def base_gap(base: ScenarioForecast) -> int:
    return roundup_100(max(0, -base.min_closing_cents))


def recommended_gap(low: ScenarioForecast, buffer: Decimal) -> int:
    if low.first_negative_week is None:
        return 0
    return roundup_100(Decimal(-low.min_closing_cents) + buffer)


def recovery_week(base: ScenarioForecast) -> int | None:
    """First week after the crossing with closing >= 0 (None: never recovers)."""
    if base.first_negative_week is None:
        return None
    return next(
        (w.week for w in base.weeks if w.week > base.first_negative_week and w.closing_cents >= 0),
        None,
    )


def needed_term_days(base: ScenarioForecast, as_of_ordinal: int) -> int:
    if base.first_negative_week is None:
        return MIN_TERM
    k = recovery_week(base)
    if k is None:
        return MAX_TERM
    end = base.weeks[k - 1].end.toordinal()
    return min(MAX_TERM, max(MIN_TERM, end - as_of_ordinal))


def going_concern(base: ScenarioForecast, low: ScenarioForecast) -> bool:
    never_recovers = base.first_negative_week is not None and recovery_week(base) is None
    return low.weeks[-1].closing_cents < 0 or never_recovers
