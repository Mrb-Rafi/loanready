"""Error catalogue (PRD/04 §7, SBX-11). Every error response is ``ErrorOut``."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from contracts.sandbox_api import SANDBOX_ERROR_CODES

ERROR_CATALOGUE: dict[str, int] = {
    "UNAUTHENTICATED": 401,
    "INSUFFICIENT_SCOPE": 403,
    "IDEMPOTENCY_KEY_REQUIRED": 400,
    "IDEMPOTENCY_CONFLICT": 422,
    "RATE_LIMITED": 429,
    "VALIDATION_ERROR": 422,
    "UNKNOWN_ACCOUNT": 404,
    "ACCOUNT_FROZEN": 409,
    "UNKNOWN_PAYMENT": 404,
    "PAYEE_UNKNOWN": 403,
    "PAYEE_CLOSED": 403,
    "ACCOUNT_MISMATCH": 403,
    "INVALID_AMOUNT": 422,
    "INVALID_VALUE_DATE": 422,
    "VALUE_DATE_TOO_FAR": 422,
    "BANK_LIMIT_EXCEEDED": 409,
    "INSUFFICIENT_FUNDS": 409,
    "NOT_CANCELLABLE": 409,
    "INVALID_CURSOR": 422,
    "CLOCK_BACKWARDS": 422,
    "INVALID_RECIPIENT": 422,
    "MESSAGE_TOO_LONG": 422,
    "PACK_HASH_MISMATCH": 422,
    "UNKNOWN_LENDER": 404,
    "UNKNOWN_APPLICATION": 404,
    "PDF_INVALID": 422,
}
assert set(ERROR_CATALOGUE) == set(SANDBOX_ERROR_CODES), "catalogue must match contracts"


class SandboxHTTPError(Exception):
    """Raise with a catalogue code; rendered as ErrorOut with the catalogue's HTTP status."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        if code not in ERROR_CATALOGUE:
            raise ValueError(f"unknown sandbox error code {code}")
        super().__init__(message or code)
        self.code = code
        self.status = ERROR_CATALOGUE[code]
        self.message = message or code.replace("_", " ").lower()
        self.details = details
        self.headers = headers or {}

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def response(self) -> JSONResponse:
        return JSONResponse(self.payload(), status_code=self.status, headers=self.headers)
