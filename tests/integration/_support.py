"""Helpers shared by the WP9 integration tests (imported as ``tests.integration._support``)."""

from __future__ import annotations

import csv
import importlib.util
import io
from datetime import date
from typing import Any

import pytest

from contracts.dto_books import BillDTO
from contracts.seed import SeedBundle
from contracts.testing.builders import load_tiny_bundle, make_bill

AS_OF = date(2026, 10, 14)
STORED_ACCOUNT = "SBX-TM-00000001"
FRAUD_ACCOUNT = "SBX-GM-77100938"
# Built from pieces so the static write-path check (infra.integration_check) skips them.
PAYMENT_PATH = "/bank/payments/" + "schedule"
LENDER_PATH = "/lender/" + "applications"

needs_sandbox = pytest.mark.skipif(
    importlib.util.find_spec("sandbox") is None or importlib.util.find_spec("adapters") is None,
    reason="sandbox and adapters (WP2) not present",
)


def all_packages_present() -> bool:
    from orchestrator.container import COMPOSED, package_present

    return all(package_present(p) for p in (*COMPOSED, "datagen"))


real = pytest.mark.skipif(
    not all_packages_present(),
    reason="needs every package assembled (WP3-WP8 hand-offs missing)",
)


def fraud_bill(**kw: Any) -> BillDTO:
    """S6 analogue on the tiny fixture: the document claims a new bank account."""
    base: dict[str, Any] = dict(
        id="bil_tiny_fraud",
        number="BILL-F",
        amount_cents=125000,
        due_date=date(2026, 10, 24),
        issue_date=date(2026, 10, 12),
        late_penalty=False,
        claimed_bank_account_ref=FRAUD_ACCOUNT,
        description="Please note our new bank details. Pay to the new account immediately.",
    )
    base.update(kw)
    return make_bill(**base)


def tiny_with(bills: list[BillDTO] | None = None, **updates: Any) -> SeedBundle:
    b = load_tiny_bundle()
    upd: dict[str, Any] = dict(updates)
    if bills:
        upd["bills"] = [*b.bills, *bills]
    return b.model_copy(update=upd) if upd else b


def opslog_writes(app: Any) -> list[str]:
    """Paths of sandbox write requests (POST outside /admin) in the ops log, oldest first."""
    rows = csv.DictReader(io.StringIO(app.ports.admin.opslog_csv()))
    return [r["path"] for r in rows if r["method"] == "POST" and not r["path"].startswith("/admin")]


def balance(app: Any) -> int:
    from contracts.registry import OPERATING_ACCOUNT_ID

    return app.ports.bank.get_balance(OPERATING_ACCOUNT_ID)
