"""T-GOV-03 / T-GOV-25: kill switch."""

from __future__ import annotations

from typing import Any

import pytest

from contracts.actions import AGENT, BOOKKEEPER, OWNER
from contracts.enums import ActionType, Decision, EventType, ReasonCode
from contracts.errors import PermissionDenied


def test_kill_switch_blocks_tier1_to_3_allows_tier0(gov: Any) -> None:
    """T-GOV-03 proves GOV-06, CTL-AC-07: on -> Tier 1-3 blocked, Tier 0 allowed; toggles
    logged with before/after; only the owner can toggle."""
    gov.gate.set_kill_switch(True, OWNER)
    row = gov.audit.rows()[-1]
    assert row.action_type == EventType.KILL_SWITCH_TOGGLED.value
    assert row.params_masked == {"key": "kill_switch", "before": "false", "after": "true"}
    assert (
        gov.gate.evaluate(gov.act(ActionType.RUN_FORECAST, {"as_of": "2026-10-14"})).decision
        == Decision.ALLOWED
    )
    for action in (
        gov.act(ActionType.DRAFT_REMINDER, {"invoice_id": "inv_tiny_1"}),
        gov.pay(),
        gov.reminder(),
    ):
        d = gov.gate.evaluate(action)
        assert d.decision == Decision.BLOCKED and d.reason_code == ReasonCode.KILL_SWITCH
    for who in (BOOKKEEPER, AGENT):
        with pytest.raises(PermissionDenied):
            gov.gate.set_kill_switch(False, who)
    assert gov.audit.rows()[-1].reason_code == ReasonCode.ROLE_NOT_ALLOWED
    gov.gate.set_kill_switch(False, OWNER)
    assert gov.gate.evaluate(gov.pay()).decision == Decision.PENDING_APPROVAL


def test_kill_switch_after_approval_blocks_execution(gov: Any) -> None:
    """T-GOV-25 proves CTL-AC-07: switched on after approval -> execution blocked, proposal
    stays approved; switched off -> the same token executes."""
    d = gov.gate.evaluate(gov.pay())
    token = gov.gate.approve(d.proposal_id, OWNER)
    gov.gate.set_kill_switch(True, OWNER)
    e = gov.gate.execute(d.proposal_id, token.token_id, OWNER)
    assert e.decision == Decision.BLOCKED and e.reason_code == ReasonCode.KILL_SWITCH
    assert gov.executor.calls == []
    gov.gate.set_kill_switch(False, OWNER)
    assert gov.gate.execute(d.proposal_id, token.token_id, OWNER).decision == Decision.EXECUTED
