"""Invoices and supplier bills: history (paid) and open documents (PRD/10 §3).

Customer lateness histories in ``business.py`` are the observed lateness of that customer's
paid invoices, so the history is generated from them: one paid invoice per history value,
in order, paid on ``due_date + lateness`` (all before AS_OF). Supplier bills are paid on
their due date. Open scenario documents (S2, S3, S4, S6, S10) are added by ``scenarios.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import date, timedelta

from contracts.dto_books import BillDTO, InvoiceDTO
from contracts.enums import BillStatus, InvoiceStatus
from datagen.bank import RANK_BILL, RANK_CUSTOMER, Flow
from datagen.business import (
    ID_TAG,
    PAYEE_BOXCO,
    PAYEE_GOLDEN,
    counterparty_name,
    lateness_history,
    terms_days,
)
from datagen.params import ScenarioParams

D = date

# Issue dates of paid invoices, one per lateness-history value (oldest first).
PAID_INVOICE_ISSUES: dict[str, tuple[date, ...]] = {
    "cpt_bright": (D(2026, 4, 20), D(2026, 5, 18), D(2026, 6, 15), D(2026, 7, 13), D(2026, 8, 10)),
    "cpt_harbor": (D(2026, 5, 4), D(2026, 6, 8), D(2026, 7, 6), D(2026, 8, 3)),
    "cpt_lumen": (D(2026, 4, 17), D(2026, 5, 15), D(2026, 5, 29), D(2026, 6, 12)),
    "cpt_daily": (D(2026, 6, 5), D(2026, 7, 3), D(2026, 8, 7), D(2026, 9, 4)),
}
# Recent cafe invoices still open at AS_OF and not yet due (so S3 has exactly two overdue).
OPEN_CAFE_ISSUES: tuple[tuple[str, date], ...] = (
    ("cpt_lumen", D(2026, 10, 1)),
    ("cpt_daily", D(2026, 10, 7)),
    ("cpt_corner", D(2026, 10, 12)),
)
CATERING = ("cpt_bright", "cpt_harbor")

# Golden Mills: one bill every 14 days from 15 Apr (09-16 and 09-30 are the S10 and S2 bills,
# added by scenarios.py). A pre-history bill issued 02 Apr is paid on 02 May (the payee's
# first_paid_on, PRD/10 §2); only its payment is in the bank feed.
GM_FIRST_ISSUE = D(2026, 4, 15)
GM_LAST_REGULAR_ISSUE = D(2026, 9, 2)
GM_PREHISTORY_ISSUE = D(2026, 4, 2)
GM_PREHISTORY_NUMBER = 2012
GM_ANCHOR_NUMBER = 2190  # BILL-2190 issued 2026-09-16
GM_ANCHOR_ISSUE = D(2026, 9, 16)
GM_NUMBER_STEP = 15  # supplier issues about 15 bills every two weeks
# BoxCo: monthly on the 1st from May (10-01 is the S4 bill BILL-2215).
BOXCO_ISSUES = (D(2026, 5, 1), D(2026, 6, 1), D(2026, 7, 1), D(2026, 8, 1), D(2026, 9, 1))
BOXCO_ANCHOR_NUMBER = 2215  # BILL-2215 issued 2026-10-01
BOXCO_NUMBER_STEP = 9

# Invoice numbers fixed by scenarios: (issue date, number). Others are numbered around them.
INVOICE_ANCHORS: tuple[tuple[date, int], ...] = (
    (D(2026, 7, 1), 998),
    (D(2026, 9, 20), 1043),
    (D(2026, 9, 23), 1051),
)


@dataclass(frozen=True)
class InvoiceSpec:
    customer_id: str
    issue: date
    amount_cents: int
    paid_on: date | None
    number: int | None = None

    @property
    def due(self) -> date:
        return self.issue + timedelta(days=terms_days(self.customer_id))


def _amount(rng: random.Random, lo: int, hi: int, step: int) -> int:
    """Uniform amount in [lo, hi] on a ``step``-cent grid."""
    return rng.randint(lo // step, hi // step) * step


def history_invoices(rng: random.Random, params: ScenarioParams) -> list[InvoiceSpec]:
    specs: list[InvoiceSpec] = []
    for cpt in sorted(PAID_INVOICE_ISSUES):
        late = lateness_history(cpt)
        issues = PAID_INVOICE_ISSUES[cpt]
        if len(late) != len(issues):
            raise ValueError(f"{cpt}: one paid invoice per lateness value")
        for issue, days_late in zip(issues, late, strict=True):
            if cpt in CATERING:
                amount = _amount(rng, params.catering_min_cents, params.catering_max_cents, 5000)
            else:
                amount = _amount(rng, params.cafe_min_cents, params.cafe_max_cents, 500)
            spec = InvoiceSpec(cpt, issue, amount, None)
            paid_on = spec.due + timedelta(days=max(0, days_late))
            if paid_on >= params.as_of:
                raise ValueError(f"{cpt} invoice of {issue} would be paid after AS_OF")
            specs.append(replace(spec, paid_on=paid_on))
    for cpt, issue in OPEN_CAFE_ISSUES:
        amount = _amount(rng, params.cafe_min_cents, params.cafe_max_cents, 500)
        spec = InvoiceSpec(cpt, issue, amount, None)
        if spec.due < params.as_of:
            raise ValueError("open cafe invoices must not be overdue (S3)")
        specs.append(spec)
    return specs


def number_invoices(specs: list[InvoiceSpec]) -> list[InvoiceSpec]:
    """Specs that already have a number keep it (scenario anchors). The rest are numbered
    in issue-date order: before the first anchor counting down from it, after an anchor
    counting up from it (never reaching the next anchor)."""
    anchors = sorted(INVOICE_ANCHORS)
    ordered = sorted(specs, key=lambda s: (s.issue, s.customer_id))
    out: list[InvoiceSpec] = []
    before = [s for s in ordered if s.number is None and s.issue < anchors[0][0]]
    nxt = anchors[0][1] - len(before)
    for s in ordered:
        if s.number is not None:
            out.append(s)
            continue
        if s.issue < anchors[0][0]:
            out.append(replace(s, number=nxt))
            nxt += 1
            continue
        floor = max(n for d, n in anchors if d <= s.issue)
        used = {o.number for o in out} | {n for _d, n in anchors}
        number = floor + 1
        while number in used:
            number += 1
        later = [n for d, n in anchors if d > s.issue]
        if later and number >= min(later):
            raise ValueError("invoice numbering collides with a scenario anchor")
        out.append(replace(s, number=number))
    return sorted(out, key=lambda s: (s.issue, s.number or 0))


def invoice_id(number: int) -> str:
    return f"inv_{ID_TAG}_{number:04d}"


def invoice_number(number: int) -> str:
    return f"INV-{number:04d}"


def invoice_dtos(specs: list[InvoiceSpec]) -> list[InvoiceDTO]:
    out = []
    for s in specs:
        if s.number is None:
            raise ValueError("unnumbered invoice")
        paid = s.amount_cents if s.paid_on is not None else 0
        out.append(
            InvoiceDTO(
                id=invoice_id(s.number),
                number=invoice_number(s.number),
                customer_id=s.customer_id,
                issue_date=s.issue,
                due_date=s.due,
                amount_cents=s.amount_cents,
                paid_cents=paid,
                status=InvoiceStatus.PAID if s.paid_on is not None else InvoiceStatus.OPEN,
            )
        )
    return out


def invoice_payment_flows(specs: list[InvoiceSpec]) -> list[Flow]:
    """Bank inflow ``{CUSTOMER} PAYMENT {INV}`` on the payment date of each paid invoice."""
    flows = []
    for s in specs:
        if s.paid_on is None or s.number is None:
            continue
        name = counterparty_name(s.customer_id)
        flows.append(
            Flow(
                s.paid_on,
                s.amount_cents,
                f"{name.upper()} PAYMENT {invoice_number(s.number)}",
                name,
                RANK_CUSTOMER,
                f"invoice:{invoice_id(s.number)}",
            )
        )
    return flows


def bill_id(number: int) -> str:
    return f"bil_{ID_TAG}_{number}"


def make_bill(
    number: int,
    supplier_id: str,
    payee_id: str | None,
    issue: date,
    due: date,
    amount_cents: int,
    *,
    paid: bool,
    description: str,
    late_penalty: bool = True,
    essential: bool = False,
    grace_days: int = 0,
    claimed_bank_account_ref: str | None = None,
    category: str = "5000",
) -> BillDTO:
    return BillDTO(
        id=bill_id(number),
        number=f"BILL-{number}",
        supplier_id=supplier_id,
        payee_id=payee_id,
        issue_date=issue,
        due_date=due,
        amount_cents=amount_cents,
        paid_cents=amount_cents if paid else 0,
        status=BillStatus.PAID if paid else BillStatus.OPEN,
        category=category,
        late_penalty=late_penalty,
        grace_days=grace_days,
        essential=essential,
        claimed_bank_account_ref=claimed_bank_account_ref,
        description=description,
    )


def history_bills(rng: random.Random, params: ScenarioParams) -> list[BillDTO]:
    """Paid Golden Mills (fortnightly) and BoxCo (monthly) bills, net 30."""
    bills = []
    issue = GM_FIRST_ISSUE
    while issue <= GM_LAST_REGULAR_ISSUE:
        steps_back = (GM_ANCHOR_ISSUE - issue).days // 14
        number = GM_ANCHOR_NUMBER - GM_NUMBER_STEP * steps_back
        amount = _amount(rng, params.gm_bill_min_cents, params.gm_bill_max_cents, 500)
        bills.append(
            make_bill(
                number,
                "cpt_golden",
                PAYEE_GOLDEN,
                issue,
                issue + timedelta(days=terms_days("cpt_golden")),
                amount,
                paid=True,
                description="Flour delivery",
            )
        )
        issue += timedelta(days=14)
    for k, issue in enumerate(reversed(BOXCO_ISSUES), start=1):
        number = BOXCO_ANCHOR_NUMBER - BOXCO_NUMBER_STEP * k
        amount = _amount(rng, params.boxco_bill_min_cents, params.boxco_bill_max_cents, 500)
        bills.append(
            make_bill(
                number,
                "cpt_boxco",
                PAYEE_BOXCO,
                issue,
                issue + timedelta(days=terms_days("cpt_boxco")),
                amount,
                paid=True,
                description="Packaging supplies",
                late_penalty=False,
            )
        )
    return sorted(bills, key=lambda b: (b.issue_date, b.number))


def prehistory_golden_payment(rng: random.Random, params: ScenarioParams) -> Flow:
    """Payment on 02 May of the Golden Mills bill issued before history_start."""
    amount = _amount(rng, params.gm_bill_min_cents, params.gm_bill_max_cents, 500)
    paid_on = GM_PREHISTORY_ISSUE + timedelta(days=terms_days("cpt_golden"))
    return Flow(
        paid_on,
        -amount,
        f"GOLDEN MILLS BILL-{GM_PREHISTORY_NUMBER}",
        "Golden Mills",
        RANK_BILL,
        f"payee:{PAYEE_GOLDEN}",
    )


def bill_payment_flows(bills: list[BillDTO]) -> list[Flow]:
    """Bank outflow ``{SUPPLIER} {BILL}`` on the due date of every paid bill."""
    flows = []
    for b in bills:
        if b.status != BillStatus.PAID:
            continue
        name = counterparty_name(b.supplier_id)
        flows.append(
            Flow(
                b.due_date,
                -b.amount_cents,
                f"{name.upper()} {b.number}",
                name,
                RANK_BILL,
                f"payee:{b.payee_id}",
            )
        )
    return flows
