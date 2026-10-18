"""Payment lifecycle and settlement (PRD/04 §4.3).

    scheduled --settle_due (value_date <= today)--> settled --admin return--> returned
        |  `-- payee closed / funds short at settlement --> failed (failure_code)
        `---- cancel (value_date > today) --> cancelled

``settle_due`` processes due payments in a deterministic order (value_date, created_at,
sbx_ref) inside one transaction and is idempotent. Same-day payments settle immediately.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Connection

from contracts.canonical import sha256_hex
from sandbox import db
from sandbox.clock import today


def balance(conn: Connection, account_id: str) -> int:
    """Settled ledger balance = sum of transactions (derived, never stored)."""
    row = db.one(
        conn,
        "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM transactions WHERE account_id = :a",
        a=account_id,
    )
    return int(row["s"])


def available(conn: Connection, account_id: str) -> int:
    """balance + overdraft limit - scheduled payments."""
    acc = db.one(conn, "SELECT overdraft_limit_cents FROM accounts WHERE id = :a", a=account_id)
    sched = db.one(
        conn,
        "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM payments WHERE account_id = :a "
        "AND status = 'scheduled'",
        a=account_id,
    )
    return (
        balance(conn, account_id)
        + int(acc["overdraft_limit_cents"] if acc else 0)
        - int(sched["s"])
    )


def insert_txn(
    conn: Connection,
    *,
    txn_id: str,
    account_id: str,
    d: date,
    amount_cents: int,
    description: str,
    counterparty: str | None,
    kind: str,
    payment_ref: str | None,
) -> None:
    """Insert a transaction with balance_after = running sum and the next seq."""
    seq = db.one(conn, "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM transactions")["n"]
    db.run(
        conn,
        "INSERT INTO transactions (id, account_id, date, amount_cents, description, counterparty, "
        "kind, "
        "payment_ref, balance_after_cents, seq) VALUES (:id, :a, :d, :amt, :desc, :cp, :kind, "
        ":ref, :bal, :seq)",
        id=txn_id,
        a=account_id,
        d=d.isoformat(),
        amt=amount_cents,
        desc=description,
        cp=counterparty,
        kind=kind,
        ref=payment_ref,
        bal=balance(conn, account_id) + amount_cents,
        seq=seq,
    )


def payment_txn_id(sbx_ref: str) -> str:
    return "txn_" + sha256_hex(sbx_ref)[:12]


def settle_due(conn: Connection | None = None) -> int:
    """Settle or fail every scheduled payment whose value date has come. Returns the count."""
    if conn is None:
        with db.tx("bank") as c:
            return settle_due(c)
    t = today(conn)
    due = db.rows(
        conn,
        "SELECT * FROM payments WHERE status = 'scheduled' AND value_date <= :t "
        "ORDER BY value_date, created_at, sbx_ref",
        t=t.isoformat(),
    )
    for p in due:
        payee = db.one(conn, "SELECT * FROM payees WHERE id = :p", p=p["payee_id"])
        acc = db.one(conn, "SELECT * FROM accounts WHERE id = :a", a=p["account_id"])
        if payee is None or payee["status"] != "known":
            db.run(
                conn,
                "UPDATE payments SET status = 'failed', failure_code = 'PAYEE_ACCOUNT_CLOSED' "
                "WHERE sbx_ref = :r",
                r=p["sbx_ref"],
            )
            continue
        if balance(conn, p["account_id"]) + int(acc["overdraft_limit_cents"]) < p["amount_cents"]:
            db.run(
                conn,
                "UPDATE payments SET status = 'failed', failure_code = "
                "'INSUFFICIENT_FUNDS_AT_SETTLEMENT' "
                "WHERE sbx_ref = :r",
                r=p["sbx_ref"],
            )
            continue
        vd = date.fromisoformat(p["value_date"])
        txn_id = payment_txn_id(p["sbx_ref"])
        insert_txn(
            conn,
            txn_id=txn_id,
            account_id=p["account_id"],
            d=vd,
            amount_cents=-p["amount_cents"],
            description=f"{payee['name']} {p['reference']}",
            counterparty=payee["name"],
            kind="payment",
            payment_ref=p["sbx_ref"],
        )
        db.run(
            conn,
            "UPDATE payments SET status = 'settled', settled_on = :d, transaction_id = :t "
            "WHERE sbx_ref = :r",
            d=vd.isoformat(),
            t=txn_id,
            r=p["sbx_ref"],
        )
        if payee["first_paid_on"] is None:
            db.run(
                conn,
                "UPDATE payees SET first_paid_on = :d WHERE id = :p",
                d=vd.isoformat(),
                p=payee["id"],
            )
    return len(due)
