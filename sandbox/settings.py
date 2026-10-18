"""Sandbox settings (PRD/04 §1). The sandbox is a separate service and must not import
``infra``, so this module reads the environment itself (ARC-08 exception): process
environment first, then the repo's ``.env`` (ignored in test mode)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SandboxSettings:
    env: str
    as_of: date
    var_dir: Path
    read_token: str
    write_token: str
    admin_token: str
    mode: str  # inprocess | external
    write_rate_per_min: int
    max_payment_cents: int
    daily_outgoing_cents: int


def _values() -> dict[str, str]:
    values: dict[str, str] = {}
    if os.environ.get("LOANREADY_ENV") != "test":
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update(os.environ)
    return values


@lru_cache(maxsize=1)
def get_settings() -> SandboxSettings:
    v = _values()
    var = Path(v.get("LOANREADY_VAR_DIR", "var"))
    return SandboxSettings(
        env=v.get("LOANREADY_ENV", "dev"),
        as_of=date.fromisoformat(v.get("LOANREADY_AS_OF", "2026-10-14")),
        var_dir=var if var.is_absolute() else REPO_ROOT / var,
        read_token=v.get("SANDBOX_READ_TOKEN", "sbx-read-dev-0001"),
        write_token=v.get("SANDBOX_WRITE_TOKEN", "sbx-write-dev-0001"),
        admin_token=v.get("SANDBOX_ADMIN_TOKEN", "sbx-admin-dev-0001"),
        mode=v.get("SANDBOX_MODE", "inprocess"),
        write_rate_per_min=int(v.get("SBX_WRITE_RATE_PER_MIN", "30")),
        max_payment_cents=int(v.get("SBX_MAX_PAYMENT_CENTS", "500000")),
        daily_outgoing_cents=int(v.get("SBX_DAILY_OUTGOING_CENTS", "1000000")),
    )


def reset_settings() -> None:
    get_settings.cache_clear()
