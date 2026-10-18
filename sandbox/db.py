"""One SQLite file per sub-app (WAL), schemas as plain DDL (PRD/04 §4.1, §9).

``var/sbx_bank.db`` also holds the ``meta`` table (sandbox business date)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, text

from sandbox.settings import get_settings

SUBAPPS = ("bank", "pos", "messaging", "lender")
FILES = {
    "bank": "sbx_bank.db",
    "pos": "sbx_pos.db",
    "messaging": "sbx_msg.db",
    "lender": "sbx_lender.db",
}

_COMMON = [
    """CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, path TEXT NOT NULL,
       request_sha256 TEXT NOT NULL, status_code INTEGER NOT NULL, response_json TEXT NOT NULL,
       created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS ops_log (seq INTEGER PRIMARY KEY, ts TEXT NOT NULL,
       subapp TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, scope TEXT NOT NULL,
       idempotency_key TEXT, ticket_id TEXT, status_code INTEGER NOT NULL, error_code TEXT,
       request_sha256 TEXT NOT NULL, response_sha256 TEXT NOT NULL, prev_hash TEXT NOT NULL,
       row_hash TEXT NOT NULL)""",
]
SCHEMAS: dict[str, list[str]] = {
    "bank": [
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, name TEXT NOT NULL,
           currency TEXT NOT NULL, status TEXT NOT NULL, overdraft_limit_cents INTEGER NOT NULL
           DEFAULT 0, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS transactions (id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
           date TEXT NOT NULL, amount_cents INTEGER NOT NULL, description TEXT NOT NULL,
           counterparty TEXT, kind TEXT NOT NULL, payment_ref TEXT,
           balance_after_cents INTEGER NOT NULL, seq INTEGER NOT NULL UNIQUE)""",
        """CREATE TABLE IF NOT EXISTS payees (id TEXT PRIMARY KEY, name TEXT NOT NULL,
           bank_account_ref TEXT NOT NULL, status TEXT NOT NULL, first_paid_on TEXT,
           created_by TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS payments (sbx_ref TEXT PRIMARY KEY, account_id TEXT NOT NULL,
           payee_id TEXT NOT NULL, bank_account_ref TEXT NOT NULL, amount_cents INTEGER NOT NULL,
           value_date TEXT NOT NULL, reference TEXT NOT NULL, status TEXT NOT NULL,
           failure_code TEXT, idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
           settled_on TEXT, transaction_id TEXT, return_transaction_id TEXT)""",
        """CREATE TABLE IF NOT EXISTS limits (account_id TEXT PRIMARY KEY,
           max_payment_cents INTEGER NOT NULL, daily_outgoing_cents INTEGER NOT NULL)""",
        *_COMMON,
    ],
    "pos": [
        """CREATE TABLE IF NOT EXISTS sales (id TEXT PRIMARY KEY, ts TEXT NOT NULL,
           date TEXT NOT NULL, amount_cents INTEGER NOT NULL, tender TEXT NOT NULL, note TEXT)""",
        """CREATE TABLE IF NOT EXISTS sale_items (sale_id TEXT NOT NULL, pos INTEGER NOT NULL,
           name TEXT NOT NULL, qty INTEGER NOT NULL, unit_cents INTEGER NOT NULL,
           PRIMARY KEY (sale_id, pos))""",
        *_COMMON,
    ],
    "messaging": [
        """CREATE TABLE IF NOT EXISTS messages (sbx_ref TEXT PRIMARY KEY, channel TEXT NOT NULL,
           to_ref TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
           body_sha256 TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)""",
        *_COMMON,
    ],
    "lender": [
        """CREATE TABLE IF NOT EXISTS applications (receipt_id TEXT PRIMARY KEY,
           lender_id TEXT NOT NULL, business_name TEXT NOT NULL,
           requested_amount_cents INTEGER NOT NULL, term_days INTEGER NOT NULL,
           purpose TEXT NOT NULL,
           pack_sha256 TEXT NOT NULL, sections_json TEXT NOT NULL, missing_json TEXT NOT NULL,
           pdf_path TEXT NOT NULL, status TEXT NOT NULL, received_at TEXT NOT NULL)""",
        *_COMMON,
    ],
}
TABLES: dict[str, list[str]] = {
    "bank": [
        "meta",
        "accounts",
        "transactions",
        "payees",
        "payments",
        "limits",
        "idempotency",
        "ops_log",
    ],
    "pos": ["sales", "sale_items", "idempotency", "ops_log"],
    "messaging": ["messages", "idempotency", "ops_log"],
    "lender": ["applications", "idempotency", "ops_log"],
}

_engines: dict[str, Engine] = {}


def _pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def db_path(subapp: str) -> Path:
    return get_settings().var_dir / FILES[subapp]


def engine(subapp: str) -> Engine:
    """Engine for a sub-app DB, tables created on first use."""
    path = db_path(subapp)
    key = str(path.resolve())
    eng = _engines.get(key)
    if eng is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(f"sqlite:///{key}", connect_args={"check_same_thread": False})
        event.listen(eng, "connect", _pragmas)
        with eng.begin() as c:
            for ddl in SCHEMAS[subapp]:
                c.execute(text(ddl))
        _engines[key] = eng
    return eng


@contextmanager
def tx(subapp: str) -> Iterator[Connection]:
    """One transaction on a sub-app DB."""
    with engine(subapp).begin() as conn:
        yield conn


def rows(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def one(conn: Connection, sql: str, **params: Any) -> dict[str, Any] | None:
    r = conn.execute(text(sql), params).mappings().first()
    return dict(r) if r else None


def run(conn: Connection, sql: str, **params: Any) -> None:
    conn.execute(text(sql), params)


def reset_all() -> None:
    """Drop and recreate every sub-app's tables (no file deletion: Windows locks files)."""
    for subapp in SUBAPPS:
        with tx(subapp) as c:
            for table in TABLES[subapp]:
                c.execute(text(f"DROP TABLE IF EXISTS {table}"))
            for ddl in SCHEMAS[subapp]:
                c.execute(text(ddl))


def dispose_all() -> None:
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()
