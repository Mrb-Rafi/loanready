"""POS, messaging and pretend lender: T-SBX-05, T-SBX-06."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest

from sandbox.app import app

PDF = b"%PDF-1.4 tiny loan pack\n"
SECTIONS = [
    "cover",
    "pnl",
    "cash_flow",
    "balance_sheet",
    "forecast",
    "reconciliation",
    "ageing",
    "readiness",
    "policies",
]


def application(**kw: Any) -> dict[str, Any]:
    body = {
        "lender_id": "lender_demo",
        "business_name": "Tiny Bakery",
        "requested_amount_cents": 70000,
        "term_days": 30,
        "purpose": "Working capital",
        "pack_sha256": hashlib.sha256(PDF).hexdigest(),
        "sections": SECTIONS,
        "pdf_base64": base64.b64encode(PDF).decode(),
    }
    body.update(kw)
    return body


def test_pos_sales_and_pagination(sbx: Any) -> None:
    """PRD/04 §9.1: sales with items, tender and note; date filter; cursor pagination."""
    sales = sbx.get("/pos/sales", params={"start": "2026-09-01", "end": "2026-10-31"}).json()
    assert [s["id"] for s in sales] == ["pos_tiny_01", "pos_tiny_02", "pos_tiny_03"]
    assert (
        sales[0]["amount_cents"]
        == sum(i["qty"] * i["unit_cents"] for i in sales[0]["items"])
        == 100000
    )
    assert sales[0]["tender"] == "cash"
    one = sbx.get("/pos/sales", params={"start": "2026-10-06", "end": "2026-10-06"}).json()
    assert [s["id"] for s in one] == ["pos_tiny_02"]
    page = sbx.get("/pos/sales", params={"start": "2026-09-01", "end": "2026-10-31", "limit": 2})
    nxt = page.headers["X-Next-Cursor"]
    rest = sbx.get(
        "/pos/sales", params={"start": "2026-09-01", "end": "2026-10-31", "limit": 2, "cursor": nxt}
    )
    assert [s["id"] for s in page.json() + rest.json()] == [
        "pos_tiny_01",
        "pos_tiny_02",
        "pos_tiny_03",
    ]
    assert "X-Next-Cursor" not in rest.headers


@pytest.mark.parametrize(
    "channel,to_ref,ok",
    [
        ("EMAIL", "ap@tiny-catering.test", True),
        ("EMAIL", "x@shop.example", True),
        ("SMS", "+9995550101", True),
        ("EMAIL", "real@gmail.com", False),
        ("SMS", "+8801711000000", False),
        ("SMS", "+999123", False),
        ("EMAIL", "ap@tiny-catering.test.com", False),
    ],
)
def test_messaging_reserved_addresses(sbx: Any, channel: str, to_ref: str, ok: bool) -> None:
    """T-SBX-05 proves PRD/04 §9.2: only reserved fake ranges are accepted."""
    r = sbx.client.post(
        "/messaging/send",
        json={"channel": channel, "to_ref": to_ref, "subject": "Hi", "body": "Body"},
        headers=sbx.w(f"m-{abs(hash(to_ref))}"),
    )
    if ok:
        assert r.status_code == 201
        out = r.json()
        assert (
            out["status"] == "delivered_sandbox"
            and out["body_sha256"] == hashlib.sha256(b"Body").hexdigest()
        )
        assert out["sbx_ref"].startswith("msg_")
    else:
        assert r.status_code == 422 and r.json()["code"] == "INVALID_RECIPIENT"


def test_messaging_body_limit_and_list(sbx: Any) -> None:
    """T-SBX-05: body over 2,000 chars refused; sent messages listed (no body returned)."""
    r = sbx.client.post(
        "/messaging/send",
        json={"channel": "EMAIL", "to_ref": "a@b.test", "subject": "s", "body": "x" * 2001},
        headers=sbx.w("long"),
    )
    assert r.json()["code"] == "MESSAGE_TOO_LONG"
    sbx.client.post(
        "/messaging/send",
        json={"channel": "EMAIL", "to_ref": "a@b.test", "subject": "s", "body": "ok"},
        headers=sbx.w("ok1"),
    )
    msgs = sbx.get("/messaging/messages").json()
    assert len(msgs) == 1 and "body" not in msgs[0]


def test_lender_receipt_and_checks(sbx: Any) -> None:
    """T-SBX-06 proves CTL-FIN-04: receipt with status received_under_review; hash mismatch
    422; not a PDF 422; unknown lender 404; missing sections listed; receipt retrievable."""
    r = sbx.client.post("/lender/applications", json=application(), headers=sbx.w("pk1"))
    assert r.status_code == 201
    rec = r.json()
    assert rec["status"] == "received_under_review" and rec["missing_sections"] == []
    assert rec["receipt_id"].startswith("rcv_")
    assert sbx.get(f"/lender/applications/{rec['receipt_id']}").json() == rec
    assert sbx.get("/lender/applications/rcv_missing").json()["code"] == "UNKNOWN_APPLICATION"
    bad = sbx.client.post(
        "/lender/applications", json=application(pack_sha256="0" * 64), headers=sbx.w("pk2")
    )
    assert bad.status_code == 422 and bad.json()["code"] == "PACK_HASH_MISMATCH"
    notpdf = base64.b64encode(b"hello").decode()
    assert (
        sbx.client.post(
            "/lender/applications", json=application(pdf_base64=notpdf), headers=sbx.w("pk3")
        ).json()["code"]
        == "PDF_INVALID"
    )
    assert (
        sbx.client.post(
            "/lender/applications", json=application(pdf_base64="***"), headers=sbx.w("pk3b")
        ).json()["code"]
        == "PDF_INVALID"
    )
    assert (
        sbx.client.post(
            "/lender/applications", json=application(lender_id="lender_x"), headers=sbx.w("pk4")
        ).json()["code"]
        == "UNKNOWN_LENDER"
    )
    partial = sbx.client.post(
        "/lender/applications", json=application(sections=["cover", "pnl"]), headers=sbx.w("pk5")
    ).json()
    assert set(partial["missing_sections"]) == set(SECTIONS) - {"cover", "pnl"}


def test_no_approve_or_accept_route() -> None:
    """T-SBX-06 proves SBX-06, REG-LND-05: no approve, accept, sign, disburse or offer route."""
    words = ("approve", "accept", "sign", "disburse", "offer")
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if any(w in p.lower() for w in words)]
