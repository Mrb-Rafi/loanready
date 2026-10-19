"""LoanReady Streamlit entry point (PRD/12 §1, §2).

Run with ``python tasks.py ui`` (``LOANREADY_UI_FAKE=1`` for canned data). This file sets the
page config, draws the sidebar shown on every page (principal selector, kill-switch badge,
remote-AI badge and AI status, AS_OF date, synthetic-data banner) and routes to the pages
with ``st.navigation``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:  # `streamlit run ui/app.py` puts ui/ first on sys.path
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from contracts.services import AppFacade  # noqa: E402
from ui import state  # noqa: E402
from ui.components import feedback  # noqa: E402
from ui.components.badges import chip, on_off_badge, synthetic_banner  # noqa: E402
from ui.components.money import md, money  # noqa: E402

PAGES_DIR = Path(__file__).resolve().parent / "views"  # not "pages": see README


def _sidebar(app: AppFacade) -> None:
    sb = st.sidebar
    sb.title("LoanReady")
    roles = list(state.UI_ROLES)
    current = state.session_role()
    choice = sb.selectbox(
        "Acting as",
        roles,
        index=roles.index(current),
        format_func=lambda r: state.ROLE_LABELS[r],
        key="lr_role_select",
    )
    if choice != current:
        state.set_session_role(choice)
        state.ensure_principal(app)
        st.rerun()
    sb.caption(f"Principal: {app.principal().display_name}")

    dash = state.cached(app, "dashboard")
    health = state.cached(app, "health")
    ai = health.get("ai", {}) if isinstance(health, dict) else {}
    sb.markdown(on_off_badge("Kill switch", bool(dash.kill_switch), on_is_bad=True))
    if dash.kill_switch:
        sb.error("Kill switch is ON: nothing can be sent or paid.")
    sb.markdown(on_off_badge("Remote AI", bool(ai.get("remote_ai", False))))
    sb.markdown(chip(f"AS_OF {dash.as_of.isoformat()}", "blue"))
    with sb.expander("AI status"):
        st.write(f"Mode: {ai.get('mode', 'unknown')}")
        st.write("Available backends: " + (", ".join(ai.get("available", [])) or "none"))
        models = ai.get("models", {})
        if models:
            st.write("Models: " + ", ".join(f"{k}={v}" for k, v in models.items()))
        budget = ai.get("remote_budget")
        calls = ai.get("remote_calls_today", 0)
        if isinstance(budget, dict):
            total_cap = budget.get("max_usd_total", "off")
            st.write(
                f"Remote calls today: {budget.get('calls_today', calls)} of "
                f"{budget.get('max_calls_per_day', '?')}"
            )
            st.write(
                md(
                    f"OpenAI spend (estimate): ${budget.get('usd_today', '0')} today of "
                    f"${budget.get('max_usd_per_day', '?')}; "
                    f"${budget.get('usd_total', '0')} in total"
                    + (f" of ${total_cap}" if total_cap != "off" else "")
                )
            )
        else:
            st.write(f"Remote calls today: {calls}")
        routes = ai.get("routes")
        if isinstance(routes, dict) and routes:
            st.caption("Hybrid routing (task: backend)")
            for task, backend in routes.items():
                chain = " → ".join(backend) if isinstance(backend, list) else str(backend)
                st.write(f"{task}: {chain}")
    sandbox = health.get("sandbox", {}) if isinstance(health, dict) else {}
    problem = health.get("sandbox_problem") if isinstance(health, dict) else None
    if problem or (isinstance(sandbox, dict) and sandbox.get("status", "ok") != "ok"):
        sb.error(f"Sandbox problem: {problem or sandbox}")  # UI-16 (as reported by the facade)
    if state.is_fake():
        sb.warning("Demo data: running on FakeAppFacade (LOANREADY_UI_FAKE=1 or no orchestrator).")
    with sb:
        synthetic_banner()


def home() -> None:
    """Dashboard tiles (PRD/12 §2 Home)."""
    app = state.get_app()
    st.title("LoanReady dashboard")
    feedback.show_flashes()
    d = state.cached(app, "dashboard")
    st.caption(f"Business date (AS_OF): {d.as_of.isoformat()}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash today", money(d.cash_cents))
    c2.metric(
        "Base case first negative week",
        "none" if d.first_negative_week_base is None else f"week {d.first_negative_week_base}",
    )
    c3.metric("Recommended gap to cover", money(d.recommended_gap_cents))
    c4.metric("Readiness score", f"{d.readiness_score} / 100")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Pending approvals", d.pending_approvals)
    c6.metric("Confirm queue", d.confirm_queue)
    c7.metric("Blocked attempts", d.blocked_count)
    c8.metric("Audit chain", "intact" if d.chain_ok else "BROKEN")
    if not d.chain_ok:
        st.error("The audit chain failed verification. See Log and security.")
    if d.pending_approvals:
        st.page_link(
            str(PAGES_DIR / "4_Action_Plan.py"),
            label="Go to the approval queue",
            icon=":material/fact_check:",
        )
    st.divider()
    st.button(
        "Run Rina story",
        disabled=True,
        help="The guided demo stepper (ui/demo.py) is a SHOULD item and is not built yet. "
        "Click through the pages instead.",
    )


def main() -> None:
    st.set_page_config(page_title="LoanReady", page_icon=":material/savings:", layout="wide")
    app = state.get_app()
    _sidebar(app)
    pages = [
        st.Page(home, title="Home", icon=":material/dashboard:", default=True),
        st.Page(str(PAGES_DIR / "1_Capture.py"), title="Capture", icon=":material/upload:"),
        st.Page(str(PAGES_DIR / "2_Books.py"), title="Books", icon=":material/menu_book:"),
        st.Page(str(PAGES_DIR / "3_Forecast.py"), title="Forecast", icon=":material/show_chart:"),
        st.Page(
            str(PAGES_DIR / "4_Action_Plan.py"), title="Action plan", icon=":material/checklist:"
        ),
        st.Page(str(PAGES_DIR / "5_Financing.py"), title="Financing", icon=":material/payments:"),
        st.Page(
            str(PAGES_DIR / "6_Loan_Pack.py"), title="Loan pack", icon=":material/description:"
        ),
        st.Page(
            str(PAGES_DIR / "7_Log_and_Security.py"),
            title="Log and security",
            icon=":material/shield:",
        ),
    ]
    st.navigation(pages).run()


main()
