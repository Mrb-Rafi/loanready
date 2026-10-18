"""T-ADP-01..04: read mapping, pagination, ticket enforcement, binding checks, error mapping."""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

import httpx
import pytest

from adapters import build_ports
from adapters.errors import ERROR_MAP, raise_for_error
from contracts.canonical import bytes_sha256, sha256_hex
from contracts.enums import ActionType, ReasonCode
from contracts.errors import SandboxError, TicketInvalid
from contracts.ports import BankPort, LenderPort, MessagePort, PosPort, SandboxAdminPort
from contracts.sandbox_api import LenderApplicationIn, MessageSendIn, PaymentScheduleIn
from contracts.testing.builders import make_action
from contracts.testing.fakes import accept_all_verifier, make_fake_ticket
from infra.sandbox_runner import asgi_transport
from sandbox.app import app

ACC, PAYEE, REF = "acc_operating", "pye_tiny_mills", "SBX-TM-00000001"
PDF = b"%PDF-1.4 adapter test pack\n"


def pay_req(**kw: Any) -> PaymentScheduleIn:
    base = dict(
        from_account_id=ACC,
        payee_id=PAYEE,
        bank_account_ref=REF,
        amount_cents=80000,
        value_date=date(2026, 10, 14),
        reference="BILL-1 via LoanReady",
    )
    base.update(kw)
    return PaymentScheduleIn(**base)


def pay_ticket(req: PaymentScheduleIn) -> Any:
    params = {"bill_id": "bil_tiny_1", **req.model_dump(exclude={"reference"})}
    return make_fake_ticket(make_action(ActionType.SCHEDULE_SUPPLIER_PAYMENT, params))


def msg_ticket(req: MessageSendIn, t: ActionType = ActionType.SEND_REMINDER) -> Any:
    params = {"to_ref": req.to_ref, "body_sha256": sha256_hex(req.body), "channel": req.channel}
    return make_fake_ticket(make_action(t, params))


def payment_requests(env: Any) -> int:
    return sum(
        1 for line in env.ports.admin.opslog_csv().splitlines() if "/bank/payments/schedule" in line
    )


def test_reads_map_to_dtos(env: Any) -> None:
    """T-ADP-01 proves SBX-A-01: ports satisfy their protocols; reads map to contract DTOs."""
    p = env.ports
    assert (
        isinstance(p.bank, BankPort)
        and isinstance(p.pos, PosPort)
        and isinstance(p.messaging, MessagePort)
    )
    assert isinstance(p.lender, LenderPort) and isinstance(p.admin, SandboxAdminPort)
    assert p.bank.list_accounts()[0].balance_cents == 271000
    assert p.bank.get_balance(ACC) == 271000
    assert p.bank.list_payees()[0].bank_account_ref == REF
    txns = p.bank.list_transactions(ACC, date(2026, 9, 1), date(2026, 10, 31))
    assert len(txns) == 7 and txns[-1].balance_after_cents == 271000
    assert [s.id for s in p.pos.list_sales(date(2026, 9, 1), date(2026, 10, 31))] == [
        "pos_tiny_01",
        "pos_tiny_02",
        "pos_tiny_03",
    ]
    assert p.admin.get_clock() == date(2026, 10, 14)
    assert p.admin.health()["status"] == "ok"
    assert p.admin.opslog_verify() == {"bank": None, "pos": None, "messaging": None, "lender": None}
    assert p.admin.set_clock(date(2026, 10, 14)) == date(2026, 10, 14)


def test_pagination_follows_cursor(env: Any) -> None:
    """T-ADP-01 proves SBX-A-02: get_all follows X-Next-Cursor until exhausted."""
    from adapters.http import ReadClient

    rc = ReadClient(
        "http://sandbox.local",
        env.settings.sandbox_read_token.get_secret_value(),
        asgi_transport(app),
    )
    rows = rc.get_all(
        f"/bank/accounts/{ACC}/transactions",
        {"start": "2026-01-01", "end": "2026-12-31", "limit": 2},
    )
    assert len(rows) == 7 and len({r["id"] for r in rows}) == 7
    rc.close()


