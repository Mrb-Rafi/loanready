"""Loan pack: generate (Tier 1), preview and download, submit to the pretend lender (Tier 2
via the approval dialog), lender receipt (PRD/12 §2 page 6; REG-LND-05)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from contracts.enums import ActionType, Decision, Tier
from ui import state
from ui.components import feedback
from ui.components.approval_dialog import review_button
from ui.components.badges import tier_badge
from ui.components.money import cents_to_input, money, parse_amount

app = state.get_app()
st.title("Loan pack")
st.caption(
    "A lender-ready PDF built from your books, forecast and readiness score. LoanReady prepares "
    "and submits documents only; it never accepts a loan. Not financial advice."
)
feedback.show_flashes()

plan = state.cached(app, "cash_plan")
fc = state.cached(app, "forecast")
req0 = plan.financing_request
default_amount = req0.amount_cents if req0 else (fc.recommended_gap_cents or 100000)
default_term = req0.term_days if req0 else max(1, fc.needed_term_days or 90)

st.markdown(tier_badge(Tier.T1_PROPOSE) + " Generating the pack is local only.")
with st.form("lr_pack_form"):
    c1, c2 = st.columns(2)
    amount_text = c1.text_input("Requested amount", value=cents_to_input(default_amount))
    term = c2.number_input(
        "Term (days)", min_value=1, max_value=1825, value=int(default_term), step=1
    )
    purpose = st.text_input("Purpose", value="Working capital", max_chars=120)
    generate = st.form_submit_button("Generate pack")
if generate:
    try:
        amount_cents = parse_amount(amount_text)
    except ValueError as exc:
        st.error(f"Not a valid amount: {exc}")
    else:
        pack = feedback.attempt(app.build_loan_pack, amount_cents, int(term), purpose.strip())
        if pack is not None:
            state.set_last(state.LAST_PACK, pack)
            state.set_last(state.LAST_SUBMISSION, None)
            state.flash("success", f"Loan pack {pack.pack_id} generated.")
            st.rerun()

pack = state.get_last(state.LAST_PACK)
if pack is None:
    st.info("No pack generated in this session yet.")
    st.stop()

st.subheader("Summary")
meta = pack.metadata
c1, c2, c3 = st.columns(3)
c1.metric("Requested", money(int(meta.get("requested_amount_cents", 0) or 0)))
c2.metric("Framework", str(meta.get("framework", "n/a")))
c3.metric("AS_OF", str(meta.get("as_of", "n/a")))
st.write(f"Business: {meta.get('business', 'n/a')}")
st.write("Sections: " + ", ".join(pack.sections))
st.caption(
    f"Pack {pack.pack_id}, SHA-256 {pack.sha256[:16]}…, "
    f"created {pack.created_at:%Y-%m-%d %H:%M UTC}"
)
path = Path(pack.path)
if path.exists():
    st.download_button(
        "Download loan pack PDF",
        data=path.read_bytes(),
        file_name=path.name,
        mime="application/pdf",
        key="lr_pack_download",
    )
else:
    st.warning("The PDF file is not on this computer any more. Generate the pack again.")

st.divider()
st.subheader("Submit to the pretend lender")
st.markdown(tier_badge(Tier.T2_APPROVE))
pending = [
    p
    for p in state.cached(app, "pending")
    if p.action.type == ActionType.SUBMIT_LOAN_PACK
    and p.action.params.get("pack_id") == pack.pack_id
]
if pending:
    st.info("Submission proposed and waiting for your approval.")
    review_button(app, pending[0])
elif st.button("Submit to lender", key="lr_pack_submit"):
    decision = feedback.attempt(app.propose_submit_pack, pack.pack_id)
    if decision is not None:
        feedback.flash_decision(decision)
        st.rerun()

sub = state.get_last(state.LAST_SUBMISSION)
if sub is not None:
    st.subheader("Lender receipt")
    if sub.decision == Decision.EXECUTED and sub.result is not None:
        st.success(f"Receipt ID: {sub.result.sbx_ref}")
        status = sub.result.result.get("status")
        missing = sub.result.result.get("missing_sections") or []
        st.write(f"Lender status: {status or 'n/a'}")
        st.write("Missing sections: " + (", ".join(missing) if missing else "none"))
    else:
        feedback.show_decision(sub)
