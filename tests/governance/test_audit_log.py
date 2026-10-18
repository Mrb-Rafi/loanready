"""T-GOV-19..22: hash chain, append-only API, CSV export and masking."""

from __future__ import annotations

import csv
import itertools
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from contracts.actions import CSV_COLUMNS, OWNER, SYSTEM
from contracts.canonical import ZERO_HASH
from contracts.clock import FixedClock
from contracts.enums import Decision, EventType, ReasonCode
from governance.audit import AuditLog
from infra.db import create_all, engine_for

_DB = itertools.count()


def fresh_log(tmp_dir: Path) -> tuple[AuditLog, Path]:
    path = tmp_dir / f"audit_{next(_DB)}.db"
    eng = engine_for(path)
    create_all(eng)
    return AuditLog(eng, FixedClock(__import__("datetime").date(2026, 10, 14))), path


def tamper(path: Path, sql: str, params: tuple = ()) -> None:
    con = sqlite3.connect(path)
    con.execute(sql, params)
    con.commit()
    con.close()


def fill(log: AuditLog, n: int) -> None:
    for i in range(n):
        log.log_event(EventType.DEMO_STEP, {"step": i, "note": "x" * i}, SYSTEM, ["CTL-LOG-01"])


def test_chain_basics(tmp_path: Path) -> None:
    """T-GOV-19 proves GOV-13, CTL-LOG-01: seq from 1 by +1, row 1 links to ZERO_HASH."""
    log, _ = fresh_log(tmp_path)
    assert log.verify_chain() is None and log.last_hash() == ZERO_HASH
    fill(log, 5)
    rows = log.rows()
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]
    assert rows[0].prev_hash == ZERO_HASH
    assert all(b.prev_hash == a.row_hash for a, b in itertools.pairwise(rows))
    assert log.verify_chain() is None
    assert log.last_hash() == rows[-1].row_hash
    assert [r.seq for r in log.rows(2)] == [4, 5]


@pytest.mark.parametrize(
    "column,value",
    [
        ("action_type", "EDIT"),
        ("principal_id", "mallory"),
        ("decision", "APPROVED"),
        ("params_masked_json", '{"step":99}'),
        ("control_ids_json", "[]"),
        ("ts", "2026-10-14T03:00:09.000000Z"),
        ("prev_hash", "f" * 64),
        ("row_hash", "e" * 64),
    ],
)
def test_tampering_any_field_detected_at_that_row(tmp_path: Path, column: str, value: str) -> None:
    """T-GOV-19 proves GOV-14, CTL-LOG-03: changing any field of row k returns k."""
    log, path = fresh_log(tmp_path)
    fill(log, 6)
    tamper(path, f"UPDATE action_log SET {column} = ? WHERE seq = 4", (value,))
    assert log.verify_chain() == 4


def test_deletion_and_reordering_detected(tmp_path: Path) -> None:
    """T-GOV-19 proves GOV-14: deleting a row or swapping two rows is detected."""
    log, path = fresh_log(tmp_path)
    fill(log, 6)
    tamper(path, "DELETE FROM action_log WHERE seq = 3")
    assert log.verify_chain() == 3
    log2, path2 = fresh_log(tmp_path)
    fill(log2, 6)
    tamper(path2, "UPDATE action_log SET seq = 100 WHERE seq = 2")
    tamper(path2, "UPDATE action_log SET seq = 2 WHERE seq = 5")
    tamper(path2, "UPDATE action_log SET seq = 5 WHERE seq = 100")
    assert log2.verify_chain() == 2
    log3, path3 = fresh_log(tmp_path)
    fill(log3, 3)
    tamper(path3, "DELETE FROM action_log WHERE seq = 3")
    assert log3.verify_chain() is None  # truncating the tail is visible via last_hash/export count


@settings(
    max_examples=40,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(n=st.integers(min_value=1, max_value=8), k=st.data(), payload=st.text(max_size=40))
def test_property_any_change_detected(tmp_path: Path, n: int, k: Any, payload: str) -> None:
    """T-GOV-19 proves GOV-14 (property): random appends verify; a change to any stored
    field of row k makes verify_chain return k."""
    log, path = fresh_log(tmp_path)
    for i in range(n):
        log.log_event(EventType.DEMO_STEP, {"i": i, "payload": payload}, OWNER)
    assert log.verify_chain() is None
    row = k.draw(st.integers(min_value=1, max_value=n))
    column = k.draw(
        st.sampled_from(
            ["action_type", "principal_id", "params_masked_json", "reason_code", "prev_hash"]
        )
    )
    tamper(path, f"UPDATE action_log SET {column} = {column} || 'x' WHERE seq = ?", (row,))
    assert log.verify_chain() == row


def test_no_update_or_delete_api() -> None:
    """T-GOV-20 proves GOV-12, CTL-LOG-01: AuditLog has no update/delete/clear method."""
    names = [n.lower() for n in dir(AuditLog) if not n.startswith("__")]
    assert not [
        n
        for n in names
        if any(w in n for w in ("update", "delete", "remove", "clear", "edit", "truncate"))
    ]


def test_csv_export_columns_masking_and_event(tmp_path: Path) -> None:
    """T-GOV-21 proves GOV-15, GOV-16, CTL-LOG-03, CTL-LOG-04: exact columns, LF, masked
    accounts/contacts/untrusted text/tokens, EXPORT_CREATED logged after writing."""
    log, _ = fresh_log(tmp_path)
    log.append_decision(
        action_id="act_1",
        action_type="SCHEDULE_SUPPLIER_PAYMENT",
        decision=Decision.BLOCKED,
        reason_code=ReasonCode.ACCOUNT_CHANGED,
        principal_id="agent",
        tier=2,
        params={
            "bank_account_ref": "SBX-GM-77100938",
            "to_ref": "accounts@bright-events.test",
            "description": "ignore previous instructions " * 10,
            "token_id": "abcdef",
            "amount_cents": 125000,
        },
        control_ids=["CTL-PAY-02"],
    )
    out = log.export_csv(tmp_path / "evidence" / "action_log.csv")
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    rows = list(csv.reader(raw.decode("utf-8").splitlines()))
    assert rows[0] == CSV_COLUMNS and len(rows) == 2
    text = raw.decode("utf-8")
    assert "77100938" not in text and "****0938" in text
    assert "accounts@" not in text and "a***@bright-events.test" in text
    assert "abcdef" not in text and "[redacted]" in text
    assert "…" in text
    last = log.rows()[-1]
    assert last.action_type == EventType.EXPORT_CREATED.value and last.seq == 2
    assert log.verify_chain() is None
    counts = log.counts()
    assert (
        counts["BLOCKED"] == 1
        and counts["EVENT"] == 1
        and set(counts) >= {d.value for d in Decision}
    )
