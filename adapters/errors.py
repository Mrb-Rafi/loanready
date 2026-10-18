"""Sandbox error code -> ReasonCode (PRD/04 §7, SBX-A-04). Unknown codes -> SANDBOX_ERROR."""

from __future__ import annotations

from typing import Any

import httpx

from contracts.enums import ReasonCode
from contracts.errors import SandboxError

ERROR_MAP: dict[str, ReasonCode] = {
    "UNAUTHENTICATED": ReasonCode.SANDBOX_ERROR,
    "INSUFFICIENT_SCOPE": ReasonCode.SANDBOX_ERROR,
    "IDEMPOTENCY_KEY_REQUIRED": ReasonCode.SANDBOX_ERROR,
    "IDEMPOTENCY_CONFLICT": ReasonCode.SANDBOX_ERROR,
    "RATE_LIMITED": ReasonCode.RATE_LIMITED,
    "VALIDATION_ERROR": ReasonCode.INVALID_PARAMS,
    "UNKNOWN_ACCOUNT": ReasonCode.UNKNOWN_ENTITY,
    "ACCOUNT_FROZEN": ReasonCode.SANDBOX_ERROR,
    "UNKNOWN_PAYMENT": ReasonCode.UNKNOWN_ENTITY,
    "PAYEE_UNKNOWN": ReasonCode.NEW_PAYEE,
    "PAYEE_CLOSED": ReasonCode.PAYEE_BLOCKED,
    "ACCOUNT_MISMATCH": ReasonCode.ACCOUNT_CHANGED,
    "INVALID_AMOUNT": ReasonCode.INVALID_PARAMS,
    "INVALID_VALUE_DATE": ReasonCode.INVALID_PARAMS,
    "VALUE_DATE_TOO_FAR": ReasonCode.INVALID_PARAMS,
    "BANK_LIMIT_EXCEEDED": ReasonCode.BANK_LIMIT_EXCEEDED,
    "INSUFFICIENT_FUNDS": ReasonCode.INSUFFICIENT_FUNDS,
    "NOT_CANCELLABLE": ReasonCode.SANDBOX_ERROR,
    "INVALID_CURSOR": ReasonCode.INVALID_PARAMS,
    "CLOCK_BACKWARDS": ReasonCode.SANDBOX_ERROR,
    "INVALID_RECIPIENT": ReasonCode.INVALID_PARAMS,
    "MESSAGE_TOO_LONG": ReasonCode.INVALID_PARAMS,
    "PACK_HASH_MISMATCH": ReasonCode.PACK_HASH_MISMATCH,
    "UNKNOWN_LENDER": ReasonCode.UNKNOWN_ENTITY,
    "UNKNOWN_APPLICATION": ReasonCode.UNKNOWN_ENTITY,
    "PDF_INVALID": ReasonCode.INVALID_PARAMS,
}


def raise_for_error(response: httpx.Response) -> None:
    """Map an error response to ``SandboxError(reason_code, http_status, sandbox_code)``."""
    if response.status_code < 400:
        return
    try:
        body: dict[str, Any] = response.json()
    except ValueError:
        body = {}
    code = str(body.get("code") or f"HTTP_{response.status_code}")
    reason = ERROR_MAP.get(code, ReasonCode.SANDBOX_ERROR)
    details = dict(body.get("details") or {})
    if "Retry-After" in response.headers:
        details["retry_after"] = response.headers["Retry-After"]
    raise SandboxError(
        f"sandbox refused the request: {code}: {body.get('message', '')}".strip(),
        reason_code=reason,
        details=details,
        http_status=response.status_code,
        sandbox_code=code,
    )