def test_payment_with_valid_ticket(env: Any) -> None:
    """T-ADP-02 proves SBX-A-02, CTL-AC-06: a verified ticket goes through with
    Idempotency-Key = action_id and X-Ticket-Id; a replay of the same action is idempotent."""
    req = pay_req()
    ticket = pay_ticket(req)
    out = env.ports.bank.schedule_payment(ticket, req)
    assert out.status == "settled" and env.ports.bank.get_balance(ACC) == 191000
    assert env.ports.bank.get_payment(out.sbx_ref).status == "settled"
    assert len(env.ports.bank.list_payments("settled")) == 1
    ops = env.ports.admin.opslog_csv()
    assert ticket.action_id in ops and ticket.ticket_id in ops
    with pytest.raises(TicketInvalid, match="already used"):
        env.ports.bank.schedule_payment(ticket, req)


def test_write_without_valid_ticket_never_reaches_sandbox(env: Any) -> None:
    """T-ADP-02 proves CTL-AC-06: an expired, reused or wrong-type ticket raises TicketInvalid
    and the sandbox ops log shows no payment request."""
    req = pay_req()
    wrong_type = make_fake_ticket(
        make_action(ActionType.SUBMIT_LOAN_PACK, {"pack_sha256": "x", "lender_id": "y"})
    )
    with pytest.raises(TicketInvalid):
        env.ports.bank.schedule_payment(wrong_type, req)
    expired = pay_ticket(req).model_copy(update={"expires_at": pay_ticket(req).issued_at})
    from contracts.clock import get_clock

    get_clock().advance(120)  # type: ignore[attr-defined]
    with pytest.raises(TicketInvalid, match="expired"):
        env.ports.bank.schedule_payment(expired, req)
    assert payment_requests(env) == 0
    assert env.ports.bank.get_balance(ACC) == 271000


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_cents", 90000),
        ("payee_id", "pye_other"),
        ("bank_account_ref", "SBX-TM-77100938"),
        ("value_date", date(2026, 10, 15)),
        ("from_account_id", "acc_other"),
    ],
)
def test_payment_binding_mismatch(env: Any, field: str, value: Any) -> None:
    """T-ADP-03 proves SBX-A-03, CTL-AU-01: a request that differs from the ticket's binding
    (amount, payee, account, date, source account) is refused before any HTTP request."""
    ticket = pay_ticket(pay_req())
    with pytest.raises(TicketInvalid, match="binding"):
        env.ports.bank.schedule_payment(ticket, pay_req(**{field: value}))
    assert payment_requests(env) == 0


def test_message_binding_and_send(env: Any) -> None:
    """T-ADP-03 proves SBX-A-03: message binding = to_ref, body hash, channel; a changed
    body is refused."""
    req = MessageSendIn(
        channel="EMAIL", to_ref="ap@tiny-catering.test", subject="Reminder", body="Pay INV-1"
    )
    ticket = msg_ticket(req)
    with pytest.raises(TicketInvalid):
        env.ports.messaging.send(
            ticket, req.model_copy(update={"body": "Pay INV-1 to account 123"})
        )
    out = env.ports.messaging.send(ticket, req)
    assert out.status == "delivered_sandbox" and out.body_sha256 == sha256_hex(req.body)
    assert len(env.ports.messaging.list_messages()) == 1
    delay = msg_ticket(req, ActionType.SEND_DELAY_REQUEST)
    assert env.ports.messaging.send(delay, req).sbx_ref != out.sbx_ref
    not_send = make_fake_ticket(
        make_action(ActionType.SUBMIT_LOAN_PACK, {"pack_sha256": "x", "lender_id": "y"})
    )
    with pytest.raises(TicketInvalid, match="not for a message"):
        env.ports.messaging.send(not_send, req)


