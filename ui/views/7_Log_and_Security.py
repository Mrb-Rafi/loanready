"""Log and security: audit log, chain check, CSV export, caps, kill switch, tier and control
tables, and the "try a blocked action" panel (PRD/12 §2 page 7; CTL-LOG-*, CTL-AC-07)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from contracts.enums import ActionType, Decision, Role
from contracts.errors import LoanReadyError
from ui import state
from ui.components import feedback
from ui.components.charts import gauge
from ui.components.money import cents_to_input, money_md, parse_amount, share
from ui.components.tables import audit_df, controls_df, counts_df, tiers_df

BLOCKED_DEMOS: dict[ActionType, tuple[str, dict[str, object]]] = {
    ActionType.ACCEPT_LOAN: (
        "Accept a loan offer",
        {"product_id": "mca_demo", "amount_cents": 500000},
    ),
    ActionType.CHANGE_LIMITS: (
        "Raise the daily payment limit",
        {"key": "cap_per_day_cents", "value": "99999999"},
    ),
    ActionType.PAY_NEW_PAYEE: (
        "Pay a payee never seen before",
        {
            "payee_name": "Unknown Supplies Ltd",
            "bank_account_ref": "SBX-9999-0000",
            "amount_cents": 250000,
        },
    ),
    ActionType.DISABLE_GATE: ("Turn off the permission gate", {}),
}
MONEY_CAPS = (
    ("cap_per_action_cents", "Per-action cap"),
    ("cap_per_day_cents", "Per-day cap"),
    ("cap_per_payee_week_cents", "Per-payee weekly cap"),
)

app = state.get_app()
st.title("Log and security")
feedback.show_flashes()
is_owner = state.session_role() == Role.OWNER

# --- audit log ---------------------------------------------------------------------------------
st.subheader("Audit log (hash-chained, append only)")
rows = state.cached(app, "audit_rows", None)
counts = state.cached(app, "audit_counts")
c1, c2 = st.columns([3, 1])
with c1:
    choices = [d.value for d in Decision]
    picked = st.multiselect("Filter by decision", choices, default=[], key="lr_log_filter")
    shown = [r for r in rows if not picked or r.decision.value in picked]
    st.dataframe(audit_df(shown), hide_index=True, width="stretch")
    st.caption(f"{len(shown)} of {len(rows)} rows, newest first.")
with c2:
    st.dataframe(counts_df(counts), hide_index=True, width="stretch")

b1, b2 = st.columns(2)
with b1:
    if st.button("Verify chain", key="lr_verify"):
        try:
            state.set_last("lr_chain_result", ("done", app.verify_chain()))
        except LoanReadyError as exc:
            st.error(feedback.error_message(exc))
    result = state.get_last("lr_chain_result")
    if result is not None:
        bad = result[1]
        if bad is None:
            st.success(f"Chain intact: every row hash checks out ({len(rows)} rows).")
        else:
            st.error(f"Chain BROKEN at row {bad}. The log was changed after it was written.")
with b2:
    if st.button("Export CSV", key="lr_export"):
        path = feedback.attempt(app.export_csv, state.export_csv_path())
        if path is not None:
            state.set_last("lr_export_path", str(path))
            state.flash("success", f"Exported the action log to {path}.")
            st.rerun()
    exported = state.get_last("lr_export_path")
    if exported:
        p = Path(exported)
        if p.exists():
            st.download_button(
                "Download action_log.csv",
                data=p.read_bytes(),
                file_name="action_log.csv",
                mime="text/csv",
                key="lr_export_download",
            )

# --- caps and kill switch ----------------------------------------------------------------------
st.divider()
st.subheader("Limits and kill switch")
caps = state.cached(app, "cap_usage")
dash = state.cached(app, "dashboard")
g1, g2 = st.columns(2)
with g1:
    st.altair_chart(gauge(caps.used_today_cents, caps.per_day_cents, "Paid today vs per-day cap"))
    st.progress(
        share(caps.used_today_cents, caps.per_day_cents),
        text=f"{money_md(caps.used_today_cents)} of {money_md(caps.per_day_cents)} per day",
    )
with g2:
    st.markdown(
        f"- Per-action cap: **{money_md(caps.per_action_cents)}**\n"
        f"- Per-day cap: **{money_md(caps.per_day_cents)}**\n"
        f"- Per-payee weekly cap: **{money_md(caps.per_payee_week_cents)}**\n"
        f"- Messages per customer per week: **{caps.msgs_per_customer_week}**"
    )

if is_owner:
    new_kill = st.toggle(
        "Kill switch (stops every send and payment)", value=bool(dash.kill_switch), key="lr_kill"
    )
    if new_kill != bool(dash.kill_switch) and feedback.attempt_ok(app.set_kill_switch, new_kill):
        state.flash(
            "warning" if new_kill else "success",
            "Kill switch ON: every send and payment is blocked."
            if new_kill
            else "Kill switch off.",
        )
        st.rerun()
    current = {
        "cap_per_action_cents": caps.per_action_cents,
        "cap_per_day_cents": caps.per_day_cents,
        "cap_per_payee_week_cents": caps.per_payee_week_cents,
    }
    with st.expander("Edit limits (owner only)"):
        with st.form("lr_caps_form"):
            inputs = {
                key: st.text_input(label, value=cents_to_input(current[key]))
                for key, label in MONEY_CAPS
            }
            msgs = st.number_input(
                "Messages per customer per week",
                min_value=0,
                max_value=50,
                value=int(caps.msgs_per_customer_week),
                step=1,
            )
            save = st.form_submit_button("Save limits")
        if save:
            changes: dict[str, str] = {}
            try:
                for key, _label in MONEY_CAPS:
                    cents = parse_amount(inputs[key])
                    if cents != current[key]:
                        changes[key] = str(cents)
            except ValueError as exc:
                st.error(f"Not a valid amount: {exc}")
                changes = {}
            else:
                if int(msgs) != caps.msgs_per_customer_week:
                    changes["cap_msgs_per_customer_week"] = str(int(msgs))
                done = [
                    k for k, v in changes.items() if feedback.attempt_ok(app.update_setting, k, v)
                ]
                if done and len(done) == len(changes):
                    state.flash("success", "Saved: " + ", ".join(sorted(done)))
                    st.rerun()
                elif not changes:
                    st.info("Nothing changed.")
else:
    st.markdown(
        f"Kill switch: **{'ON' if dash.kill_switch else 'off'}**. Only the owner can change "
        "the kill switch and the limits."
    )

# --- blocked actions ---------------------------------------------------------------------------
st.divider()
st.subheader("Try a blocked action")
st.caption(
    "These buttons ask the gate for actions LoanReady never allows. Each refusal is written to "
    "the audit log."
)
bcols = st.columns(len(BLOCKED_DEMOS))
for col, (atype, (label, params)) in zip(bcols, BLOCKED_DEMOS.items(), strict=True):
    with col:
        st.markdown(f"**{atype.value}**")
        st.caption(label)
        if st.button("Try it", key=f"lr_try_{atype.value}"):
            decision = feedback.attempt(app.try_blocked, atype, dict(params))
            if decision is not None:
                feedback.flash_decision(decision)
                st.rerun()

# --- tiers and controls ------------------------------------------------------------------------
st.divider()
t1, t2 = st.tabs(["Tier table", "Controls"])
with t1:
    st.caption("From code (governance tier table).")
    st.dataframe(tiers_df(state.cached(app, "tier_table")), hide_index=True, width="stretch")
with t2:
    st.caption("Controls and the rules they are designed around (from code).")
    st.dataframe(controls_df(state.cached(app, "controls_table")), hide_index=True, width="stretch")
