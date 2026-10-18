"""Settings table: kill switch, caps, thresholds (GOV-06..08, CTL-CHG-01, CTL-AC-07).

Only the owner changes settings, every change is logged with before/after, and the agent
has no code path here (CHANGE_LIMITS is a blocked action type)."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from contracts.actions import Principal
from contracts.clock import Clock
from contracts.enums import ActionType, Decision, EventType, Framework, ReasonCode
from contracts.errors import InvalidParams, PermissionDenied
from contracts.registry import SETTINGS_KEYS
from governance.audit import AuditLog
from governance.repo import GovernanceRepo
from governance.tiers import may_change_settings

INT_KEYS = frozenset(
    {
        "cap_per_action_cents",
        "cap_per_day_cents",
        "cap_per_payee_week_cents",
        "cap_msgs_per_customer_week",
        "approval_ttl_seconds",
    }
)
RATIO_KEYS = frozenset({"confidence_confirm_threshold", "receipt_confirm_threshold"})
TIER3_ALLOWED = frozenset({ActionType.TEST_T3_NOTE.value})


def validate(key: str, value: str) -> str:
    """Return the normalised value or raise InvalidParams (GOV-07, GOV-08)."""
    if key not in SETTINGS_KEYS:
        raise InvalidParams(f"unknown setting {key!r}")
    v = str(value).strip()
    if key in INT_KEYS:
        if not v.isdigit() or int(v) <= 0:
            raise InvalidParams(f"{key} must be a positive integer")
        return str(int(v))
    if key in RATIO_KEYS:
        try:
            d = Decimal(v)
        except InvalidOperation as exc:
            raise InvalidParams(f"{key} must be a number between 0 and 1") from exc
        if not (d.is_finite() and Decimal(0) <= d <= Decimal(1)):
            raise InvalidParams(f"{key} must be between 0 and 1")
        return v
    if key == "kill_switch":
        if v.lower() not in ("true", "false"):
            raise InvalidParams("kill_switch must be true or false")
        return v.lower()
    if key == "framework":
        try:
            return Framework(v).value
        except ValueError as exc:
            raise InvalidParams("framework must be IFRS_SME or US_GAAP") from exc
    if key == "safety_buffer_cents":
        if v != "auto" and not v.isdigit():
            raise InvalidParams("safety_buffer_cents must be 'auto' or a non-negative integer")
        return v
    # tier3_enabled_types
    try:
        items = json.loads(v)
    except ValueError as exc:
        raise InvalidParams("tier3_enabled_types must be a JSON list") from exc
    if not isinstance(items, list) or not set(items) <= TIER3_ALLOWED:
        raise InvalidParams("tier3_enabled_types may contain only TEST_T3_NOTE")
    return json.dumps(sorted(set(items)))


class SettingsStore:
    def __init__(
        self, repo: GovernanceRepo, audit: AuditLog, clock: Clock, defaults: dict[str, str]
    ):
        self._repo = repo
        self._audit = audit
        self._clock = clock
        self._defaults = {k: validate(k, v) for k, v in defaults.items()}

    def seed_missing(self) -> None:
        """Insert defaults for keys not in the table yet (idempotent; not logged)."""
        current = self._repo.settings()
        for key, value in self._defaults.items():
            if key not in current:
                self._repo.put_setting(key, value, "system", self._clock.now())

    def all(self) -> dict[str, str]:
        return {**self._defaults, **self._repo.settings()}

    def get(self, key: str) -> str:
        return self.all()[key]

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def kill_switch(self) -> bool:
        return self.get("kill_switch") == "true"

    def tier3_enabled(self) -> frozenset[str]:
        return frozenset(json.loads(self.get("tier3_enabled_types")))

    def _require_owner(self, principal: Principal, what: str) -> None:
        if not may_change_settings(principal):
            self._audit.append_decision(
                action_type=ActionType.CHANGE_LIMITS.value
                if what != "kill_switch"
                else ActionType.DISABLE_KILL_SWITCH.value,
                decision=Decision.BLOCKED,
                reason_code=ReasonCode.ROLE_NOT_ALLOWED,
                principal_id=principal.id,
                params={"setting": what},
                control_ids=["CTL-CHG-01", "CTL-AC-04", "CTL-LOG-02"],
            )
            raise PermissionDenied(
                f"only the owner may change {what}", reason_code=ReasonCode.ROLE_NOT_ALLOWED
            )

    def update(self, key: str, value: str, principal: Principal) -> None:
        self._require_owner(principal, key)
        new = validate(key, value)
        before = self.get(key)
        self._repo.put_setting(key, new, principal.id, self._clock.now())
        event = (
            EventType.KILL_SWITCH_TOGGLED if key == "kill_switch" else EventType.SETTINGS_CHANGED
        )
        ctl = ["CTL-AC-07", "CTL-CHG-01"] if key == "kill_switch" else ["CTL-CHG-01"]
        self._audit.log_event(event, {"key": key, "before": before, "after": new}, principal, ctl)

    def set_kill_switch(self, on: bool, principal: Principal) -> None:
        self.update("kill_switch", "true" if on else "false", principal)
