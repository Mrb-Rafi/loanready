"""Action plan: COLLECT -> DELAY -> BORROW steps, Tier 1 drafts, approval queue, supplier
payment form (PRD/12 §2 page 4; UI-07, UI-09)."""

from __future__ import annotations

import streamlit as st

from contracts.enums import Lever, Tier
from ui import state
from ui.components import feedback
from ui.components.approval_dialog import review_button
from ui.components.badges import draft_label, tier_badge
from ui.components.money import money, money_md
from ui.components.tables import display_party, proposals_df

LEVER_TEXT = {
    Lever.COLLECT: "1. COLLECT what customers owe",
    Lever.DELAY: "2. DELAY bills that allow it",
    Lever.BORROW: "3. BORROW the rest, cheapest first",
}

app = state.get_app()
st.title("Action plan")
feedback.show_flashes()

plan = state.cached(app, "cash_plan")
st.subheader("Closing the cash gap, cheapest lever first")
st.caption(
    f"Gap to close: {money_md(plan.initial_gap_cents)}. Remaining after the plan: "
    f"{money_md(plan.remaining_gap_cents)}."
)
by_lever = {s.lever: s for s in plan.steps}
cols = st.columns(3)
for col, lever in zip(cols, (Lever.COLLECT, Lever.DELAY, Lever.BORROW), strict=True):
    with col.container(border=True):
        st.markdown(f"**{LEVER_TEXT[lever]}**")
        step = by_lever.get(lever)
        if step is None:
            st.write("Not needed for this gap.")
            continue
        st.metric("Closes", money(step.closed_cents))
        st.write(f"Remaining after this step: {money_md(step.gap_after_cents)}")
        if step.item_refs:
            st.caption("Items: " + ", ".join(step.item_refs))

st.divider()
st.subheader("Drafts (Tier 1: nothing is sent until you approve)")
st.markdown(tier_badge(Tier.T1_PROPOSE))
c1, c2 = st.columns(2)
if c1.button("Draft payment reminders", key="lr_draft_reminders"):
    out = feedback.attempt(app.draft_reminders)
    if out is not None:
        state.flash("info", f"{len(out)} reminder draft(s) created.")
        st.rerun()
if c2.button("Draft delay requests", key="lr_draft_delays"):
    out = feedback.attempt(app.draft_delay_requests)
    if out is not None:
        state.flash("info", f"{len(out)} delay-request draft(s) created.")
        st.rerun()

drafts = state.cached(app, "drafts")
if not drafts:
    st.caption("No drafts yet.")
for d in drafts:
    with st.container(border=True):
        st.markdown(
            f"**{d.kind.value.replace('_', ' ').title()}** to {display_party(d.to_ref)} "
            f"by {d.channel.value} &nbsp; {draft_label(d.generated_by, d.ai_label)}"
        )
        st.markdown(f"Subject: {d.subject}")
        with st.expander("Preview message"):
            st.text(d.body)
            st.caption(f"Body SHA-256: {d.body_sha256[:16]}… (bound into the approval)")
        if d.status == "sent":
            st.success(f"Sent. Sandbox reference: {d.sbx_ref}")
        elif state.was_proposed(d.draft_id):
            st.caption("Sending proposed: see the approval queue below.")
        elif st.button("Propose sending", key=f"lr_propose_{d.draft_id}"):
            decision = feedback.attempt(app.propose_send, d.draft_id)
            if decision is not None:
                state.mark_proposed(d.draft_id)
                feedback.flash_decision(decision)
                st.rerun()

st.divider()
st.subheader("Approval queue (Tier 2)")
pending = state.cached(app, "pending")
if not pending:
    st.caption("Nothing is waiting for approval.")
else:
    st.dataframe(proposals_df(pending), hide_index=True, width="stretch")
    for p in pending:
        with st.container(border=True):
            s = p.summary
            st.markdown(
                f"**{s.action_type.value}**: {money_md(s.amount_cents, s.currency)} to "
                f"{display_party(s.counterparty_name) or 'n/a'} &nbsp; {tier_badge(s.tier)}"
            )
            st.caption(s.reason)
            review_button(app, p)

st.divider()
st.subheader("Pay a supplier bill")
st.caption(
    "Choose an open bill and a value date. LoanReady proposes the payment to the payee and "
    "account already on file; you approve it in the queue above."
)
bill_ids = [b.id for b in state.cached(app, "open_bills")]
fc = state.cached(app, "forecast")
for item in fc.drivers.top_outflows:
    if item.ref_id not in bill_ids and item.ref_id.startswith("bil"):
        bill_ids.append(item.ref_id)
for draft in plan.drafts:
    bid = draft.params.get("bill_id")
    if isinstance(bid, str) and bid not in bill_ids:
        bill_ids.append(bid)
with st.form("lr_pay_form"):
    other = "Type a bill id"
    choice = st.selectbox("Open bill", [*bill_ids, other], key="lr_pay_bill")
    typed = st.text_input("Bill id (if not in the list)", key="lr_pay_bill_typed")
    value_date = st.date_input("Value date", value=fc.as_of, min_value=fc.as_of)
    submitted = st.form_submit_button("Propose payment")
if submitted:
    bill_id = typed.strip() if choice == other else choice
    if not bill_id:
        st.error("Choose a bill or type its id.")
    else:
        decision = feedback.attempt(app.propose_supplier_payment, bill_id, value_date)
        if decision is not None:
            feedback.flash_decision(decision)
            st.rerun()
