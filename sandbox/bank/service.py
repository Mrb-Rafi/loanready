"""MockBank logic (PRD/04 §4). The bank does its own checks even though the app's gate
already did them (defence in depth): it refuses unknown payees and changed accounts itself
(CTL-PAY-02, REG-PAY-02 verification of payee)."""

from __future__ import annotations

import base64
import binascii
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection

from contracts.canonical import sha256_hex
from contracts.sandbox_api import (
    BalanceOut,
    BankAccountOut,
    BankTxnOut,
    PayeeOut,
    PaymentOut,
    PaymentScheduleIn,
)
from sandbox import db
from sandbox.bank import limits
from sandbox.bank.settlement import available, balance, insert_txn, settle_due
from sandbox.clock import now_iso, parse_ts, today
from sandbox.errors import SandboxHTTPError

MAX_VALUE_DATE_DAYS = 90


# -- mapping rows to contract models ------------------------------------------------------
def account_out(conn: Connection, row: dict[str, Any]) -> BankAccountOut:
    return BankAccountOut(
        id=row["id"],
        name=row["name"],
        currency=row["currency"],
        status=row["status"],
        balance_cents=balance(conn, row["id"]),
    )


def txn_out(row: dict[str, Any]) -> BankTxnOut:
    return BankTxnOut(
        id=row["id"],
        account_id=row["account_id"],
        date=date.fromisoformat(row["date"]),
        amount_cents=row["amount_cents"],
        description=row["description"],
        counterparty=row["counterparty"],
        kind=row["kind"],
        payment_ref=row["payment_ref"],
        balance_after_cents=row["balance_after_cents"],
    )


def payee_out(row: dict[str, Any]) -> PayeeOut:
    return PayeeOut(
        id=row["id"],
        name=row["name"],
        bank_account_ref=row["bank_account_ref"],
        first_paid_on=date.fromisoformat(row["first_paid_on"]) if row["first_paid_on"] else None,
        status=row["status"],
    )


def payment_out(row: dict[str, Any]) -> PaymentOut:
    return PaymentOut(
        sbx_ref=row["sbx_ref"],
        from_account_id=row["account_id"],
        payee_id=row["payee_id"],
        amount_cents=row["amount_cents"],
        value_date=date.fromisoformat(row["value_date"]),
        reference=row["reference"],
        status=row["status"],
        failure_code=row["failure_code"],
        settled_on=date.fromisoformat(row["settled_on"]) if row["settled_on"] else None,
        created_at=parse_ts(row["created_at"]),
    )


# -- reads ----------------------------------------------------------------------------------
def _account(conn: Connection, account_id: str) -> dict[str, Any]:
    row = db.one(conn, "SELECT * FROM accounts WHERE id = :a", a=account_id)
    if row is None:
        raise SandboxHTTPError("UNKNOWN_ACCOUNT", f"no account {account_id}")
    return row


def accounts() -> list[BankAccountOut]:
    with db.tx("bank") as c:
        settle_due(c)
        return [account_out(c, r) for r in db.rows(c, "SELECT * FROM accounts ORDER BY id")]


def get_balance(account_id: str) -> BalanceOut:
    with db.tx("bank") as c:
        settle_due(c)
        _account(c, account_id)
        return BalanceOut(
            account_id=account_id,
            balance_cents=balance(c, account_id),
            available_cents=available(c, account_id),
            today=today(c),
        )


def encode_cursor(*parts: Any) -> str:
    return base64.urlsafe_b64encode("|".join(str(p) for p in parts).encode()).decode().rstrip("=")


