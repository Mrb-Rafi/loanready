"""SeedBundle: the synthetic-data hand-off format between WP8 and WP2/WP9 (CON-21).

Stored as ``var/seed/bundle.json``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from contracts._model import Frozen
from contracts.dto_books import (
    BillDTO,
    CounterpartyDTO,
    InvoiceDTO,
    JournalLineDTO,
    PayeeDTO,
    RecurringObligation,
)
from contracts.dto_planning import EventUplift
from contracts.sandbox_api import BankAccountOut, BankTxnOut, PayeeOut, PosSaleOut


class SeedBundle(Frozen):
    version: Literal["1"]
    seed: int
    as_of: date
    business: dict[str, str]  # name, owner, currency, city (fictional)
    history_start: date  # first day of history (AS_OF minus 183 days for Rina)
    opening_balances: list[JournalLineDTO]  # opening entry at history_start; credits to 3000
    bank_accounts: list[BankAccountOut]  # one operating account "acc_operating"
    bank_transactions: list[BankTxnOut]  # signed amounts
    bank_payees: list[PayeeOut]  # MockBank's payee registry
    pos_sales: list[PosSaleOut]
    counterparties: list[CounterpartyDTO]
    payees: list[PayeeDTO]  # app-side payee master (same ids as bank_payees)
    invoices: list[InvoiceDTO]
    bills: list[BillDTO]
    receipts: list[dict[str, str]]  # {"id","image","sidecar","expected_status"}
    recurring: list[RecurringObligation]
    events: list[EventUplift]
    scenario_ids: dict[str, list[str]]  # "S1": [raw ids...], "S6": [bill id] ...
