"""MockBank: T-SBX-02, -04, -07, -09, -10 (limits), -11, -12, -17."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

ACC = "acc_operating"


def test_reset_loads_bundle(sbx: Any) -> None:
    """T-SBX-07 proves PRD/04 §9.4: reset loads the bundle; the opening entry makes the last
    seed transaction's balance_after equal the account balance; today = AS_OF."""
    txns = sbx.get(
        f"/bank/accounts/{ACC}/transactions", params={"start": "2026-01-01", "end": "2026-12-31"}
    ).json()
    assert txns[0]["kind"] == "opening" and txns[0]["amount_cents"] == 200000
    assert txns[-1]["balance_after_cents"] == 271000
    assert sbx.balance() == {
        "account_id": ACC,
        "balance_cents": 271000,
        "available_cents": 271000,
        "today": "2026-10-14",
    }
    acc = sbx.get("/bank/accounts").json()
    assert acc == [
        {
            "id": ACC,
            "name": "Operating account",
            "currency": "USD",
            "status": "open",
            "balance_cents": 271000,
        }
    ]
    assert sbx.get("/bank/payees").json()[0]["status"] == "known"
    assert sbx.client.get("/health").json()["today"] == "2026-10-14"


def test_balance_derived_and_running(sbx: Any) -> None:
    """T-SBX-12 proves PRD/04 §4.1: balance = sum of transactions; balance_after equals the
    running sum; statement opening/closing."""
    txns = sbx.get(
        f"/bank/accounts/{ACC}/transactions", params={"start": "2026-01-01", "end": "2026-12-31"}
    ).json()
    running = 0
    for t in txns:
        running += t["amount_cents"]
        assert t["balance_after_cents"] == running
    st = sbx.get(f"/bank/accounts/{ACC}/statement", params={"month": "2026-10"}).json()
    assert st["opening_cents"] == 200000 - 30000 + 100000  # end of September
    assert st["closing_cents"] == 271000
    assert st["closing_cents"] == st["opening_cents"] + sum(
        t["amount_cents"] for t in st["transactions"]
    )
    assert (
        sbx.get(f"/bank/accounts/{ACC}/statement", params={"month": "2026-13"}).status_code == 422
    )
    assert sbx.get("/bank/accounts/acc_x/balance").json()["code"] == "UNKNOWN_ACCOUNT"


def test_pagination(sbx: Any) -> None:
    """T-SBX-11 proves PRD/04 §4.2: the cursor walk returns every row exactly once; an invalid
    cursor -> 422 INVALID_CURSOR."""
    params = {"start": "2026-01-01", "end": "2026-12-31"}
    full = [t["id"] for t in sbx.get(f"/bank/accounts/{ACC}/transactions", params=params).json()]
    seen, cursor = [], None
    while True:
        p = {**params, "limit": 2, **({"cursor": cursor} if cursor else {})}
        r = sbx.get(f"/bank/accounts/{ACC}/transactions", params=p)
        seen += [t["id"] for t in r.json()]
        cursor = r.headers.get("X-Next-Cursor")
        if not cursor:
            break
    assert seen == full and len(set(seen)) == len(full) == 7
    for bad in ("%%%", "bm9fc2VwYXJhdG9y", "MjAyNi0xMC0xNHxhYmM"):
        r = sbx.get(f"/bank/accounts/{ACC}/transactions", params={**params, "cursor": bad})
        assert r.status_code == 422 and r.json()["code"] == "INVALID_CURSOR"
    assert (
        sbx.get(f"/bank/accounts/{ACC}/transactions", params={**params, "limit": 0}).json()["code"]
        == "VALIDATION_ERROR"
    )


