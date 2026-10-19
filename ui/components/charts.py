"""Altair charts: forecast band, cap gauge, readiness gauge (PRD/12 §1, UI-10, UI-13).

Data handed to the charts is DTO int cents. The cents-to-dollars division for the axis
happens inside the Vega-Lite spec in the browser (``transform_calculate``), so Python never
turns money into a float. Every chart has a title; pages show a table alternative next to it.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from contracts.dto_planning import ForecastDTO
from contracts.enums import Scenario

DOLLAR_AXIS = alt.Axis(format="$,.0f", title="Closing cash")


def forecast_frame(forecast: ForecastDTO) -> pd.DataFrame:
    """One row per week with int cents closing balances for LOW, BASE and HIGH."""
    low = forecast.scenarios[Scenario.LOW].weeks
    base = forecast.scenarios[Scenario.BASE].weeks
    high = forecast.scenarios[Scenario.HIGH].weeks
    return pd.DataFrame(
        [
            {
                "week": b.week,
                "week_start": b.start.isoformat(),
                "low_cents": lo.closing_cents,
                "base_cents": b.closing_cents,
                "high_cents": hi.closing_cents,
            }
            for lo, b, hi in zip(low, base, high, strict=False)
        ]
    )


def forecast_band(forecast: ForecastDTO) -> alt.LayerChart:
    """BASE line inside the LOW–HIGH band, zero line, and the base crossing-week marker."""
    data = forecast_frame(forecast)
    base = alt.Chart(data).transform_calculate(
        low="datum.low_cents / 100",
        mid="datum.base_cents / 100",
        high="datum.high_cents / 100",
    )
    x = alt.X("week:O", title="Week", axis=alt.Axis(labelAngle=0))
    tooltip = [
        alt.Tooltip("week:O", title="Week"),
        alt.Tooltip("week_start:N", title="From"),
        alt.Tooltip("low:Q", title="Low", format="$,.2f"),
        alt.Tooltip("mid:Q", title="Base", format="$,.2f"),
        alt.Tooltip("high:Q", title="High", format="$,.2f"),
    ]
    band = base.mark_area(opacity=0.25, color="#4c78a8").encode(
        x=x, y=alt.Y("low:Q", axis=DOLLAR_AXIS), y2="high:Q", tooltip=tooltip
    )
    line = base.mark_line(point=True, color="#1f3b73").encode(
        x=x, y=alt.Y("mid:Q", axis=DOLLAR_AXIS), tooltip=tooltip
    )
    zero = (
        alt.Chart(pd.DataFrame({"zero": [0]}))
        .mark_rule(color="#c0392b", strokeDash=[4, 4])
        .encode(y="zero:Q")
    )
    layers: list[alt.Chart] = [band, line, zero]
    crossing = forecast.scenarios[Scenario.BASE].first_negative_week
    if crossing is not None:
        marker = pd.DataFrame({"week": [crossing], "label": [f"Base below zero: week {crossing}"]})
        layers.append(alt.Chart(marker).mark_rule(color="#c0392b").encode(x=alt.X("week:O")))
        layers.append(
            alt.Chart(marker)
            .mark_text(align="left", dx=4, dy=-6, color="#c0392b")
            .encode(x=alt.X("week:O"), text="label:N")
        )
    return alt.layer(*layers).properties(
        title="13-week cash forecast: base line, low to high band", height=320
    )


def gauge(used_cents: int, cap_cents: int, title: str) -> alt.LayerChart:
    """Horizontal bar of ``used`` against ``cap`` (both int cents)."""
    data = pd.DataFrame({"used_cents": [used_cents], "cap_cents": [cap_cents]})
    base = alt.Chart(data).transform_calculate(
        used="datum.used_cents / 100", cap="datum.cap_cents / 100"
    )
    axis = alt.Axis(format="$,.0f", title=None)
    track = base.mark_bar(color="#e0e0e0", size=22).encode(x=alt.X("cap:Q", axis=axis))
    fill = base.mark_bar(color="#e67e22", size=22).encode(
        x="used:Q",
        tooltip=[
            alt.Tooltip("used:Q", title="Used", format="$,.2f"),
            alt.Tooltip("cap:Q", title="Cap", format="$,.2f"),
        ],
    )
    return alt.layer(track, fill).properties(title=title, height=60)


def score_gauge(score: int, title: str = "Readiness score (0 to 100)") -> alt.LayerChart:
    data = pd.DataFrame({"score": [score], "max": [100]})
    track = (
        alt.Chart(data)
        .mark_bar(color="#e0e0e0", size=26)
        .encode(x=alt.X("max:Q", scale=alt.Scale(domain=[0, 100]), title=None))
    )
    colour = "#27ae60" if score >= 75 else "#e67e22" if score >= 50 else "#c0392b"
    fill = alt.Chart(data).mark_bar(color=colour, size=26).encode(x="score:Q")
    text = (
        alt.Chart(data)
        .mark_text(align="left", dx=6, fontWeight="bold")
        .encode(x="score:Q", text="score:Q")
    )
    return alt.layer(track, fill, text).properties(title=title, height=70)
