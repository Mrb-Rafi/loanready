"""Sandbox business date and timestamps (SBX-09).

The business date ``today`` lives in ``meta`` of ``sbx_bank.db``; ``/admin/reset`` sets it
to the bundle's AS_OF. Timestamps use the app's injected clock in ``inprocess`` mode (so
the headless demo is deterministic) and real UTC time in ``external`` mode."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Connection

from contracts.clock import get_clock, wall_clock_now
from sandbox import db
from sandbox.settings import get_settings

TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def now() -> datetime:
    dt = get_clock().now() if get_settings().mode == "inprocess" else wall_clock_now()
    return dt.astimezone(UTC)


def now_iso() -> str:
    return now().strftime(TS_FMT)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FMT).replace(tzinfo=UTC)


def today(conn: Connection | None = None) -> date:
    def read(c: Connection) -> date:
        row = db.one(c, "SELECT value FROM meta WHERE key = 'today'")
        return date.fromisoformat(row["value"]) if row else get_settings().as_of

    if conn is not None:
        return read(conn)
    with db.tx("bank") as c:
        return read(c)


def set_meta(conn: Connection, key: str, value: str) -> None:
    db.run(
        conn,
        "INSERT INTO meta (key, value) VALUES (:k, :v) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        k=key,
        v=value,
    )
