"""Streamlit AppTest checks of the UI against FakeAppFacade (PRD/12 §6, PRD/15 §4.10)."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from contracts.enums import Role
from tests.ui.conftest import APP_FILE, TIMEOUT

PAGES = [
    "views/1_Capture.py",
    "views/2_Books.py",
    "views/3_Forecast.py",
    "views/4_Action_Plan.py",
    "views/5_Financing.py",
    "views/6_Loan_Pack.py",
    "views/7_Log_and_Security.py",
]


def _texts(at: AppTest) -> str:
    parts: list[str] = []
    for kind in ("markdown", "caption", "info", "warning", "error", "success", "metric"):
        for el in getattr(at, kind):
            parts.append(str(getattr(el, "value", "")))
            parts.append(str(getattr(el, "label", "")))
    return "\n".join(parts).replace(r"\$", "$")  # markdown escapes $ (LaTeX)


def _start(role: Role = Role.OWNER) -> AppTest:
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT)
    at.session_state["lr_role"] = role
    at.run()
    assert not at.exception, at.exception
    return at


def test_home_renders() -> None:
    """T-UI-01 proves PRD/12 §2 Home: dashboard tiles render with fakes."""
    at = _start()
    labels = {m.label for m in at.metric}
    assert {"Cash today", "Readiness score", "Pending approvals", "Audit chain"} <= labels
    assert "$3,810.00" in _texts(at)


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders(page: str) -> None:
    """T-UI-01 proves PRD/12 §7: every page renders without exception with fakes."""
    at = _start()
    at.switch_page(page)
    at.run()
    assert not at.exception, at.exception
    assert [s.label for s in at.sidebar.selectbox] == ["Acting as"]  # app.py frame ran


def test_financing_header_and_notices() -> None:
    """T-UI-01 proves UI-11, REG-LND-05/06: not-advice header on the financing page."""
    at = _start()
    at.switch_page("views/5_Financing.py").run()
    text = _texts(at)
    assert "It does not lend or arrange loans. Not financial advice." in text
    assert "Cheapest by EAR" in text and "Most expensive by EAR" in text
    assert "AI-generated explanation of figures calculated by LoanReady" in text


def test_forecast_shows_range_and_assumptions() -> None:
    """T-UI-01 proves UI-10, REG-AI-05: low/base/high range and an assumptions expander."""
    at = _start()
    at.switch_page("views/3_Forecast.py").run()
    assert {"Low case", "Base case", "High case"} <= {m.label for m in at.metric}
    assert any(e.label == "Based on assumptions" for e in at.expander)
    assert "AI-generated" in _texts(at)


def test_kill_switch_toggle_hidden_for_bookkeeper() -> None:
    """T-UI-03 proves PRD/12 §6: kill-switch toggle hidden for the bookkeeper."""
    at = _start(Role.BOOKKEEPER)
    at.switch_page("views/7_Log_and_Security.py").run()
    assert not at.exception
    assert len(at.toggle) == 0
    assert "Only the owner can change" in _texts(at)


def test_kill_switch_toggle_for_owner_blocks_actions() -> None:
    """T-UI-01 proves CTL-AC-07 in the UI: owner turns the kill switch on; sidebar shows it."""
    from ui import state

    at = _start(Role.OWNER)
    at.switch_page("views/7_Log_and_Security.py").run()
    assert len(at.toggle) == 1
    at.toggle[0].set_value(True).run()
    assert not at.exception
    assert "Kill switch ON: every send and payment is blocked." in _texts(at)
    assert state.get_app().dashboard().kill_switch is True
    home = _start(Role.OWNER)  # same cached facade: the sidebar now shows the red state
    assert any("Kill switch is ON" in e.value for e in home.sidebar.error)


def test_try_blocked_action_shows_refusal() -> None:
    """T-UI-01 proves PRD/12 §2 page 7: a blocked action is refused with its reason code."""
    at = _start()
    at.switch_page("views/7_Log_and_Security.py").run()
    btn = next(b for b in at.button if b.key == "lr_try_ACCEPT_LOAN")
    btn.click().run()
    assert not at.exception
    assert any("BLOCKED_TYPE" in e.value for e in at.error)


def test_verify_chain_button() -> None:
    """T-UI-01 proves CTL-LOG-02 in the UI: Verify chain reports an intact chain."""
    at = _start()
    at.switch_page("views/7_Log_and_Security.py").run()
    next(b for b in at.button if b.key == "lr_verify").click().run()
    assert any("Chain intact" in s.value for s in at.success)


def test_drafts_then_proposal_in_queue() -> None:
    """T-UI-01 proves PRD/12 §2 page 4: Tier 1 draft, then "Propose sending" fills the queue."""
    at = _start()
    at.switch_page("views/4_Action_Plan.py").run()
    next(b for b in at.button if b.key == "lr_draft_reminders").click().run()
    assert not at.exception
    propose = [b for b in at.button if (b.key or "").startswith("lr_propose_")]
    assert propose, "a reminder draft with a Propose sending button"
    propose[0].click().run()
    assert not at.exception
    assert any((b.key or "").startswith("lr_review_") for b in at.button)
