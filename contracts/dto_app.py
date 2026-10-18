"""View models for the UI facade (CON-16)."""

from __future__ import annotations

from datetime import date
from typing import Any

from contracts._model import Frozen
from contracts.enums import Intent


class TierRow(Frozen):
    action_type: str
    tier: str
    description: str
    safeguards: list[str]


class ControlRow(Frozen):
    control_id: str
    title: str
    regulation_refs: list[str]
    implemented_in: str
    tests: list[str]


class CapUsage(Frozen):
    per_action_cents: int
    per_day_cents: int
    used_today_cents: int
    per_payee_week_cents: int
    msgs_per_customer_week: int


class DashboardDTO(Frozen):
    as_of: date
    cash_cents: int
    first_negative_week_base: int | None
    recommended_gap_cents: int
    readiness_score: int
    pending_approvals: int
    confirm_queue: int
    blocked_count: int
    chain_ok: bool
    kill_switch: bool


class ChatReply(Frozen):
    text: str
    ai_label: str | None
    intent: Intent
    attachments: list[str]
    proposals: list[str]  # proposal ids created


class DemoStepResult(Frozen):
    step: int
    title: str
    ok: bool
    details: dict[str, Any]
