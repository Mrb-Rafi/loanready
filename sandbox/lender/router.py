"""Pretend lender (PRD/04 §9.3; CTL-FIN-04, REG-LND-05).

Receives a loan pack, verifies the PDF hash and lists missing sections. There is no approve,
accept, sign, disburse or offer endpoint (SBX-06); the status never leaves
``received_under_review``."""

from __future__ import annotations

import base64
import binascii
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from contracts.canonical import bytes_sha256, sha256_hex
from contracts.sandbox_api import ErrorOut, LenderApplicationIn, LenderReceiptOut
from sandbox import db, idempotency
from sandbox.auth import require
from sandbox.clock import now_iso, parse_ts
from sandbox.errors import SandboxHTTPError
from sandbox.ratelimit import LIMITER
from sandbox.settings import get_settings

router = APIRouter(prefix="/lender", tags=["lender"], responses={"4XX": {"model": ErrorOut}})
LENDERS = frozenset({"lender_demo"})
# Same list as contracts.dto_outputs.LENDER_REQUIRED_SECTIONS (the sandbox may not import it).
LENDER_REQUIRED_SECTIONS = (
    "cover",
    "pnl",
    "cash_flow",
    "balance_sheet",
    "forecast",
    "reconciliation",
    "ageing",
    "readiness",
    "policies",
)


def receipt_out(row: dict) -> LenderReceiptOut:
    return LenderReceiptOut(
        receipt_id=row["receipt_id"],
        status=row["status"],
        pack_sha256=row["pack_sha256"],
        received_at=parse_ts(row["received_at"]),
        missing_sections=json.loads(row["missing_json"]),
    )


def _submit(req: LenderApplicationIn, key: str) -> tuple[int, dict]:
    if req.lender_id not in LENDERS:
        raise SandboxHTTPError("UNKNOWN_LENDER", f"no lender {req.lender_id}")
    try:
        pdf = base64.b64decode(req.pdf_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SandboxHTTPError("PDF_INVALID", "pdf_base64 is not valid base64") from exc
    if not pdf.startswith(b"%PDF-"):
        raise SandboxHTTPError("PDF_INVALID", "the document is not a PDF")
    if bytes_sha256(pdf) != req.pack_sha256:
        raise SandboxHTTPError("PACK_HASH_MISMATCH", "PDF bytes do not match pack_sha256")
    missing = [s for s in LENDER_REQUIRED_SECTIONS if s not in req.sections]
    receipt_id = "rcv_" + sha256_hex(key)[:12]
    inbox = get_settings().var_dir / "lender_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    pdf_path = inbox / f"{receipt_id}.pdf"
    pdf_path.write_bytes(pdf)
    with db.tx("lender") as c:
        db.run(
            c,
            "INSERT INTO applications (receipt_id, lender_id, business_name, "
            "requested_amount_cents, term_days, "
            "purpose, pack_sha256, sections_json, missing_json, pdf_path, status, received_at) "
            "VALUES "
            "(:r, :l, :b, :a, :t, :p, :h, :s, :m, :f, 'received_under_review', :ts)",
            r=receipt_id,
            l=req.lender_id,
            b=req.business_name,
            a=req.requested_amount_cents,
            t=req.term_days,
            p=req.purpose,
            h=req.pack_sha256,
            s=json.dumps(req.sections),
            m=json.dumps(missing),
            f=pdf_path.name,
            ts=now_iso(),
        )
        row = db.one(c, "SELECT * FROM applications WHERE receipt_id = :r", r=receipt_id)
    return 201, receipt_out(row).model_dump(mode="json")


@router.post("/applications", response_model=LenderReceiptOut, status_code=201)
def submit_application(
    req: LenderApplicationIn, request: Request, scope: str = Depends(require("write"))
) -> JSONResponse:
    key = idempotency.require_key(request)
    LIMITER.hit(scope, "lender")
    return idempotency.run(
        "lender", request, key, req.model_dump(mode="json"), lambda: _submit(req, key)
    )


@router.get("/applications/{receipt_id}", response_model=LenderReceiptOut)
def get_application(receipt_id: str, _scope: str = Depends(require("read"))) -> JSONResponse:
    with db.tx("lender") as c:
        row = db.one(c, "SELECT * FROM applications WHERE receipt_id = :r", r=receipt_id)
    if row is None:
        raise SandboxHTTPError("UNKNOWN_APPLICATION", f"no application {receipt_id}")
    return JSONResponse(receipt_out(row).model_dump(mode="json"))
