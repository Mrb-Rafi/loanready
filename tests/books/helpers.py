"""Small builders shared by the WP3 tests."""

from __future__ import annotations

from datetime import date

from contracts.sandbox_api import BankTxnOut


def txn(
    tid: str,
    d: date,
    cents: int,
    memo: str,
    counterparty: str | None = None,
    kind: str = "seed",
) -> BankTxnOut:
    return BankTxnOut(
        id=tid,
        account_id="acc_operating",
        date=d,
        amount_cents=cents,
        description=memo,
        counterparty=counterparty,
        kind=kind,  # type: ignore[arg-type]
        payment_ref=None,
        balance_after_cents=0,
    )
