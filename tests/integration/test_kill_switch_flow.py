"""T-ORC-09: the kill switch turned on after approval still stops execution."""

from __future__ import annotations

from typing import Any

import pytest

from contracts.enums import ActionType, Decision, ReasonCode
from tests.integration._support import PAYMENT_PATH, balance, opslog_writes

pytestmark = [pytest.mark.integration]


def test_kill_switch_after_approval_blocks_execution(app: Any) -> None:
    """T-ORC-09 proves CTL-AC-07: approve, switch on, execute -> BLOCKED KILL_SWITCH; MockBank
    sees no payment request; balance unchanged."""
    before = balance(app)
    d = app.propose_supplier_payment("bil_tiny_1")
    app.approve(d.proposal_id)
    app.set_kill_switch(True)
    assert app.dashboard().kill_switch is True
    out = app.execute(d.proposal_id)
    assert out.decision == Decision.BLOCKED
    assert out.reason_code == ReasonCode.KILL_SWITCH
    assert PAYMENT_PATH not in opslog_writes(app)
    assert balance(app) == before


def test_kill_switch_allows_observation_only(app: Any) -> None:
    """T-ORC-09 proves CTL-AC-07: with the switch on, Tier 0 reads work, new proposals and
    drafts are refused; switching off restores them."""
    app.set_kill_switch(True)
    assert app.forecast() is not None
    assert app.readiness() is not None
    d = app.propose_supplier_payment("bil_tiny_1")
    assert d.decision == Decision.BLOCKED and d.reason_code == ReasonCode.KILL_SWITCH
    blocked = app.try_blocked(ActionType.DISABLE_KILL_SWITCH, {})
    assert blocked.decision == Decision.BLOCKED
    app.set_kill_switch(False)
    assert app.propose_supplier_payment("bil_tiny_1").decision == Decision.PENDING_APPROVAL
