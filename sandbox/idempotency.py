"""Idempotency-Key store and replay (SBX-03, SBX-07; CTL-PAY-04, REG-SEC-03).

Same key + same path + same request hash -> the stored response (errors too) with
``Idempotent-Replay: true`` and no new side effect. Same key with a different path or body
-> 422 IDEMPOTENCY_CONFLICT. The app always uses ``Idempotency-Key = action_id``."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from contracts.canonical import canonical_json, sha256_hex
from contracts.sandbox_api import (
    HEADER_IDEMPOTENCY_KEY,
    HEADER_IDEMPOTENT_REPLAY,
    IDEMPOTENCY_KEY_PATTERN,
)
from sandbox import db
from sandbox.clock import now_iso
from sandbox.errors import SandboxHTTPError

_KEY_RE = re.compile(IDEMPOTENCY_KEY_PATTERN)


def require_key(request: Request) -> str:
    key = request.headers.get(HEADER_IDEMPOTENCY_KEY)
    if not key or not _KEY_RE.match(key):
        raise SandboxHTTPError(
            "IDEMPOTENCY_KEY_REQUIRED", "write requests need an Idempotency-Key header"
        )
    return key


def run(
    subapp: str,
    request: Request,
    key: str,
    body: dict[str, Any],
    handler: Callable[[], tuple[int, dict[str, Any]]],
) -> JSONResponse:
    """Replay a stored response or run ``handler`` once and store its outcome."""
    path = request.url.path
    req_hash = sha256_hex(canonical_json(body))
    with db.tx(subapp) as c:
        stored = db.one(c, "SELECT * FROM idempotency WHERE key = :k", k=key)
    if stored is not None:
        if stored["path"] != path or stored["request_sha256"] != req_hash:
            raise SandboxHTTPError(
                "IDEMPOTENCY_CONFLICT", "this Idempotency-Key was used for a different request"
            )
        return JSONResponse(
            json.loads(stored["response_json"]),
            status_code=stored["status_code"],
            headers={HEADER_IDEMPOTENT_REPLAY: "true"},
        )
    try:
        status, payload = handler()
    except SandboxHTTPError as exc:
        status, payload = exc.status, exc.payload()
    with db.tx(subapp) as c:
        db.run(
            c,
            "INSERT INTO idempotency (key, path, request_sha256, status_code, response_json, "
            "created_at) "
            "VALUES (:k, :p, :h, :s, :r, :t)",
            k=key,
            p=path,
            h=req_hash,
            s=status,
            r=json.dumps(payload, ensure_ascii=False),
            t=now_iso(),
        )
    return JSONResponse(payload, status_code=status)