def test_schedule_success_same_day_settles(sbx: Any) -> None:
    """T-SBX-02 / T-SBX-09 prove SBX-05, §4.3: a same-day payment to a known payee settles in
    the schedule response; the transaction appears; first_paid_on is set."""
    r = sbx.pay("act_pay_1", amount_cents=80000)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "settled" and body["settled_on"] == "2026-10-14"
    assert body["sbx_ref"].startswith("pay_")
    assert sbx.balance()["balance_cents"] == 191000
    txns = sbx.get(
        f"/bank/accounts/{ACC}/transactions", params={"start": "2026-10-14", "end": "2026-10-14"}
    ).json()
    assert txns[-1]["kind"] == "payment" and txns[-1]["payment_ref"] == body["sbx_ref"]
    assert txns[-1]["description"] == "Tiny Mills BILL-1 via LoanReady"
    assert sbx.get("/bank/payees").json()[0]["first_paid_on"] == "2026-10-14"
    assert sbx.get(f"/bank/payments/{body['sbx_ref']}").json()["status"] == "settled"
    assert len(sbx.get("/bank/payments", params={"status": "settled"}).json()) == 1
    assert sbx.get("/bank/payments/pay_missing").json()["code"] == "UNKNOWN_PAYMENT"


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"payee_id": "pye_stranger"}, 403, "PAYEE_UNKNOWN"),
        ({"bank_account_ref": "SBX-TM-77100938"}, 403, "ACCOUNT_MISMATCH"),
        ({"from_account_id": "acc_x"}, 404, "UNKNOWN_ACCOUNT"),
        ({"amount_cents": 0}, 422, "INVALID_AMOUNT"),
        ({"value_date": "2026-10-13"}, 422, "INVALID_VALUE_DATE"),
        ({"value_date": "2027-01-13"}, 422, "VALUE_DATE_TOO_FAR"),
        ({"amount_cents": 500001}, 409, "BANK_LIMIT_EXCEEDED"),
        ({"amount_cents": 300000}, 409, "INSUFFICIENT_FUNDS"),
        ({"reference": "x" * 61}, 422, "VALIDATION_ERROR"),
    ],
)
def test_schedule_rules(sbx: Any, body: dict, status: int, code: str) -> None:
    """T-SBX-02 proves SBX-05, CTL-PAY-02, REG-PAY-02: the bank's own checks, in order."""
    r = sbx.pay(f"act_{code.lower()}", **body)
    assert r.status_code == status and r.json()["code"] == code, r.text
    assert sbx.balance()["balance_cents"] == 271000


def test_closed_payee_and_daily_limit(sbx: Any) -> None:
    """T-SBX-10 proves CTL-LIM-06: daily outgoing limit per value date; closed payee 403."""
    sbx.client.put(
        f"/admin/limits/{ACC}",
        json={"max_payment_cents": 500000, "daily_outgoing_cents": 100000},
        headers=sbx.admin,
    )
    assert sbx.pay("d1", amount_cents=60000).status_code == 201
    r = sbx.pay("d2", amount_cents=50000)
    assert r.status_code == 409 and r.json()["details"] == {"limit": "daily"}
    assert sbx.pay("d3", amount_cents=50000, value_date="2026-10-15").status_code == 201
    assert (
        sbx.client.post("/admin/payees/pye_tiny_mills/close", headers=sbx.admin).json()["status"]
        == "closed"
    )
    assert sbx.pay("d4").json()["code"] == "PAYEE_CLOSED"
    assert (
        sbx.client.put(
            "/admin/limits/acc_x",
            json={"max_payment_cents": 1, "daily_outgoing_cents": 1},
            headers=sbx.admin,
        ).json()["code"]
        == "UNKNOWN_ACCOUNT"
    )


