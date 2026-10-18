"""Admin endpoints (PRD/04 §4.4, §9.4): reset from a SeedBundle, payee registry, clock,
limits, ops-log export and verification. Admin token only. Admin writes do not use
Idempotency-Key (they are operator/test actions, never app actions)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from contracts.canonical import canonical_json, sha256_hex
from contracts.sandbox_api import AdminPayeeIn, ClockIn, ClockOut, ErrorOut, PayeeOut, PaymentOut
from contracts.seed import SeedBundle
from sandbox import db, opslog
from sandbox.auth import require
from sandbox.bank import limits, service
from sandbox.bank.settlement import insert_txn, settle_due
from sandbox.clock import TS_FMT, now_iso, set_meta, today
from sandbox.errors import SandboxHTTPError
from sandbox.pos.router import insert_sale
from sandbox.ratelimit import LIMITER
from sandbox.settings import get_settings

router = APIRouter(prefix="/admin", tags=["admin"], responses={"4XX": {"model": ErrorOut}})
SCHEMA_VERSION = "1"


class LimitsIn(BaseModel):
    max_payment_cents: int = Field(gt=0)
    daily_outgoing_cents: int = Field(gt=0)


def reset(bundle: SeedBundle) -> dict[str, int]:
    """Drop and recreate all four DBs, then load the bundle (PRD/04 §9.4)."""
    db.reset_all()
    LIMITER.reset()
    s = get_settings()
    with db.tx("bank") as c:
        set_meta(c, "today", bundle.as_of.isoformat())
        set_meta(c, "schema_version", SCHEMA_VERSION)
        set_meta(c, "bundle_sha256", sha256_hex(canonical_json(bundle)))
        for acc in bundle.bank_accounts:
            db.run(
                c,
                "INSERT INTO accounts (id, name, currency, status, overdraft_limit_cents, "
                "created_at) "
                "VALUES (:i, :n, :c, :s, 0, :t)",
                i=acc.id,
                n=acc.name,
                c=acc.currency,
                s=acc.status,
                t=now_iso(),
            )
            limits.set_limits(c, acc.id, s.max_payment_cents, s.daily_outgoing_cents)
        for p in bundle.bank_payees:
            service.add_payee(c, p.id, p.name, p.bank_account_ref, created_by="seed")
            db.run(
                c,
                "UPDATE payees SET status = :s, first_paid_on = :f WHERE id = :i",
                s=p.status,
                f=p.first_paid_on.isoformat() if p.first_paid_on else None,
                i=p.id,
            )
        for acc in bundle.bank_accounts:
            seed = [t for t in bundle.bank_transactions if t.account_id == acc.id]
            opening = acc.balance_cents - sum(t.amount_cents for t in seed)
            insert_txn(
                c,
                txn_id=f"txn_opening_{acc.id}",
                account_id=acc.id,
                d=bundle.history_start,
                amount_cents=opening,
                description="OPENING BALANCE",
                counterparty=None,
                kind="opening",
                payment_ref=None,
            )
            for t in sorted(seed, key=lambda x: x.date):
                insert_txn(
                    c,
                    txn_id=t.id,
                    account_id=acc.id,
                    d=t.date,
                    amount_cents=t.amount_cents,
                    description=str(t.description),
                    counterparty=t.counterparty,
                    kind="seed",
                    payment_ref=t.payment_ref,
                )
        counts = {
            "accounts": len(bundle.bank_accounts),
            "payees": len(bundle.bank_payees),
            "transactions": db.one(c, "SELECT COUNT(*) AS n FROM transactions")["n"],
        }
    with db.tx("pos") as c:
        for sale in bundle.pos_sales:
            insert_sale(c, sale, sale.ts.strftime(TS_FMT))
    counts["pos_sales"] = len(bundle.pos_sales)
    return counts


@router.post("/reset")
def admin_reset(bundle: SeedBundle, _scope: str = Depends(require("admin"))) -> dict[str, int]:
    return reset(bundle)


@router.post("/payees", response_model=PayeeOut, status_code=201)
def admin_add_payee(req: AdminPayeeIn, _scope: str = Depends(require("admin"))) -> JSONResponse:
    with db.tx("bank") as c:
        service.add_payee(c, req.id, req.name, req.bank_account_ref)
        row = db.one(c, "SELECT * FROM payees WHERE id = :i", i=req.id)
    return JSONResponse(service.payee_out(row).model_dump(mode="json"), status_code=201)


@router.post("/payees/{payee_id}/close", response_model=PayeeOut)
def admin_close_payee(payee_id: str, _scope: str = Depends(require("admin"))) -> JSONResponse:
    return JSONResponse(service.close_payee(payee_id).model_dump(mode="json"))


@router.post("/payments/{sbx_ref}/return", response_model=PaymentOut)
def admin_return_payment(sbx_ref: str, _scope: str = Depends(require("admin"))) -> JSONResponse:
    return JSONResponse(service.return_payment(sbx_ref).model_dump(mode="json"))


@router.get("/clock", response_model=ClockOut)
def admin_get_clock(_scope: str = Depends(require("admin"))) -> dict[str, Any]:
    return {"today": today().isoformat()}


@router.post("/clock", response_model=ClockOut)
def admin_set_clock(req: ClockIn, _scope: str = Depends(require("admin"))) -> dict[str, Any]:
    """Moves forward only, then settles due payments (SHOULD)."""
    with db.tx("bank") as c:
        current = today(c)
        if req.today < current:
            raise SandboxHTTPError("CLOCK_BACKWARDS", f"cannot move the clock back from {current}")
        set_meta(c, "today", req.today.isoformat())
        settle_due(c)
    return {"today": req.today.isoformat()}


@router.put("/limits/{account_id}")
def admin_set_limits(
    account_id: str, req: LimitsIn, _scope: str = Depends(require("admin"))
) -> dict[str, Any]:
    with db.tx("bank") as c:
        if db.one(c, "SELECT id FROM accounts WHERE id = :a", a=account_id) is None:
            raise SandboxHTTPError("UNKNOWN_ACCOUNT", f"no account {account_id}")
        limits.set_limits(c, account_id, req.max_payment_cents, req.daily_outgoing_cents)
        return limits.get_limits(c, account_id)


@router.get("/opslog.csv", response_class=PlainTextResponse)
def admin_opslog_csv(_scope: str = Depends(require("admin"))) -> PlainTextResponse:
    return PlainTextResponse(opslog.export_csv(), media_type="text/csv; charset=utf-8")


@router.get("/opslog/verify")
def admin_opslog_verify(_scope: str = Depends(require("admin"))) -> dict[str, int | None]:
    return opslog.verify_all()
