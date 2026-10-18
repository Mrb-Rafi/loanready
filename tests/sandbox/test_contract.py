"""T-SBX-15, T-SBX-16: error catalogue and the frozen API."""

from __future__ import annotations

import re
from typing import Any

from adapters.errors import ERROR_MAP
from contracts.canonical import file_sha256
from contracts.sandbox_api import SANDBOX_ERROR_CODES
from infra.paths import REPO_ROOT
from sandbox.app import OPENAPI_PATH
from sandbox.dump_openapi import render
from sandbox.errors import ERROR_CATALOGUE, SandboxHTTPError


def test_every_raised_code_in_catalogue_and_mapped() -> None:
    """T-SBX-15 proves SBX-11: every code raised in sandbox/ is in the catalogue, the
    catalogue equals the contracts list, and the adapter maps every code."""
    raised = set()
    for f in (REPO_ROOT / "sandbox").rglob("*.py"):
        raised |= set(
            re.findall(r'SandboxHTTPError\(\s*"([A-Z_]+)"', f.read_text(encoding="utf-8"))
        )
    assert raised and raised <= set(ERROR_CATALOGUE)
    assert set(ERROR_CATALOGUE) == set(SANDBOX_ERROR_CODES) == set(ERROR_MAP)
    try:
        SandboxHTTPError("NOT_A_CODE")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_openapi_frozen(sbx: Any) -> None:
    """T-SBX-16 proves SBX-12: the live schema equals the committed openapi.json and /health
    exposes the committed file's hash."""
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render(), (
        "sandbox API changed: run `python -m sandbox.dump_openapi` deliberately and review the diff"
    )
    health = sbx.client.get("/health").json()
    assert health == {
        "status": "ok",
        "today": "2026-10-14",
        "version": "1",
        "openapi_sha256": file_sha256(OPENAPI_PATH),
    }
