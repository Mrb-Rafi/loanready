"""The only reader of the sandbox write token (project rule 3; CTL-AC-05, REG-SEC-01).

Used only by ``WriteClient`` after a ticket has been verified."""

from __future__ import annotations

from typing import Any


def write_token(settings: Any) -> str:
    return settings.sandbox_write_token.get_secret_value()