def test_lender_binding_and_submit(env: Any) -> None:
    """T-ADP-03 proves SBX-A-03, CTL-FIN-04: lender binding = hash of the decoded PDF and the
    lender id; a different PDF is refused; the real one gets a receipt."""
    req = LenderApplicationIn(
        lender_id="lender_demo",
        business_name="Tiny Bakery",
        requested_amount_cents=70000,
        term_days=30,
        purpose="Working capital",
        pack_sha256=bytes_sha256(PDF),
        sections=["cover", "pnl"],
        pdf_base64=base64.b64encode(PDF).decode(),
    )
    ticket = make_fake_ticket(
        make_action(
            ActionType.SUBMIT_LOAN_PACK,
            {"pack_sha256": bytes_sha256(PDF), "lender_id": "lender_demo"},
        )
    )
    other = req.model_copy(update={"pdf_base64": base64.b64encode(b"%PDF-1.4 other").decode()})
    with pytest.raises(TicketInvalid):
        env.ports.lender.submit_application(ticket, other)
    with pytest.raises(TicketInvalid):
        env.ports.lender.submit_application(ticket, req.model_copy(update={"pdf_base64": "!!"}))
    rec = env.ports.lender.submit_application(ticket, req)
    assert rec.status == "received_under_review" and "pnl" not in rec.missing_sections
    assert env.ports.lender.get_application(rec.receipt_id) == rec


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"payee_id": "pye_stranger"}, ReasonCode.NEW_PAYEE),
        ({"bank_account_ref": "SBX-TM-77100938"}, ReasonCode.ACCOUNT_CHANGED),
        ({"amount_cents": 300000}, ReasonCode.INSUFFICIENT_FUNDS),
        ({"amount_cents": 500001}, ReasonCode.BANK_LIMIT_EXCEEDED),
        ({"value_date": date(2026, 10, 13)}, ReasonCode.INVALID_PARAMS),
    ],
)
def test_error_mapping_from_live_sandbox(settings: Any, kw: dict, reason: ReasonCode) -> None:
    """T-ADP-04 proves SBX-A-04: sandbox refusals become SandboxError with the mapped reason
    (403 PAYEE_UNKNOWN -> NEW_PAYEE, ACCOUNT_MISMATCH -> ACCOUNT_CHANGED,
    409 -> INSUFFICIENT_FUNDS)."""
    from contracts.testing.builders import load_tiny_bundle
    from sandbox.settings import reset_settings

    reset_settings()
    t = asgi_transport(app)
    ports = build_ports(settings, accept_all_verifier, t)
    ports.admin.reset(load_tiny_bundle())
    req = pay_req(**kw)
    with pytest.raises(SandboxError) as exc:
        ports.bank.schedule_payment(pay_ticket(req), req)
    assert exc.value.reason_code == reason and exc.value.http_status in (403, 409, 422)
    assert exc.value.sandbox_code
    t.close()


@pytest.mark.parametrize("code", sorted(ERROR_MAP))
def test_every_catalogue_code_maps(code: str) -> None:
    """T-ADP-04 proves SBX-A-04: every catalogue code maps; unknown codes -> SANDBOX_ERROR."""
    resp = httpx.Response(
        409,
        json={"code": code, "message": "m", "details": None},
        headers={"Retry-After": "7"},
        request=httpx.Request("GET", "http://x"),
    )
    with pytest.raises(SandboxError) as exc:
        raise_for_error(resp)
    assert exc.value.reason_code == ERROR_MAP[code] and exc.value.details["retry_after"] == "7"
    with pytest.raises(SandboxError) as unknown:
        raise_for_error(httpx.Response(500, text="boom", request=httpx.Request("GET", "http://x")))
    assert unknown.value.reason_code == ReasonCode.SANDBOX_ERROR
    raise_for_error(httpx.Response(200, json={}, request=httpx.Request("GET", "http://x")))


def test_retry_once_on_connection_error(settings: Any) -> None:
    """SBX-A-05: one automatic retry only on connection errors, with the same request."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Idempotency-Key"))
        if len(calls) == 1:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(
            201,
            json={
                "sbx_ref": "pay_x",
                "from_account_id": ACC,
                "payee_id": PAYEE,
                "amount_cents": 80000,
                "value_date": "2026-10-14",
                "reference": "BILL-1 via LoanReady",
                "status": "scheduled",
                "failure_code": None,
                "settled_on": None,
                "created_at": "2026-10-14T03:00:00Z",
            },
        )

    ports = build_ports(settings, accept_all_verifier, httpx.MockTransport(handler))
    req = pay_req()
    ticket = pay_ticket(req)
    out = ports.bank.schedule_payment(ticket, req)
    assert out.sbx_ref == "pay_x" and calls == [ticket.action_id, ticket.action_id]

    def always_429(request: httpx.Request) -> httpx.Response:
        calls.append("429")
        return httpx.Response(
            429, json={"code": "RATE_LIMITED", "message": "slow"}, headers={"Retry-After": "5"}
        )

    calls.clear()
    ports = build_ports(settings, accept_all_verifier, httpx.MockTransport(always_429))
    with pytest.raises(SandboxError) as exc:
        ports.bank.schedule_payment(pay_ticket(req), req)
    assert exc.value.reason_code == ReasonCode.RATE_LIMITED and calls == ["429"]  # no retry on HTTP


def test_external_mode_uses_sandbox_url(settings: Any) -> None:
    """SBX-A-01: without a transport the ports point at SANDBOX_URL."""
    ports = build_ports(settings, accept_all_verifier)
    assert str(ports.bank._read._client.base_url).startswith(settings.sandbox_url)  # type: ignore[attr-defined]
