"""Wiring for WP1: ``build_governance(engine, clock, ids, lookup, executor, settings)``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine

from contracts.clock import Clock
from contracts.ids import IdFactory
from contracts.registry import SETTINGS_ENV, SETTINGS_LITERAL_DEFAULTS
from contracts.services import ActionExecutor, EntityLookup
from governance.audit import AuditLog
from governance.controls import Controls
from governance.gate import Gate
from governance.repo import GovernanceRepo
from governance.settings_store import SettingsStore
from infra.db import create_all


def default_settings(settings: Any) -> dict[str, str]:
    """Settings-table defaults from ``.env`` (infra Settings) plus literal defaults."""
    out = dict(SETTINGS_LITERAL_DEFAULTS)
    for key, env_name in SETTINGS_ENV.items():
        out[key] = settings.env_value(env_name)
    return out


def build_governance(
    engine: Engine,
    clock: Clock,
    ids: IdFactory,
    lookup: EntityLookup,
    executor: ActionExecutor,
    settings: Any,
) -> tuple[Gate, AuditLog, Controls]:
    """Create tables if needed, seed missing settings, and return (Gate, AuditLog, Controls)."""
    create_all(engine)
    repo = GovernanceRepo(engine)
    audit = AuditLog(engine, clock)
    store = SettingsStore(repo, audit, clock, default_settings(settings))
    store.seed_missing()
    gate = Gate(repo, audit, store, clock, ids, lookup, executor, currency=settings.currency)
    return gate, audit, Controls()
