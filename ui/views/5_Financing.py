"""Financing comparison and readiness (PRD/12 §2 page 5; UI-11; REG-LND-05, REG-LND-06,
REG-AI-06; CTL-FIN-03).

Options are sorted by effective annual rate (EAR) computed by code; the UI never ranks,
recommends or computes cost itself.
"""

from __future__ import annotations

import streamlit as st

from contracts.enums import ProductKind
from contracts.errors import LoanReadyError
from ui import state
from ui.components import feedback
from ui.components.badges import band_badge, show_ai_text
from ui.components.charts import score_gauge
from ui.components.money import cents_to_input, money_md, parse_amount, rate
from ui.components.tables import options_df, readiness_df, sort_by_ear

HEADER = "LoanReady compares costs. It does not lend or arrange loans. Not financial advice."

app = state.get_app()
st.title("Financing options")
st.warning(HEADER)
feedback.show_flashes()

plan = state.cached(app, "cash_plan")
fc = state.cached(app, "forecast")
req0 = plan.financing_request
default_amount = req0.amount_cents if req0 else (fc.recommended_gap_cents or 100000)
default_term = req0.term_days if req0 else max(1, fc.needed_term_days or 30)

with st.form("lr_fin_form"):
    c1, c2 = st.columns(2)
    amount_text = c1.text_input("Amount needed", value=cents_to_input(default_amount))
    term = c2.number_input(
        "Term (days)", min_value=1, max_value=1825, value=int(default_term), step=1
    )
    st.form_submit_button("Compare")
try:
    amount_cents = parse_amount(amount_text)
except ValueError as exc:
    st.error(f"Not a valid amount ({exc}); showing the plan amount instead.")
    amount_cents = default_amount

try:
    cmp_ = state.cached(app, "financing", amount_cents, int(term))
except LoanReadyError as exc:
    st.error(feedback.error_message(exc))
    st.stop()

st.caption(
    f"Request: {money_md(cmp_.request.amount_cents)} for {cmp_.request.term_days} days. "
    f"Catalogue dated {cmp_.catalogue_as_of.isoformat()}, source: {cmp_.catalogue_source}."
)
st.dataframe(
    options_df(cmp_.options, cmp_.cheapest_id, cmp_.most_expensive_id),
    hide_index=True,
    width="stretch",
)
by_id = {o.product_id: o for o in cmp_.options}
cheap = by_id.get(cmp_.cheapest_id or "")
dear = by_id.get(cmp_.most_expensive_id or "")
if cheap:
    st.success(f"Cheapest by EAR: {cheap.name} ({rate(cheap.ear)}).")
if dear:
    st.error(f"Most expensive by EAR: {dear.name} ({rate(dear.ear)}).")
for o in cmp_.options:
    if o.kind == ProductKind.MERCHANT_CASH_ADVANCE:
        st.info(
            f"{o.name} is a merchant cash advance: a fixed fee repaid from a share of daily card "
            f"sales. Over this term the fee works out at an effective annual rate of "
            f"{rate(o.ear)} (total cost {money_md(o.total_cost_cents)})."
        )

st.subheader("Explanation")
show_ai_text(state.cached(app, "financing_explanation"))

st.subheader("Disclosures")
for o in sort_by_ear(cmp_.options):
    d = o.disclosure
    with st.expander(f"{o.name} ({o.provider})"):
        st.markdown(
            f"- Funding provided: {money_md(d.funding_provided_cents)}\n"
            f"- Finance charge: {money_md(d.finance_charge_cents)}\n"
            f"- Total repayment: {money_md(d.total_repayment_cents)}\n"
            f"- Estimated APR: {rate(d.estimated_apr)}; EAR: {rate(d.ear)}\n"
            f"- Payments: {d.number_of_payments} x {money_md(d.payment_amount_cents)} "
            f"({d.payment_frequency}) over {d.term_days} days\n"
            f"- Prepayment: {d.prepayment_note}\n"
            f"- Collateral: {d.collateral_note}\n"
            f"- Disclosure items designed around: {', '.join(d.regimes) or 'n/a'}"
        )
        if not o.eligible:
            st.warning("Not eligible: " + "; ".join(o.ineligibility_reasons))
for n in cmp_.notices:
    st.caption(n)

st.divider()
st.subheader("Loan readiness")
ready = state.cached(app, "readiness")
g1, g2 = st.columns([2, 1])
with g1:
    st.altair_chart(score_gauge(ready.score), width="stretch")
with g2:
    st.metric("Score", f"{ready.score} / 100")
    st.markdown(band_badge(ready.band))
    st.caption("DSCR: " + ("n/a (no debt)" if ready.dscr is None else str(ready.dscr)))
st.dataframe(readiness_df(ready.components), hide_index=True, width="stretch")
st.markdown("**How to improve**")
for imp in ready.improvements:
    st.write(f"- {imp.text} (+{imp.points_gain} points)")
st.caption(ready.disclaimer)
