"""T-GOV-23: settings are owner-only, validated and logged with before/after."""

from __future__ import annotations

from typing import Any

import pytest

from contracts.actions import AGENT, BOOKKEEPER, OWNER
from contracts.enums import EventType, ReasonCode
from contracts.errors import InvalidParams, PermissionDenied
from contracts.registry import SETTINGS_KEYS
from governance.settings_store import validate


def test_defaults_seeded(gov: Any) -> None:
    """GOV-07: every settings key has a value seeded from .env defaults."""
    s = gov.gate.settings()
    assert set(SETTINGS_KEYS) <= set(s)
    assert s["kill_switch"] == "false" and s["cap_per_action_cents"] == "500000"
    assert s["approval_ttl_seconds"] == "900" and s["tier3_enabled_types"] == "[]"
    assert s["safety_buffer_cents"] == "auto" and s["framework"] == "IFRS_SME"


def test_owner_only_and_logged(gov: Any) -> None:
    """T-GOV-23 proves GOV-07, CTL-CHG-01: owner changes are logged with before/after;
    bookkeeper and agent are refused and the refusal is logged."""
    gov.gate.update_setting("cap_per_day_cents", "750000", OWNER)
    row = gov.audit.rows()[-1]
    assert row.action_type == EventType.SETTINGS_CHANGED.value
    assert row.params_masked == {"key": "cap_per_day_cents", "before": "1000000", "after": "750000"}
    assert "CTL-CHG-01" in row.control_ids
    assert gov.gate.cap_usage().per_day_cents == 750000
    for who in (BOOKKEEPER, AGENT):
        with pytest.raises(PermissionDenied) as exc:
            gov.gate.update_setting("cap_per_day_cents", "1", who)
        assert exc.value.reason_code == ReasonCode.ROLE_NOT_ALLOWED
    assert gov.gate.settings()["cap_per_day_cents"] == "750000"


@pytest.mark.parametrize(
    "key,value",
    [
        ("cap_per_day_cents", "0"),
        ("cap_per_day_cents", "-5"),
        ("cap_per_day_cents", "1.5"),
        ("confidence_confirm_threshold", "1.2"),
        ("confidence_confirm_threshold", "x"),
        ("kill_switch", "maybe"),
        ("framework", "LOCAL"),
        ("safety_buffer_cents", "-1"),
        ("tier3_enabled_types", '["SCHEDULE_SUPPLIER_PAYMENT"]'),
        ("tier3_enabled_types", "not json"),
        ("tier3_enabled_types", '{"a": 1}'),
        ("unknown_key", "1"),
    ],
)
def test_validation(gov: Any, key: str, value: str) -> None:
    """T-GOV-23 proves GOV-07, GOV-08: caps positive integers, thresholds 0..1, Tier 3 list
    only TEST_T3_NOTE, unknown keys refused."""
    with pytest.raises(InvalidParams):
        gov.gate.update_setting(key, value, OWNER)


def test_validate_normalises() -> None:
    """GOV-07: accepted values are normalised."""
    assert validate("kill_switch", "TRUE") == "true"
    assert validate("tier3_enabled_types", '["TEST_T3_NOTE","TEST_T3_NOTE"]') == '["TEST_T3_NOTE"]'
    assert validate("receipt_confirm_threshold", "0.75") == "0.75"
    assert validate("safety_buffer_cents", "150000") == "150000"
    assert validate("framework", "US_GAAP") == "US_GAAP"
