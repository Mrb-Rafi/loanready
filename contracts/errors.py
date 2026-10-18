"""Domain exceptions with reason codes (CON-07). Raise these; never return error strings."""

from __future__ import annotations

from typing import Any

from contracts.enums import ReasonCode


class LoanReadyError(Exception):
    default_reason: ReasonCode = ReasonCode.INVALID_PARAMS

    def __init__(
        self,
        message: str,
        *,
        reason_code: ReasonCode | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.reason_code: ReasonCode = reason_code or self.default_reason
        self.details: dict[str, Any] = dict(details or {})

    def __str__(self) -> str:
        return f"{self.reason_code.value}: {self.message}"


class PermissionDenied(LoanReadyError):
    """KILL_SWITCH, BLOCKED_TYPE, UNTRUSTED_ORIGIN, ROLE_NOT_ALLOWED."""

    default_reason = ReasonCode.BLOCKED_TYPE


class InvalidParams(LoanReadyError):
    default_reason = ReasonCode.INVALID_PARAMS


class EntityCheckFailed(LoanReadyError):
    """UNKNOWN_ENTITY, NEW_PAYEE, ACCOUNT_CHANGED, AMOUNT_*, DRAFT_MISMATCH, PACK_HASH_MISMATCH."""

    default_reason = ReasonCode.UNKNOWN_ENTITY


class CapExceeded(LoanReadyError):
    """CAP_*, RATE_LIMIT_REMINDER."""

    default_reason = ReasonCode.CAP_PER_ACTION


class ApprovalError(LoanReadyError):
    """APPROVAL_*, SELF_APPROVAL_FORBIDDEN."""

    default_reason = ReasonCode.APPROVAL_INVALID


class TicketInvalid(LoanReadyError):
    default_reason = ReasonCode.TICKET_INVALID


class SandboxError(LoanReadyError):
    """SANDBOX_ERROR, INSUFFICIENT_FUNDS and the mapped sandbox catalogue codes."""

    default_reason = ReasonCode.SANDBOX_ERROR

    def __init__(
        self,
        message: str,
        *,
        reason_code: ReasonCode | None = None,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
        sandbox_code: str | None = None,
    ):
        super().__init__(message, reason_code=reason_code, details=details)
        self.http_status = http_status
        self.sandbox_code = sandbox_code


class RemoteNotAllowed(LoanReadyError):
    default_reason = ReasonCode.REMOTE_NOT_ALLOWED


class PeriodLocked(LoanReadyError):
    default_reason = ReasonCode.PERIOD_LOCKED


class UnbalancedEntry(LoanReadyError):
    default_reason = ReasonCode.INVALID_PARAMS


class CatalogueMiss(LoanReadyError):
    """Product not in the catalogue."""

    default_reason = ReasonCode.INVALID_PARAMS


class LLMOutputInvalid(LoanReadyError):
    default_reason = ReasonCode.INVALID_PARAMS
