"""Display helpers (PRD/12 §1, UI-09, UI-13, UI-14). No money is computed in the UI."""

from __future__ import annotations

from decimal import Decimal

import pytest

from contracts.actions import GateDecision
from contracts.enums import Decision, ReasonCode, Tier
from contracts.errors import EntityCheckFailed
from contracts.testing.fakes import FakeAppFacade
from ui.components import charts, tables
from ui.components.badges import ai_badge, decision_badge, tier_badge, tier_text
from ui.components.feedback import decision_message, error_message
from ui.components.money import cents_to_input, money, parse_amount, rate, share, signed_money


def test_money_helpers() -> None:
    """T-UI-01 proves UI-09: amounts come from int cents via contracts.money.fmt."""
    assert money(123456) == "$1,234.56"
    assert money(None) == "n/a"
    assert signed_money(500) == "+$5.00" and signed_money(-500) == "-$5.00"
    assert rate(Decimal("3.5212")) == "352.12%" and rate(None) == "n/a"
    assert share(250, 1000) == 25 and share(5, 0) == 0 and share(2000, 1000) == 100
    assert cents_to_input(123405) == "1234.05" and cents_to_input(-5) == "-0.05"
    assert parse_amount("$1,234.50") == 123450
    with pytest.raises(ValueError):
        parse_amount("0")
    with pytest.raises(ValueError):
        parse_amount("12.345")
    with pytest.raises(TypeError):
        money(12.5)  # type: ignore[arg-type]  # floats are rejected


def test_badges_have_text() -> None:
    """T-UI-01 proves UI-13: status chips carry text, not colour alone; AI label (CTL-AI-06)."""
    assert tier_text(2) == "Tier 2: needs your approval"
    assert "needs your approval" in tier_badge(Tier.T2_APPROVE)
    assert "Blocked" in decision_badge(Decision.BLOCKED)
    assert "AI-generated" in ai_badge()


def test_friendly_error_names_reason() -> None:
    """T-UI-01 proves UI-14: domain errors are shown with the reason code."""
    exc = EntityCheckFailed("account differs", reason_code=ReasonCode.ACCOUNT_CHANGED)
    text = error_message(exc)
    assert text.startswith("Blocked: ") and "(ACCOUNT_CHANGED)" in text
    assert "Call the supplier on a known number" in text
    d = GateDecision(
        action_id="act_1",
        proposal_id=None,
        decision=Decision.BLOCKED,
        tier=Tier.BLOCKED,
        reason_code=ReasonCode.BLOCKED_TYPE,
        message="BLOCKED_TYPE",
        log_seq=7,
    )
    kind, msg = decision_message(d)
    assert kind == "error" and "BLOCKED_TYPE" in msg and "#7" in msg


def test_tables_and_charts_from_fake() -> None:
    """T-UI-01 proves UI-10, UI-13: tables are text, sorted by EAR; chart data stays int cents."""
    app = FakeAppFacade()
    fin = app.financing()
    ordered = tables.sort_by_ear(fin.options)
    assert ordered[0].product_id == fin.cheapest_id
    df = tables.options_df(fin.options, fin.cheapest_id, fin.most_expensive_id)
    assert df.iloc[0]["Cost rank"] == "Cheapest"
    assert all(isinstance(v, str) for v in df["Total cost"])
    fc = app.forecast()
    frame = charts.forecast_frame(fc)
    assert len(frame) == 13
    for col in ("low_cents", "base_cents", "high_cents"):
        assert all(isinstance(int(v), int) and str(v).lstrip("-").isdigit() for v in frame[col])
    charts.forecast_band(fc).to_dict()  # builds a valid Vega-Lite spec
    audit = tables.audit_df(app.audit_rows())
    assert list(audit.columns)[0] == "Seq"
    assert tables.display_party("ap@x.test") == "a***@x.test"
    assert tables.display_party("Golden Mills") == "Golden Mills"