def test_future_dated_and_settlement_state_machine(sbx: Any) -> None:
    """T-SBX-09 / T-SBX-17 prove §4.3, SBX-09: future-dated stays scheduled and lowers only the
    available balance; advancing the clock settles it; payee closed -> failed; settle_due is
    idempotent; the clock never moves back."""
    r = sbx.pay("f1", amount_cents=50000, value_date="2026-10-16").json()
    assert r["status"] == "scheduled"
    assert sbx.balance()["balance_cents"] == 271000 and sbx.balance()["available_cents"] == 221000
    second = sbx.pay("f2", amount_cents=20000, value_date="2026-10-16").json()
    sbx.client.post(
        "/admin/payees",
        json={"id": "pye_two", "name": "Two", "bank_account_ref": "SBX-TW-00000002"},
        headers=sbx.admin,
    )
    third = sbx.pay(
        "f3",
        payee_id="pye_two",
        bank_account_ref="SBX-TW-00000002",
        amount_cents=10000,
        value_date="2026-10-16",
    ).json()
    sbx.client.post("/admin/payees/pye_two/close", headers=sbx.admin)
    assert sbx.clock(date(2026, 10, 13)).json()["code"] == "CLOCK_BACKWARDS"
    assert sbx.clock(date(2026, 10, 16)).json() == {"today": "2026-10-16"}
    assert sbx.get(f"/bank/payments/{r['sbx_ref']}").json()["status"] == "settled"
    assert sbx.get(f"/bank/payments/{second['sbx_ref']}").json()["status"] == "settled"
    failed = sbx.get(f"/bank/payments/{third['sbx_ref']}").json()
    assert failed["status"] == "failed" and failed["failure_code"] == "PAYEE_ACCOUNT_CLOSED"
    assert sbx.balance()["balance_cents"] == 201000
    assert sbx.clock(date(2026, 10, 16)).status_code == 200  # same day again: nothing changes
    assert sbx.balance()["balance_cents"] == 201000


def test_insufficient_funds_at_settlement(sbx: Any) -> None:
    """T-SBX-09 proves §4.3: funds short at settlement (an outside debit after scheduling) ->
    failed INSUFFICIENT_FUNDS_AT_SETTLEMENT, no transaction."""
    from sandbox import db
    from sandbox.bank.settlement import insert_txn

    a = sbx.pay("s1", amount_cents=200000, value_date="2026-10-15").json()
    with db.tx("bank") as c:
        insert_txn(
            c,
            txn_id="txn_outside",
            account_id=ACC,
            d=date(2026, 10, 14),
            amount_cents=-150000,
            description="OUTSIDE DEBIT",
            counterparty=None,
            kind="admin",
            payment_ref=None,
        )
    sbx.clock(date(2026, 10, 15))
    out = sbx.get(f"/bank/payments/{a['sbx_ref']}").json()
    assert out["status"] == "failed" and out["failure_code"] == "INSUFFICIENT_FUNDS_AT_SETTLEMENT"
    assert sbx.balance()["balance_cents"] == 121000


def test_cancel_and_return(sbx: Any) -> None:
    """T-SBX-04 proves CTL-PAY-05: cancel before the value date; after (or once settled) ->
    409 NOT_CANCELLABLE; admin return of a settled payment credits the money back."""
    fut = sbx.pay("c1", amount_cents=30000, value_date="2026-10-20").json()
    r = sbx.client.post(f"/bank/payments/{fut['sbx_ref']}/cancel", headers=sbx.w("c1-cancel"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert sbx.balance()["available_cents"] == 271000
    now = sbx.pay("c2", amount_cents=30000).json()
    r = sbx.client.post(f"/bank/payments/{now['sbx_ref']}/cancel", headers=sbx.w("c2-cancel"))
    assert r.status_code == 409 and r.json()["code"] == "NOT_CANCELLABLE"
    assert (
        sbx.client.post("/bank/payments/pay_x/cancel", headers=sbx.w("c3")).json()["code"]
        == "UNKNOWN_PAYMENT"
    )
    ret = sbx.client.post(f"/admin/payments/{now['sbx_ref']}/return", headers=sbx.admin).json()
    assert ret["status"] == "returned" and sbx.balance()["balance_cents"] == 271000
    again = sbx.client.post(f"/admin/payments/{now['sbx_ref']}/return", headers=sbx.admin)
    assert again.json()["code"] == "NOT_CANCELLABLE"
    assert (
        sbx.client.post("/admin/payments/pay_x/return", headers=sbx.admin).json()["code"]
        == "UNKNOWN_PAYMENT"
    )
    assert (
        sbx.client.post("/admin/payees/pye_x/close", headers=sbx.admin).json()["code"]
        == "PAYEE_UNKNOWN"
    )
