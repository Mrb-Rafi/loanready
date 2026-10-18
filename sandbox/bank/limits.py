"""Bank-side limits, independent of the app's caps (PRD/04 SBX-05 step 6; CTL-LIM-06)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Connection

from sandbox import db
from sandbox.errors import SandboxHTTPError


def get_limits(conn: Connection, account_id: str) -> dict[str, int]:
    row = db.one(conn, "SELECT * FROM limits WHERE account_id = :a", a=account_id)
    if row is None:
        raise SandboxHTTPError("UNKNOWN_ACCOUNT", "no limits for this account")
    return row


def check(conn: Connection, account_id: str, amount_cents: int, value_date: date) -> None:
    """Per-payment limit, then the daily outgoing limit for that value date (scheduled and
    settled payments both count)."""
    lim = get_limits(conn, account_id)
    if amount_cents > lim["max_payment_cents"]:
        raise SandboxHTTPError(
            "BANK_LIMIT_EXCEEDED",
            "amount above the bank's per-payment limit",
            details={"limit": "per_payment"},
        )
    row = db.one(
        conn,
        "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM payments WHERE account_id = :a "
        "AND value_date = :d AND status IN ('scheduled', 'settled')",
        a=account_id,
        d=value_date.isoformat(),
    )
    if row["s"] + amount_cents > lim["daily_outgoing_cents"]:
        raise SandboxHTTPError(
            "BANK_LIMIT_EXCEEDED", "daily outgoing limit reached", details={"limit": "daily"}
        )


def set_limits(
    conn: Connection, account_id: str, max_payment_cents: int, daily_outgoing_cents: int
) -> None:
    db.run(
        conn,
        "INSERT INTO limits (account_id, max_payment_cents, daily_outgoing_cents) VALUES (:a, :m, "
        ":d) "
        "ON CONFLICT(account_id) DO UPDATE SET max_payment_cents = excluded.max_payment_cents, "
        "daily_outgoing_cents = excluded.daily_outgoing_cents",
        a=account_id,
        m=max_payment_cents,
        d=daily_outgoing_cents,
    )
