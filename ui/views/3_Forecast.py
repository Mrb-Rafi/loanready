"""13-week cash forecast: LOW–HIGH band around BASE, zero line, crossing week, weekly table,
drivers sentence, assumptions (PRD/12 §2 page 3; UI-09, UI-10; REG-AI-05)."""

from __future__ import annotations

import streamlit as st

from contracts.enums import Scenario
from ui import state
from ui.components import feedback
from ui.components.badges import show_ai_text
from ui.components.charts import forecast_band
from ui.components.money import money_md
from ui.components.tables import band_df, weeks_df

app = state.get_app()
st.title("Cash forecast (13 weeks)")
feedback.show_flashes()

fc = state.cached(app, "forecast")
low, base, high = (fc.scenarios[s] for s in (Scenario.LOW, Scenario.BASE, Scenario.HIGH))


def _neg(week: int | None) -> str:
    return "stays above zero" if week is None else f"below zero in week {week}"


c1, c2, c3 = st.columns(3)
c1.metric(
    "Low case", _neg(low.first_negative_week), help=f"Lowest: {money_md(low.min_closing_cents)}"
)
c2.metric(
    "Base case", _neg(base.first_negative_week), help=f"Lowest: {money_md(base.min_closing_cents)}"
)
c3.metric(
    "High case", _neg(high.first_negative_week), help=f"Lowest: {money_md(high.min_closing_cents)}"
)
st.caption(
    f"Lowest closing cash: low {money_md(low.min_closing_cents)} (week {low.min_closing_week}), "
    f"base {money_md(base.min_closing_cents)} (week {base.min_closing_week}), "
    f"high {money_md(high.min_closing_cents)} (week {high.min_closing_week}). "
    "A range, not a promise: every figure rests on the assumptions below."
)
if fc.going_concern_flag:
    st.warning("Cash stays negative for a long stretch in the base case (going-concern flag).")

st.altair_chart(forecast_band(fc), width="stretch")
with st.expander("Chart as a table (low, base, high closing cash)"):
    st.dataframe(band_df(low.weeks, base.weeks, high.weeks), hide_index=True, width="stretch")

st.subheader("Why")
show_ai_text(state.cached(app, "forecast_explanation"))
drv = fc.drivers
d1, d2 = st.columns(2)
with d1:
    st.markdown("**Largest outflows**")
    if not drv.top_outflows:
        st.caption("None listed.")
    for item in drv.top_outflows:
        st.write(f"Week {item.week}: {item.label}, {money_md(item.amount_cents)}")
with d2:
    st.markdown("**Customer payments expected late**")
    if not drv.delayed_inflows:
        st.caption("None listed.")
    for item in drv.delayed_inflows:
        st.write(
            f"{item.label}: {money_md(item.amount_cents)} expected "
            f"{item.expected_date.isoformat()} (week {item.week})"
        )
st.caption(
    f"Gap to cover (recommended, includes safety buffer): {money_md(fc.recommended_gap_cents)}; "
    f"base gap {money_md(fc.base_gap_cents)}; needed for about {fc.needed_term_days} days."
)

with st.expander("Based on assumptions", expanded=False):
    for name, sc in (("Low", low), ("Base", base), ("High", high)):
        st.markdown(f"**{name} case**")
        for a in sc.assumptions:
            st.write(f"- {a}")
    st.caption(f"Inputs hash: {fc.inputs_hash[:16]}…")

excluded = state.cached(app, "excluded_bills")
st.markdown("**Excluded pending verification**")
if excluded:
    for b in excluded:
        st.write(
            f"- {b.number}: {money_md(b.outstanding_cents)} due {b.due_date.isoformat()}. "
            "The bill asks for payment to a bank account that differs from the one on file, "
            "so it is left out of the forecast until the supplier is verified."
        )
else:
    st.caption("Nothing is excluded.")

st.subheader("Weekly detail")
pick = st.radio(
    "Scenario",
    [Scenario.BASE, Scenario.LOW, Scenario.HIGH],
    horizontal=True,
    key="lr_fc_sc",
    format_func=lambda s: s.value.title(),
)
st.dataframe(weeks_df(fc.scenarios[pick].weeks), hide_index=True, width="stretch")
