"""HttpBankPort (BankPort over HTTP). Writes verify the execution ticket first (SBX-A-02/03)."""

from __future__ import annotations

from datetime import date

from adapters.http import ReadClient, WriteClient
from contracts.actions import ExecutionTicket, binding_for
from contracts.enums import ActionType
from contracts.ports import TicketVerifier
from contracts.sandbox_api import (
    BalanceOut,
    BankAccountOut,
    BankTxnOut,
    PayeeOut,
    PaymentOut,
    PaymentScheduleIn,
)


class HttpBankPort:
    def __init__(self, read: ReadClient, write: WriteClient, verifier: TicketVerifier):
        self._read, self._write, self._verify = read, write, verifier

    def list_accounts(self) -> list[BankAccountOut]:
        return [BankAccountOut.model_validate(a) for a in self._read.get("/bank/accounts")]

    def get_balance(self, account_id: str) -> int:
        return self.balance(account_id).balance_cents

    def balance(self, account_id: str) -> BalanceOut:
        return BalanceOut.model_validate(self._read.get(f"/bank/accounts/{account_id}/balance"))

    def list_transactions(self, account_id: str, start: date, end: date) -> list[BankTxnOut]:
        rows = self._read.get_all(
            f"/bank/accounts/{account_id}/transactions",
            {"start": start.isoformat(), "end": end.isoformat()},
        )
        return [BankTxnOut.model_validate(r) for r in rows]

    def list_payees(self) -> list[PayeeOut]:
        return [PayeeOut.model_validate(p) for p in self._read.get("/bank/payees")]

    def schedule_payment(self, ticket: ExecutionTicket, req: PaymentScheduleIn) -> PaymentOut:
        """Verify the ticket against the outgoing request, then POST. A returned payment with
        status ``failed`` is returned as-is (the executor reports PAYMENT_FAILED)."""
        self._verify(
            ticket,
            ActionType.SCHEDULE_SUPPLIER_PAYMENT,
            binding_for(ActionType.SCHEDULE_SUPPLIER_PAYMENT, req.model_dump()),
        )
        return PaymentOut.model_validate(
            self._write.post("/bank/payments/schedule", ticket, json=req.model_dump(mode="json"))
        )

    def cancel_payment(self, ticket: ExecutionTicket, sbx_ref: str) -> PaymentOut:
        self._verify(ticket, ActionType.CANCEL_PAYMENT, {"sbx_ref": sbx_ref})
        return PaymentOut.model_validate(
            self._write.post(f"/bank/payments/{sbx_ref}/cancel", ticket)
        )

    def list_payments(self, status: str | None = None) -> list[PaymentOut]:
        params = {"status": status} if status else None
        return [PaymentOut.model_validate(p) for p in self._read.get("/bank/payments", params)]

    def get_payment(self, sbx_ref: str) -> PaymentOut:
        return PaymentOut.model_validate(self._read.get(f"/bank/payments/{sbx_ref}"))
