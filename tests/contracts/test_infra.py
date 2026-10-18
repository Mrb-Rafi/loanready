"""Infra: settings, clock/id providers, redaction (CTL-SEC-01), in-process transport,
version check and the integration check's static rules."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import httpx
import pytest

from contracts.clock import FixedClock, SystemClock, get_clock, reset_clock
from contracts.ids import get_id_factory, reset_id_factory
from infra.integration_check import ownership_problems, static_checks
from infra.logging_setup import RedactionFilter, configure_logging, redact
from infra.paths import REPO_ROOT, ensure_dirs, var_dir
from infra.sandbox_runner import InProcessTransport, mode_mismatch
from infra.settings import Settings, get_settings, reset_settings
from infra.versions import check, package_versions


def test_settings_from_test_environment(settings: Settings) -> None:
    """CON-22 proves PRD/01 §5: tests run with settings_for_tests, never a developer .env."""
    assert (
        settings.env == "test"
        and settings.llm_backend == "mock"
        and settings.ocr_backend == "sidecar"
    )
    assert settings.as_of == date(2026, 10, 14) and settings.seed == 20261014
    assert settings.sandbox_mode == "inprocess" and settings.deterministic
    assert "sbx-write" not in repr(settings)  # SecretStr hides tokens
    assert settings.env_value("CAP_PER_ACTION_CENTS") == "500000"
    assert settings.env_value("LOANREADY_FRAMEWORK") == "IFRS_SME"
    with pytest.raises(KeyError):
        settings.env_value("SANDBOX_ADMIN_TOKEN")
    assert var_dir() == Path(settings.var_dir)
    assert (ensure_dirs() / "seed").is_dir()


def test_clock_and_id_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """CON-02 / CON-03: FixedClock and seeded ids in test/demo, SystemClock in dev."""
    reset_clock()
    reset_id_factory()
    assert isinstance(get_clock(), FixedClock)
    assert get_id_factory().seed == 20261014
    monkeypatch.setenv("LOANREADY_ENV", "dev")
    reset_settings()
    try:
        assert isinstance(get_clock(), SystemClock) and get_id_factory().seed is None
    finally:
        monkeypatch.setenv("LOANREADY_ENV", "test")
        reset_settings()
    assert get_settings().env == "test"


def test_redaction(caplog: pytest.LogCaptureFixture) -> None:
    """CTL-SEC-01 proves REG-SEC-02: tokens, keys, bearer headers and account numbers are
    redacted from operational logs."""
    write = get_settings().sandbox_write_token.get_secret_value()
    text = (
        f"token {write} Authorization: Bearer abc.def key sk-proj-ABCDEFGH1234 acct 99887766554433 "
        "payee SBX-GM-00412277 other sbx-admin-zzz"
    )
    out = redact(text)
    for secret in (
        write,
        "abc.def",
        "sk-proj-ABCDEFGH1234",
        "99887766554433",
        "00412277",
        "sbx-admin-zzz",
    ):
        assert secret not in out
    assert "****4433" in out and "****2277" in out
    configure_logging(logging.INFO)
    logger = logging.getLogger("loanready.test")
    logger.addFilter(RedactionFilter())
    with caplog.at_level(logging.INFO):
        logger.info("calling sandbox with %s", write)
    assert write not in caplog.text and "[redacted]" in caplog.text


def test_inprocess_transport() -> None:
    """ARC-13 / PRD/04 §1: synchronous httpx client over an ASGI app without a port."""
    from fastapi import FastAPI, Header

    app = FastAPI()

    @app.post("/echo")
    def echo(body: dict, idempotency_key: str = Header(default="")) -> dict:
        return {"body": body, "key": idempotency_key}

    transport = InProcessTransport(app)
    with httpx.Client(transport=transport, base_url="http://sandbox.local") as client:
        r = client.post("/echo", json={"a": 1}, headers={"Idempotency-Key": "act_1"})
        assert r.status_code == 200 and r.json() == {"body": {"a": 1}, "key": "act_1"}
        assert client.get("/missing").status_code == 404
    transport.close()
    transport.close()  # idempotent


def test_mode_mismatch_external_without_server() -> None:
    """UI-16: external mode with nothing listening is reported."""
    msg = mode_mismatch("external", "http://127.0.0.1:1")
    assert msg is not None and "nothing answers" in msg


def test_versions_on_skeleton() -> None:
    """ARC-02 / CTL-CHG-02: every skeleton declares REQUIRES_CONTRACTS = '1'."""
    rows = check()
    assert {r.package for r in rows} >= {"governance", "orchestrator", "ui"}
    assert all(r.ok for r in package_versions() if r.present)


def test_static_checks_clean_and_detecting(tmp_path: Path) -> None:
    """PRD/01 §7.4 step 4: the WP0 tree is clean; each rule fires on a planted file."""
    assert static_checks(REPO_ROOT) == []
    assert ownership_problems(REPO_ROOT) == []
    (tmp_path / "books").mkdir()
    (tmp_path / "books" / "bad.py").write_text(
        "a = float(amount_cents)\n"
        "from datetime import datetime\nb = datetime.now()\n"
        "URL = '/messaging/" + "send'\n"  # split so this test file itself stays clean
        "T = 'SANDBOX_WRITE_TOKEN'\n",
        encoding="utf-8",
    )
    found = static_checks(tmp_path)
    assert len(found) == 4
    assert any("float on money" in f for f in found)
    assert any("wall-clock" in f for f in found)
    assert any("write path" in f for f in found)
    assert any("SANDBOX_WRITE_TOKEN" in f for f in found)
    (tmp_path / "stray.txt").write_text("x", encoding="utf-8")
    assert ownership_problems(tmp_path) == ["unowned file: stray.txt"]
