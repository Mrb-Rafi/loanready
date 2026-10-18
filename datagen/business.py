"""Business profile, counterparties and payees of Rina's Bakery (PRD/10 §2).

Everything here is fictional. Emails use the reserved ``.test`` domain, phones the ``+999``
range and bank account refs the ``SBX-`` prefix (REG-DP-05, CTL-DQ-02).
"""

from __future__ import annotations

from datetime import date

from contracts.dto_books import CounterpartyDTO, PayeeDTO
from contracts.enums import CounterpartyKind, PayeeStatus
from contracts.sandbox_api import BankAccountOut, PayeeOut

BUSINESS: dict[str, str] = {
    "name": "Rina's Bakery",
    "owner": "Rina",
    "currency": "USD",
    "city": "Riverton",
}

ACCOUNT_ID = "acc_operating"
ACCOUNT_NAME = "Operating account"
ID_TAG = "rina"  # every generated id is "<prefix>_rina_<n>"

_C = CounterpartyKind.CUSTOMER
_S = CounterpartyKind.SUPPLIER

# id, name, kind, terms days, lateness history (days, oldest first), contact
_Row = tuple[str, str, CounterpartyKind, int, tuple[int, ...], str | None]
_COUNTERPARTIES: tuple[_Row, ...] = (
    (
        "cpt_bright",
        "Bright Events Co.",
        _C,
        14,
        (28, 31, 34, 36, 40),
        "accounts@bright-events.test",
    ),
    ("cpt_harbor", "Harbor Hotel Kitchens", _C, 14, (30, 33, 35, 37), "ap@harbor-hotel.test"),
    ("cpt_lumen", "Cafe Lumen", _C, 30, (0, 5, 45, 70), "hello@cafe-lumen.test"),
    ("cpt_daily", "Daily Grind Cafe", _C, 14, (0, 2, 1, 3), "+9995550101"),
    ("cpt_corner", "Corner Deli", _C, 14, (), "orders@corner-deli.test"),
    ("cpt_golden", "Golden Mills", _S, 30, (), "billing@golden-mills.test"),
    ("cpt_boxco", "BoxCo Packaging", _S, 30, (), "ar@boxco.test"),
    ("cpt_maple", "Maple Properties", _S, 0, (), "rent@maple-props.test"),
    ("cpt_power", "City Power Co", _S, 0, (), None),
)

CATERING_CUSTOMERS = ("cpt_bright", "cpt_harbor")
CAFE_CUSTOMERS = ("cpt_lumen", "cpt_daily", "cpt_corner")

# Payees: id, counterparty, name, account ref (app master and MockBank registry, same ids)
PAYEE_GOLDEN = "pye_golden"
PAYEE_BOXCO = "pye_boxco"
PAYEE_MAPLE = "pye_maple"
_PAYEES: tuple[tuple[str, str, str, str], ...] = (
    (PAYEE_GOLDEN, "cpt_golden", "Golden Mills", "SBX-GM-00412277"),
    (PAYEE_BOXCO, "cpt_boxco", "BoxCo Packaging", "SBX-BX-00981133"),
    (PAYEE_MAPLE, "cpt_maple", "Maple Properties", "SBX-MP-00230045"),
)
GOLDEN_FIRST_PAID_ON = date(2026, 5, 2)  # PRD/10 §2


def counterparties() -> list[CounterpartyDTO]:
    return [
        CounterpartyDTO(
            id=cid,
            name=name,
            kind=kind,
            payment_terms_days=terms,
            lateness_history_days=list(late),
            contact_ref=contact,
        )
        for cid, name, kind, terms, late, contact in _COUNTERPARTIES
    ]


def counterparty_name(cpt_id: str) -> str:
    for cid, name, *_ in _COUNTERPARTIES:
        if cid == cpt_id:
            return name
    raise KeyError(cpt_id)


def lateness_history(cpt_id: str) -> tuple[int, ...]:
    for cid, _name, _kind, _terms, late, _contact in _COUNTERPARTIES:
        if cid == cpt_id:
            return late
    raise KeyError(cpt_id)


def terms_days(cpt_id: str) -> int:
    for cid, _name, _kind, terms, _late, _contact in _COUNTERPARTIES:
        if cid == cpt_id:
            return terms
    raise KeyError(cpt_id)


def payee_account(payee_id: str) -> str:
    for pid, _cpt, _name, ref in _PAYEES:
        if pid == payee_id:
            return ref
    raise KeyError(payee_id)


def payees(first_paid: dict[str, date | None]) -> list[PayeeDTO]:
    """App-side payee master. ``first_paid`` maps payee id -> first payment date in history."""
    return [
        PayeeDTO(
            id=pid,
            counterparty_id=cpt,
            name=name,
            bank_account_ref=ref,
            first_paid_on=first_paid.get(pid),
            status=PayeeStatus.KNOWN,
            preregistered_by_owner=True,
        )
        for pid, cpt, name, ref in _PAYEES
    ]


def bank_payees(first_paid: dict[str, date | None]) -> list[PayeeOut]:
    """MockBank's payee registry (same ids and accounts as the app master)."""
    return [
        PayeeOut(
            id=pid,
            name=name,
            bank_account_ref=ref,
            first_paid_on=first_paid.get(pid),
            status="known",
        )
        for pid, _cpt, name, ref in _PAYEES
    ]


def bank_accounts(closing_cents: int) -> list[BankAccountOut]:
    return [
        BankAccountOut(
            id=ACCOUNT_ID,
            name=ACCOUNT_NAME,
            currency=BUSINESS["currency"],
            status="open",
            balance_cents=closing_cents,
        )
    ]