def decode_cursor(cursor: str, n: int) -> list[str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise SandboxHTTPError("INVALID_CURSOR", "cursor is not valid") from exc
    parts = raw.split("|")
    if len(parts) != n:
        raise SandboxHTTPError("INVALID_CURSOR", "cursor is not valid")
    return parts


def transactions(
    account_id: str, start: date, end: date, limit: int, cursor: str | None
) -> tuple[list[BankTxnOut], str | None]:
    """Sorted by date then seq; ``limit`` rows and the cursor of the next page, if any."""
    with db.tx("bank") as c:
        settle_due(c)
        _account(c, account_id)
        after_date, after_seq = ("", -1)
        if cursor:
            d, s = decode_cursor(cursor, 2)
            if not s.lstrip("-").isdigit():
                raise SandboxHTTPError("INVALID_CURSOR", "cursor is not valid")
            after_date, after_seq = d, int(s)
        rows = db.rows(
            c,
            "SELECT * FROM transactions WHERE account_id = :a AND date >= :s AND date <= :e "
            "AND (date > :ad OR (date = :ad AND seq > :aseq)) ORDER BY date, seq LIMIT :lim",
            a=account_id,
            s=start.isoformat(),
            e=end.isoformat(),
            ad=after_date,
            aseq=after_seq,
            lim=limit + 1,
        )
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = encode_cursor(rows[-1]["date"], rows[-1]["seq"]) if more and rows else None
    return [txn_out(r) for r in rows], nxt


def statement(account_id: str, month: str) -> dict[str, Any]:
    """Monthly statement: opening = balance at the end of the previous month (SHOULD)."""
    try:
        first = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise SandboxHTTPError("VALIDATION_ERROR", "month must be YYYY-MM") from exc
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    with db.tx("bank") as c:
        settle_due(c)
        _account(c, account_id)
        opening = db.one(
            c,
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM transactions WHERE account_id = :a "
            "AND date < :f",
            a=account_id,
            f=first.isoformat(),
        )["s"]
        rows = db.rows(
            c,
            "SELECT * FROM transactions WHERE account_id = :a AND date >= :f AND date < :n "
            "ORDER BY date, seq",
            a=account_id,
            f=first.isoformat(),
            n=nxt.isoformat(),
        )
    closing = opening + sum(r["amount_cents"] for r in rows)
    return {
        "account_id": account_id,
        "month": month,
        "opening_cents": opening,
        "closing_cents": closing,
        "transactions": [txn_out(r).model_dump(mode="json") for r in rows],
    }


def payees() -> list[PayeeOut]:
    with db.tx("bank") as c:
        settle_due(c)
        return [payee_out(r) for r in db.rows(c, "SELECT * FROM payees ORDER BY id")]


def payments(status: str | None) -> list[PaymentOut]:
    with db.tx("bank") as c:
        settle_due(c)
        q = (
            "SELECT * FROM payments"
            + (" WHERE status = :s" if status else "")
            + " ORDER BY created_at, sbx_ref"
        )
        return [payment_out(r) for r in db.rows(c, q, s=status)]


def get_payment(sbx_ref: str) -> PaymentOut:
    with db.tx("bank") as c:
        settle_due(c)
        row = db.one(c, "SELECT * FROM payments WHERE sbx_ref = :r", r=sbx_ref)
    if row is None:
        raise SandboxHTTPError("UNKNOWN_PAYMENT", f"no payment {sbx_ref}")
    return payment_out(row)


# -- writes ---------------------------------------------------------------------------------
def schedule(req: PaymentScheduleIn, idempotency_key: str) -> tuple[int, dict[str, Any]]:
    """SBX-05 rules in order; the first failure returns. Idempotency is checked before this."""
    with db.tx("bank") as c:
        settle_due(c)
        acc = _account(c, req.from_account_id)
        if acc["status"] != "open":
            raise SandboxHTTPError("ACCOUNT_FROZEN", "account is frozen")
        payee = db.one(c, "SELECT * FROM payees WHERE id = :p", p=req.payee_id)
        if payee is None:
            raise SandboxHTTPError("PAYEE_UNKNOWN", "payments go only to registered payees")
        if payee["status"] == "closed":
            raise SandboxHTTPError("PAYEE_CLOSED", "payee account is closed")
        if req.bank_account_ref != payee["bank_account_ref"]:
            raise SandboxHTTPError(
                "ACCOUNT_MISMATCH", "account does not match the registered payee account"
            )
        if req.amount_cents <= 0:
            raise SandboxHTTPError("INVALID_AMOUNT", "amount must be positive")
        t = today(c)
        if req.value_date < t:
            raise SandboxHTTPError("INVALID_VALUE_DATE", "value date is in the past")
        if req.value_date > t + timedelta(days=MAX_VALUE_DATE_DAYS):
            raise SandboxHTTPError("VALUE_DATE_TOO_FAR", "value date more than 90 days ahead")
        limits.check(c, req.from_account_id, req.amount_cents, req.value_date)
        if req.amount_cents > available(c, req.from_account_id):
            raise SandboxHTTPError("INSUFFICIENT_FUNDS", "available balance too low")
        sbx_ref = "pay_" + sha256_hex(idempotency_key)[:12]
        db.run(
            c,
            "INSERT INTO payments (sbx_ref, account_id, payee_id, bank_account_ref, amount_cents, "
            "value_date, "
            "reference, status, idempotency_key, created_at) VALUES (:r, :a, :p, :b, :amt, :vd, "
            ":ref, "
            "'scheduled', :k, :t)",
            r=sbx_ref,
            a=req.from_account_id,
            p=req.payee_id,
            b=req.bank_account_ref,
            amt=req.amount_cents,
            vd=req.value_date.isoformat(),
            ref=req.reference,
            k=idempotency_key,
            t=now_iso(),
        )
        settle_due(c)
        row = db.one(c, "SELECT * FROM payments WHERE sbx_ref = :r", r=sbx_ref)
    return 201, payment_out(row).model_dump(mode="json")


def cancel(sbx_ref: str) -> tuple[int, dict[str, Any]]:
    """Only a scheduled payment whose value date is still ahead (SHOULD, CTL-PAY-05)."""
    with db.tx("bank") as c:
        settle_due(c)
        row = db.one(c, "SELECT * FROM payments WHERE sbx_ref = :r", r=sbx_ref)
        if row is None:
            raise SandboxHTTPError("UNKNOWN_PAYMENT", f"no payment {sbx_ref}")
        if row["status"] != "scheduled" or date.fromisoformat(row["value_date"]) <= today(c):
            raise SandboxHTTPError(
                "NOT_CANCELLABLE", "only scheduled payments before their value date"
            )
        db.run(c, "UPDATE payments SET status = 'cancelled' WHERE sbx_ref = :r", r=sbx_ref)
        row = db.one(c, "SELECT * FROM payments WHERE sbx_ref = :r", r=sbx_ref)
    return 200, payment_out(row).model_dump(mode="json")


# -- admin helpers --------------------------------------------------------------------------
def add_payee(
    conn: Connection, payee_id: str, name: str, bank_account_ref: str, created_by: str = "admin"
) -> None:
    """The only way a payee enters the registry (seed loader and tests; ADD_PAYEE is blocked
    in the app)."""
    db.run(
        conn,
        "INSERT INTO payees (id, name, bank_account_ref, status, first_paid_on, created_by) "
        "VALUES (:i, :n, :b, 'known', NULL, :c) ON CONFLICT(id) DO UPDATE SET name = "
        "excluded.name, "
        "bank_account_ref = excluded.bank_account_ref",
        i=payee_id,
        n=name,
        b=bank_account_ref,
        c=created_by,
    )


def close_payee(payee_id: str) -> PayeeOut:
    with db.tx("bank") as c:
        if db.one(c, "SELECT id FROM payees WHERE id = :p", p=payee_id) is None:
            raise SandboxHTTPError("PAYEE_UNKNOWN", f"no payee {payee_id}")
        db.run(c, "UPDATE payees SET status = 'closed' WHERE id = :p", p=payee_id)
        return payee_out(db.one(c, "SELECT * FROM payees WHERE id = :p", p=payee_id))


def return_payment(sbx_ref: str) -> PaymentOut:
    """Return a settled payment: money back as a payment_return transaction (SHOULD)."""
    with db.tx("bank") as c:
        settle_due(c)
        row = db.one(c, "SELECT * FROM payments WHERE sbx_ref = :r", r=sbx_ref)
        if row is None:
            raise SandboxHTTPError("UNKNOWN_PAYMENT", f"no payment {sbx_ref}")
        if row["status"] != "settled":
            raise SandboxHTTPError("NOT_CANCELLABLE", "only settled payments can be returned")
        txn_id = "txn_" + sha256_hex("return:" + sbx_ref)[:12]
        insert_txn(
            c,
            txn_id=txn_id,
            account_id=row["account_id"],
            d=today(c),
            amount_cents=row["amount_cents"],
            description=f"RETURNED {row['reference']}",
            counterparty=None,
            kind="payment_return",
            payment_ref=sbx_ref,
        )
        db.run(
            c,
            "UPDATE payments SET status = 'returned', return_transaction_id = :t WHERE sbx_ref = "
            ":r",
            t=txn_id,
            r=sbx_ref,
        )
        return payment_out(db.one(c, "SELECT * FROM payments WHERE sbx_ref = :r", r=sbx_ref))
