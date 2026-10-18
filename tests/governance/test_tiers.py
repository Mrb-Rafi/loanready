"""T-GOV-01 / T-GOV-02: tier table and the hard-coded blocked set."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest

import governance.tiers as tiers
from contracts.enums import ActionType, Decision, ReasonCode, Tier


def test_every_action_type_has_a_tier_and_unknown_fails_closed() -> None:
    """T-GOV-01 proves GOV-01, GOV-02, CTL-AC-02: table is read-only; unknown -> BLOCKED."""
    assert set(tiers.TIER_TABLE) == set(ActionType)
    assert isinstance(tiers.TIER_TABLE, MappingProxyType)
    assert isinstance(tiers.BLOCKED_TYPES, frozenset)
    with pytest.raises(TypeError):
        tiers.TIER_TABLE[ActionType.ACCEPT_LOAN] = Tier.T0_OBSERVE  # type: ignore[index]
    assert tiers.tier_of("NOT_A_TYPE") == Tier.BLOCKED  # type: ignore[arg-type]
    assert tiers.tier_of(ActionType.SCHEDULE_SUPPLIER_PAYMENT) == Tier.T2_APPROVE
    assert tiers.tier_of(ActionType.RUN_FORECAST) == Tier.T0_OBSERVE
    assert tiers.tier_of(ActionType.DRAFT_REMINDER) == Tier.T1_PROPOSE
    assert not [n for n in dir(tiers) if n.startswith(("set_", "add_", "enable", "unblock"))]


@pytest.mark.parametrize("action_type", sorted(tiers.BLOCKED_TYPES))
def test_blocked_types_refused_and_logged(gov: Any, action_type: ActionType) -> None:
    """T-GOV-02 proves GOV-01, CTL-AC-02, CTL-LOG-02: each blocked type -> BLOCKED_TYPE and
    exactly one log row."""
    before = len(gov.audit.rows())
    d = gov.gate.evaluate(gov.act(action_type, {"anything": 1}))
    assert d.decision == Decision.BLOCKED and d.reason_code == ReasonCode.BLOCKED_TYPE
    rows = gov.audit.rows()
    assert len(rows) == before + 1 and rows[-1].seq == d.log_seq
    assert "CTL-AC-02" in rows[-1].control_ids


def test_accept_loan_blocked_with_fin_control(gov: Any) -> None:
    """T-GOV-02 proves CTL-FIN-04, REG-LND-05: ACCEPT_LOAN can never run."""
    d = gov.gate.evaluate(gov.act(ActionType.ACCEPT_LOAN, {"lender_id": "lender_demo"}))
    assert d.reason_code == ReasonCode.BLOCKED_TYPE
    assert "CTL-FIN-04" in gov.audit.rows()[-1].control_ids


def test_describe_lists_every_type() -> None:
    """GOV-03: tier table for UI and docs, one row per action type, with safeguards."""
    rows = tiers.describe()
    assert [r.action_type for r in rows] == [t.value for t in ActionType]
    assert all(r.safeguards for r in rows)
    by = {r.action_type: r for r in rows}
    assert by["ACCEPT_LOAN"].tier == "BLOCKED"
    assert by["SCHEDULE_SUPPLIER_PAYMENT"].tier == "T2_APPROVE"
