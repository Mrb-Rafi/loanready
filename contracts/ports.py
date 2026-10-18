"""Ports to the sandbox (CON-18). Implemented over HTTP by ``adapters`` (WP2).

Write methods take an ``ExecutionTicket``; the adapter verifies it with the gate's
``TicketVerifier`` before any request leaves the process (PRD/04 SBX-A-02).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from contracts.actions import ExecutionTicket
from contracts.enums import ActionType
from contracts.sandbox_api import (
    BankAccountOut,
    BankTxnOut,
    LenderApplicationIn,
    LenderReceiptOut,
    MessageOut,
    MessageSendIn,
    PayeeOut,
    PaymentOut,
    PaymentScheduleIn,
    PosSaleOut,
)
from contracts.seed import SeedBundle


@runtime_checkable
class BankPort(Protocol):
    def list_accounts(self) -> list[BankAccountOut]: ...
    def get_balance(self, account_id: str) -> int: ...  # cents
    def list_transactions(self, account_id: str, start: date, end: date) -> list[BankTxnOut]: ...
    def list_payees(self) -> list[PayeeOut]: ...
    def schedule_payment(self, ticket: ExecutionTicket, req: PaymentScheduleIn) -> PaymentOut: ...
    def cancel_payment(self, ticket: ExecutionTicket, sbx_ref: str) -> PaymentOut: ...
    def list_payments(self, status: str | None = None) -> list[PaymentOut]: ...
    def get_payment(self, sbx_ref: str) -> PaymentOut: ...


@runtime_checkable
class PosPort(Protocol):
    def list_sales(self, start: date, end: date) -> list[PosSaleOut]: ...


@runtime_checkable
class MessagePort(Protocol):
    def send(self, ticket: ExecutionTicket, req: MessageSendIn) -> MessageOut: ...
    def list_messages(self) -> list[MessageOut]: ...


@runtime_checkable
class LenderPort(Protocol):
    def submit_application(
        self, ticket: ExecutionTicket, req: LenderApplicationIn
    ) -> LenderReceiptOut: ...
    def get_application(self, receipt_id: str) -> LenderReceiptOut: ...


@runtime_checkable
class SandboxAdminPort(Protocol):
    def reset(self, bundle: SeedBundle) -> dict[str, int]: ...  # counts loaded; admin token
    def health(self) -> dict[str, str]: ...  # includes today and openapi_sha256
    def get_clock(self) -> date: ...
    def set_clock(self, today: date) -> date: ...  # tests and optional demo epilogue only
    def opslog_csv(self) -> str: ...
    def opslog_verify(self) -> dict[str, int | None]: ...  # per sub-app: None = intact


TicketVerifier = Callable[[ExecutionTicket, ActionType, Mapping[str, str]], None]
"""Raises TicketInvalid if signature, expiry, single use, action type or binding fields
do not match the outgoing request."""


@dataclass
class Ports:
    """What ``adapters.build_ports(settings, verifier, transport)`` returns (SBX-A-01)."""

    bank: BankPort
    pos: PosPort
    messaging: MessagePort
    lender: LenderPort
    admin: SandboxAdminPort
