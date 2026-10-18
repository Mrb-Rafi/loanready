"""MockBank HTTP routes (PRD/04 §4.2). Every request settles due payments first."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from contracts.sandbox_api import (
    HEADER_NEXT_CURSOR,
    BalanceOut,
    BankAccountOut,
    BankTxnOut,
    ErrorOut,
    PayeeOut,
    PaymentOut,
    PaymentScheduleIn,
    StatementOut,
)
from sandbox import idempotency
from sandbox.auth import require
from sandbox.bank import service
from sandbox.ratelimit import LIMITER

router = APIRouter(prefix="/bank", tags=["bank"], responses={"4XX": {"model": ErrorOut}})


@router.get("/accounts", response_model=list[BankAccountOut])
def list_accounts(_scope: str = Depends(require("read"))) -> JSONResponse:
    return JSONResponse([a.model_dump(mode="json") for a in service.accounts()])


@router.get("/accounts/{account_id}/balance", response_model=BalanceOut)
def get_balance(account_id: str, _scope: str = Depends(require("read"))) -> JSONResponse:
    return JSONResponse(service.get_balance(account_id).model_dump(mode="json"))


@router.get("/accounts/{account_id}/transactions", response_model=list[BankTxnOut])
def list_transactions(
    account_id: str,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    limit: int = Query(500, ge=1, le=1000),
    cursor: str | None = Query(None),
    _scope: str = Depends(require("read")),
) -> JSONResponse:
    items, nxt = service.transactions(account_id, start, end, limit, cursor)
    headers = {HEADER_NEXT_CURSOR: nxt} if nxt else {}
    return JSONResponse([t.model_dump(mode="json") for t in items], headers=headers)


@router.get("/accounts/{account_id}/statement", response_model=StatementOut)
def get_statement(
    account_id: str,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    _scope: str = Depends(require("read")),
) -> JSONResponse:
    return JSONResponse(service.statement(account_id, month))


@router.get("/payees", response_model=list[PayeeOut])
def list_payees(_scope: str = Depends(require("read"))) -> JSONResponse:
    return JSONResponse([p.model_dump(mode="json") for p in service.payees()])


@router.get("/payments", response_model=list[PaymentOut])
def list_payments(
    status: str | None = Query(None), _scope: str = Depends(require("read"))
) -> JSONResponse:
    return JSONResponse([p.model_dump(mode="json") for p in service.payments(status)])


@router.get("/payments/{sbx_ref}", response_model=PaymentOut)
def get_payment(sbx_ref: str, _scope: str = Depends(require("read"))) -> JSONResponse:
    return JSONResponse(service.get_payment(sbx_ref).model_dump(mode="json"))


@router.post("/payments/schedule", response_model=PaymentOut, status_code=201)
def schedule_payment(
    req: PaymentScheduleIn, request: Request, scope: str = Depends(require("write"))
) -> JSONResponse:
    key = idempotency.require_key(request)
    LIMITER.hit(scope, "bank")
    return idempotency.run(
        "bank", request, key, req.model_dump(mode="json"), lambda: service.schedule(req, key)
    )


@router.post("/payments/{sbx_ref}/cancel", response_model=PaymentOut)
def cancel_payment(
    sbx_ref: str, request: Request, scope: str = Depends(require("write"))
) -> JSONResponse:
    key = idempotency.require_key(request)
    LIMITER.hit(scope, "bank")
    return idempotency.run(
        "bank", request, key, {"sbx_ref": sbx_ref}, lambda: service.cancel(sbx_ref)
    )
