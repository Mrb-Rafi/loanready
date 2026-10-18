"""T-ORC-10: statement identities and reconciliation on the seeded bakery (all packages)."""

from __future__ import annotations

from typing import Any

import pytest

from contracts.registry import OPERATING_ACCOUNT_ID
from tests.integration._support import real

pytestmark = [pytest.mark.integration, pytest.mark.real, pytest.mark.slow, real]


def test_balance_sheet_identity(seeded_real: Any) -> None:
    """T-ORC-10 proves BKS identities: assets = liabilities + equity at the day before AS_OF."""
    bs = seeded_real.books_view()["balance_sheet"]
    assert bs.total_assets_cents == bs.total_liabilities_cents + bs.total_equity_cents


def test_ledger_reconciles_with_mockbank(seeded_real: Any) -> None:
    """T-ORC-10 proves PRD/11 §8: the reconciliation uses MockBank's live balance and the
    ledger reconciles to it (every difference explained by reconciling items)."""
    app = seeded_real
    rec = app.books_view()["reconciliation"]
    assert rec.bank_balance_cents == app.ports.bank.get_balance(OPERATING_ACCOUNT_ID)
    assert rec.is_reconciled, rec.difference_cents


def test_closing_balances_match_expected(seeded_real: Any) -> None:
    """T-ORC-10 proves PRD/10 §5 targets through the whole stack: bank 1,900.00, till 300.00."""
    import json

    from infra.paths import DATA_DIR

    expected = json.loads((DATA_DIR / "expected.json").read_text(encoding="utf-8"))
    app = seeded_real
    assert app.ports.bank.get_balance(OPERATING_ACCOUNT_ID) == expected["closing_bank_cents"]
    assert app.books.cash_balance(app.as_of) == (
        expected["closing_bank_cents"] + expected["closing_till_cents"]
    )
