"""Books: journal, P&L by month, cash flow, balance sheet, reconciliation, receivables ageing
with ECL (PRD/12 §2 page 2). Plain tables (cut list item 11); framework shown in titles."""

from __future__ import annotations

import streamlit as st

from contracts.dto_books import AGEING_BUCKETS
from ui import state
from ui.components import feedback
from ui.components.money import money, money_md
from ui.components.tables import (
    ageing_df,
    buckets_df,
    journal_df,
    matches_df,
    statement_df,
    statement_periods,
)

app = state.get_app()
st.title("Books")
feedback.show_flashes()

view = state.cached(app, "books_view")
pnl, cfs, bs, recon = view["pnl"], view["cash_flow"], view["balance_sheet"], view["reconciliation"]
fw = pnl.framework.value.replace("_", " ")

tabs = st.tabs(["Journal", "P&L", "Cash flow", "Balance sheet", "Reconciliation", "Receivables"])
with tabs[0]:
    st.subheader(f"Journal ({fw})")
    entries = view["journal"]
    text = st.text_input("Filter (memo, account or entry id)", key="lr_journal_filter")
    df = journal_df(entries)
    if text and not df.empty:
        mask = df.apply(lambda r: r.astype(str).str.contains(text, case=False).any(), axis=1)
        df = df[mask]
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption(f"{len(entries)} entries.")
with tabs[1]:
    st.subheader(f"Profit and loss by month ({fw})")
    st.dataframe(statement_df(pnl.lines, pnl.periods), hide_index=True, width="stretch")
    st.write(f"Net profit for the period: **{money_md(pnl.total_net_profit_cents)}**")
with tabs[2]:
    st.subheader(f"Cash flow statement, {cfs.method} method ({fw})")
    st.caption(f"{cfs.start.isoformat()} to {cfs.end.isoformat()}")
    st.dataframe(
        statement_df(cfs.lines, statement_periods(cfs.lines)), hide_index=True, width="stretch"
    )
    st.markdown(
        f"- Opening cash: {money_md(cfs.opening_cash_cents)}\n"
        f"- Net operating: {money_md(cfs.net_operating_cents)}\n"
        f"- Net investing: {money_md(cfs.net_investing_cents)}\n"
        f"- Net financing: {money_md(cfs.net_financing_cents)}\n"
        f"- Closing cash: **{money_md(cfs.closing_cash_cents)}**"
    )
    if "indirect" in view:
        st.markdown("**Indirect-method reconciliation**")
        st.write(view["indirect"])
with tabs[3]:
    st.subheader(f"Balance sheet at {bs.as_of.isoformat()} ({fw})")
    st.dataframe(
        statement_df(bs.lines, statement_periods(bs.lines)), hide_index=True, width="stretch"
    )
    st.markdown(
        f"- Total assets: {money_md(bs.total_assets_cents)}\n"
        f"- Total liabilities: {money_md(bs.total_liabilities_cents)}\n"
        f"- Total equity: {money_md(bs.total_equity_cents)}"
    )
with tabs[4]:
    st.subheader(f"Bank reconciliation at {recon.as_of.isoformat()}")
    found = len(recon.matched) + len(recon.bank_only)
    matched_pct = "n/a" if not found else f"{len(recon.matched) * 100 // found}%"
    c1, c2, c3 = st.columns(3)
    c1.metric("Bank lines matched", matched_pct)
    c2.metric("Ledger cash", money(recon.ledger_cash_cents))
    c3.metric("Bank balance", money(recon.bank_balance_cents))
    if recon.is_reconciled:
        st.success(
            f"Reconciled ✓ difference {money_md(recon.difference_cents)} is explained by the "
            "reconciling items."
        )
    else:
        st.error(f"Not reconciled: difference {money_md(recon.difference_cents)}.")
    st.dataframe(matches_df(recon.matched), hide_index=True, width="stretch")
    st.write("Bank only: " + (", ".join(recon.bank_only) or "none"))
    st.write("Books only: " + (", ".join(recon.books_only) or "none"))
    if recon.duplicates:
        st.warning("Possible duplicates: " + "; ".join(" = ".join(g) for g in recon.duplicates))
    if recon.reconciling_items:
        st.dataframe(
            statement_df(recon.reconciling_items, statement_periods(recon.reconciling_items)),
            hide_index=True,
            width="stretch",
        )
with tabs[5]:
    ag = state.cached(app, "receivables")
    st.subheader(f"Receivables ageing at {ag.as_of.isoformat()} ({fw})")
    st.dataframe(
        buckets_df(ag.buckets_cents, ag.ecl_rates, AGEING_BUCKETS), hide_index=True, width="stretch"
    )
    st.dataframe(ageing_df(ag.rows), hide_index=True, width="stretch")
    st.write(f"Expected credit loss allowance: {money_md(ag.ecl_allowance_cents)}")
