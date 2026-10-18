"""Messaging sandbox (PRD/04 §9.2). Only reserved fake addresses are accepted, so nothing can
reach a real person: emails ending ``.test`` / ``.example``, phones ``+999`` plus 7-10 digits.
Messages are stored with status ``delivered_sandbox``; nothing leaves the machine."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from contracts.canonical import sha256_hex
from contracts.sandbox_api import ErrorOut, MessageOut, MessageSendIn
from sandbox import db, idempotency
from sandbox.auth import require
from sandbox.clock import now_iso, parse_ts
from sandbox.errors import SandboxHTTPError
from sandbox.ratelimit import LIMITER

router = APIRouter(prefix="/messaging", tags=["messaging"], responses={"4XX": {"model": ErrorOut}})
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.(test|example)$")
SMS_RE = re.compile(r"^\+999\d{7,10}$")
MAX_BODY = 2000


def message_out(row: dict) -> MessageOut:
    return MessageOut(
        sbx_ref=row["sbx_ref"],
        channel=row["channel"],
        to_ref=row["to_ref"],
        subject=row["subject"],
        body_sha256=row["body_sha256"],
        status=row["status"],
        created_at=parse_ts(row["created_at"]),
    )


def _send(req: MessageSendIn, key: str) -> tuple[int, dict]:
    pattern = EMAIL_RE if req.channel == "EMAIL" else SMS_RE
    if not pattern.match(req.to_ref):
        raise SandboxHTTPError("INVALID_RECIPIENT", "recipient is not a reserved sandbox address")
    if len(req.body) > MAX_BODY:
        raise SandboxHTTPError("MESSAGE_TOO_LONG", f"body longer than {MAX_BODY} characters")
    sbx_ref = "msg_" + sha256_hex(key)[:12]
    with db.tx("messaging") as c:
        db.run(
            c,
            "INSERT INTO messages (sbx_ref, channel, to_ref, subject, body, body_sha256, status, "
            "created_at) "
            "VALUES (:r, :ch, :to, :su, :b, :h, 'delivered_sandbox', :t)",
            r=sbx_ref,
            ch=req.channel,
            to=req.to_ref,
            su=req.subject,
            b=req.body,
            h=sha256_hex(req.body),
            t=now_iso(),
        )
        row = db.one(c, "SELECT * FROM messages WHERE sbx_ref = :r", r=sbx_ref)
    return 201, message_out(row).model_dump(mode="json")


@router.post("/send", response_model=MessageOut, status_code=201)
def send(
    req: MessageSendIn, request: Request, scope: str = Depends(require("write"))
) -> JSONResponse:
    key = idempotency.require_key(request)
    LIMITER.hit(scope, "messaging")
    return idempotency.run(
        "messaging", request, key, req.model_dump(mode="json"), lambda: _send(req, key)
    )


@router.get("/messages", response_model=list[MessageOut])
def list_messages(_scope: str = Depends(require("read"))) -> JSONResponse:
    with db.tx("messaging") as c:
        rows = db.rows(c, "SELECT * FROM messages ORDER BY created_at, sbx_ref")
    return JSONResponse([message_out(r).model_dump(mode="json") for r in rows])
