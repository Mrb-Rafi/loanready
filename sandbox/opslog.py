"""Operation log (SBX-04, SBX-10; CTL-LOG-05): one hash-chained row per request, in each
sub-app's DB, including rejected requests. Request and response bodies are not stored, only
their SHA-256; tokens are never stored (scope name only).

``row_hash = sha256_hex(prev_hash + canonical_json(row without row_hash))``; row 1 links to
``ZERO_HASH``. ``/admin/*`` and ``/health`` requests are logged in the bank's log."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
from typing import Any

from contracts.canonical import ZERO_HASH, canonical_json, sha256_hex
from sandbox import db
from sandbox.clock import now_iso

COLUMNS = [
    "seq",
    "ts",
    "subapp",
    "method",
    "path",
    "scope",
    "idempotency_key",
    "ticket_id",
    "status_code",
    "error_code",
    "request_sha256",
    "response_sha256",
    "prev_hash",
    "row_hash",
]
_LOCKS = {s: threading.Lock() for s in db.SUBAPPS}
_INSERT_SQL = (
    f"INSERT INTO ops_log ({', '.join(COLUMNS)}) VALUES ({', '.join(':' + k for k in COLUMNS)})"
)


def subapp_for(path: str) -> str:
    for s in ("bank", "pos", "messaging", "lender"):
        if path == f"/{s}" or path.startswith(f"/{s}/"):
            return s
    return "bank"


def _hash(record: dict[str, Any]) -> str:
    return sha256_hex(record["prev_hash"] + canonical_json(record))


def append(subapp: str, entry: dict[str, Any]) -> int:
    """Append one row; ``entry`` holds every column except seq, ts, subapp and hashes."""
    with _LOCKS[subapp], db.tx(subapp) as c:
        last = db.one(c, "SELECT seq, row_hash FROM ops_log ORDER BY seq DESC LIMIT 1")
        seq, prev = (last["seq"] + 1, last["row_hash"]) if last else (1, ZERO_HASH)
        record = {"seq": seq, "ts": now_iso(), "subapp": subapp, **entry, "prev_hash": prev}
        record["row_hash"] = _hash({k: v for k, v in record.items()})
        db.run(c, _INSERT_SQL, **record)
        return seq


def verify(subapp: str) -> int | None:
    """None if the sub-app's chain is intact, else the first bad seq."""
    with db.tx(subapp) as c:
        stored = db.rows(c, f"SELECT {', '.join(COLUMNS)} FROM ops_log ORDER BY seq")
    prev = ZERO_HASH
    for expected, row in enumerate(stored, start=1):
        record = {k: row[k] for k in COLUMNS if k != "row_hash"}
        if row["seq"] != expected or row["prev_hash"] != prev or _hash(record) != row["row_hash"]:
            return expected
        prev = row["row_hash"]
    return None


def verify_all() -> dict[str, int | None]:
    return {s: verify(s) for s in db.SUBAPPS}


def export_csv() -> str:
    """All four logs merged, sorted by ts, subapp, seq (-> evidence/sandbox_ops.csv)."""
    merged: list[dict[str, Any]] = []
    for s in db.SUBAPPS:
        with db.tx(s) as c:
            merged += db.rows(c, f"SELECT {', '.join(COLUMNS)} FROM ops_log")
    merged.sort(key=lambda r: (r["ts"], r["subapp"], r["seq"]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for r in merged:
        w.writerow(["" if r[k] is None else r[k] for k in COLUMNS])
    return buf.getvalue()


class OpsLogMiddleware:
    """Pure ASGI middleware: buffers the request body, captures status and response body,
    and appends one ops-log row after every HTTP request."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        messages, body, more = [], b"", True
        while more:
            msg = await receive()
            messages.append(msg)
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        pending = iter(messages)

        async def replay() -> Any:
            try:
                return next(pending)
            except StopIteration:
                return await receive()

        status, resp = 500, bytearray()

        async def capture(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                resp.extend(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay, capture)
        finally:
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            error_code = None
            if status >= 400:
                try:
                    error_code = json.loads(bytes(resp)).get("code")
                except (ValueError, AttributeError):
                    error_code = None
            state = scope.get("state") or {}
            append(
                subapp_for(scope["path"]),
                {
                    "method": scope["method"],
                    "path": scope["path"],
                    "scope": state.get("sbx_scope", "none"),
                    "idempotency_key": headers.get("idempotency-key"),
                    "ticket_id": headers.get("x-ticket-id"),
                    "status_code": status,
                    "error_code": error_code,
                    "request_sha256": hashlib.sha256(body).hexdigest(),
                    "response_sha256": hashlib.sha256(bytes(resp)).hexdigest(),
                },
            )
