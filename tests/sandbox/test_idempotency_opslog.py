"""T-SBX-03, -08, -13, -14: idempotency and the hash-chained operation log."""

from __future__ import annotations

import sqlite3
from typing import Any

from sandbox import db, opslog


def test_idempotent_replay(sbx: Any) -> None:
    """T-SBX-03 proves SBX-07, CTL-PAY-04: same key + same body -> same response with the
    replay header and no second payment; same key + different body -> 422 conflict."""
    first = sbx.pay("act_same", amount_cents=10000)
    again = sbx.pay("act_same", amount_cents=10000)
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json() and again.headers.get("Idempotent-Replay") == "true"
    assert "Idempotent-Replay" not in first.headers
    assert sbx.balance()["balance_cents"] == 261000
    assert len(sbx.get("/bank/payments").json()) == 1
    conflict = sbx.pay("act_same", amount_cents=10001)
    assert conflict.status_code == 422 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    other_path = sbx.client.post(
        "/messaging/send",
        json={"channel": "EMAIL", "to_ref": "a@b.test", "subject": "s", "body": "b"},
        headers=sbx.w("act_same"),
    )
    assert other_path.status_code == 201  # keys are per sub-app store


def test_key_required(sbx: Any) -> None:
    """SBX-03: writes need a valid Idempotency-Key."""
    r = sbx.client.post("/bank/payments/schedule", json={}, headers=sbx.write)
    assert r.status_code in (400, 422)
    body = {
        "from_account_id": "acc_operating",
        "payee_id": "pye_tiny_mills",
        "bank_account_ref": "SBX-TM-00000001",
        "amount_cents": 1,
        "value_date": "2026-10-14",
        "reference": "r",
    }
    r = sbx.client.post("/bank/payments/schedule", json=body, headers=sbx.write)
    assert r.status_code == 400 and r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    r = sbx.client.post(
        "/bank/payments/schedule", json=body, headers={**sbx.write, "Idempotency-Key": "bad key!"}
    )
    assert r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_error_replay(sbx: Any) -> None:
    """T-SBX-13 proves SBX-07: an error response is replayed as the same error even after the
    cause disappears (no new side effect)."""
    r1 = sbx.pay("act_err", payee_id="pye_new", bank_account_ref="SBX-NW-00000009")
    assert r1.json()["code"] == "PAYEE_UNKNOWN"
    sbx.client.post(
        "/admin/payees",
        json={"id": "pye_new", "name": "New", "bank_account_ref": "SBX-NW-00000009"},
        headers=sbx.admin,
    )
    r2 = sbx.pay("act_err", payee_id="pye_new", bank_account_ref="SBX-NW-00000009")
    assert (
        r2.status_code == 403
        and r2.json() == r1.json()
        and r2.headers["Idempotent-Replay"] == "true"
    )
    assert (
        sbx.pay("act_err2", payee_id="pye_new", bank_account_ref="SBX-NW-00000009").status_code
        == 201
    )


def test_opslog_rows_for_every_request(sbx: Any) -> None:
    """T-SBX-08 proves SBX-04, CTL-LOG-05: a row for every request including rejected ones;
    scope name, key and ticket id recorded; no token values or bodies stored."""
    sbx.pay("act_ops", amount_cents=10000)
    sbx.client.post("/bank/payments/schedule", json={}, headers={"Authorization": "Bearer wrong"})
    sbx.client.post(
        "/bank/payments/schedule", json={}, headers=sbx.w("act_x", **{"X-Ticket-Id": "tkt_123"})
    )
    rows = sbx.ops()
    paths = [(r[2], r[3], r[4], r[5], r[8], r[9]) for r in rows]
    assert ("bank", "POST", "/bank/payments/schedule", "write", "201", "") in paths
    assert ("bank", "POST", "/bank/payments/schedule", "none", "401", "UNAUTHENTICATED") in paths
    assert any(r[7] == "tkt_123" and r[9] == "VALIDATION_ERROR" for r in rows)
    assert any(r[6] == "act_ops" for r in rows)
    csv_text = sbx.client.get("/admin/opslog.csv", headers=sbx.admin).text
    assert "SBX-TM-00000001" not in csv_text and "BILL-1 via LoanReady" not in csv_text
    assert sbx.client.get("/admin/opslog/verify", headers=sbx.admin).json() == {
        "bank": None,
        "pos": None,
        "messaging": None,
        "lender": None,
    }


def test_opslog_tamper_detected(sbx: Any) -> None:
    """T-SBX-14 proves SBX-10, CTL-LOG-05: per-sub-app chain; a change is found at its seq."""
    for _i in range(4):
        sbx.get("/bank/accounts")
        sbx.get("/pos/sales", params={"start": "2026-09-01", "end": "2026-10-31"})
    assert opslog.verify_all() == {"bank": None, "pos": None, "messaging": None, "lender": None}
    con = sqlite3.connect(db.db_path("pos"))
    con.execute("UPDATE ops_log SET status_code = 500 WHERE seq = 2")
    con.commit()
    con.close()
    result = opslog.verify_all()
    assert result["pos"] == 2 and result["bank"] is None
    con = sqlite3.connect(db.db_path("bank"))
    con.execute("DELETE FROM ops_log WHERE seq = 3")
    con.commit()
    con.close()
    assert opslog.verify("bank") == 3
    assert opslog.subapp_for("/health") == "bank" and opslog.subapp_for("/lender/x") == "lender"
