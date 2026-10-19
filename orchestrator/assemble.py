"""Assembly across packages (PRD/11 §6): forecast inputs, the financing request, loan-pack
content and the events list. Pure functions over service protocols; no side effects."""

from __future__ import annotations

import importlib
import json
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from contracts.dto_books import BillDTO, PayeeDTO
from contracts.dto_financing import (
    EligibleInvoice,
    FinancingComparisonDTO,
    FinancingRequest,
    ReadinessDTO,
)
from contracts.dto_outputs import LoanPackContent
from contracts.dto_planning import (
    BillForecastItem,
    CashPlanDTO,
    EventUplift,
    ForecastDTO,
    ForecastInputs,
    InvoiceForecastItem,
)
from contracts.enums import Framework
from contracts.money import from_cents, to_cents
from contracts.ports import BankPort
from contracts.registry import OPERATING_ACCOUNT_ID
from contracts.services import BooksService
from infra.paths import DATA_DIR, var_dir

SALES_WEEKS = 12  # ORC-07 weekly sales and COGS ratio window
PACK_SALES_WEEKS = 26  # ORC-09
CARD_SALES_DAYS = 28  # ORC-08
IMPAIRED_AFTER_DAYS = 60  # invoices more than 60 days overdue are not expected to be collected
DEFAULT_FINANCING_CENTS = 100000
DEFAULT_TERM_DAYS = 30
HISTORY_DAYS_FALLBACK = 183

DEFAULT_POLICIES = [
    "Accrual basis: revenue when goods are delivered, expenses when incurred.",
    "Cash comprises bank balances and cash on hand (till).",
    "Receivables are stated net of an allowance for expected credit losses.",
    "Inventory purchases (ingredients, packaging) are expensed as cost of goods sold.",
    "Personal expenses paid from the business account are owner's drawings, not expenses.",
]


def suspected_fraud(bill: BillDTO, payee: PayeeDTO | None) -> bool:
    """A bill whose document claims a bank account different from the stored payee account
    (or that has no stored payee to compare with) is excluded pending verification (ORC-07)."""
    claimed = bill.claimed_bank_account_ref
    if not claimed:
        return False
    return payee is None or str(claimed) != payee.bank_account_ref


def load_events(as_of: date) -> list[EventUplift]:
    """Sales-uplift events: ``data/events.json`` (WP8), else the seed bundle's events, else none."""
    candidates = [DATA_DIR / "events.json", var_dir() / "seed" / "bundle.json"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = raw.get("events", [])
        events = [EventUplift.model_validate(r) for r in rows]
        return [e for e in events if e.end >= as_of]
    return []


def safety_buffer(settings: dict[str, str]) -> int | None:
    """Settings ``safety_buffer_cents``: "auto" (or missing) -> None, else int cents."""
    raw = settings.get("safety_buffer_cents", "auto")
    return None if raw in ("", "auto") else int(raw)


@dataclass(frozen=True)
class ForecastAssembly:
    inputs: ForecastInputs
    excluded_bills: list[BillDTO]  # "excluded pending verification" (S6)


def forecast_inputs(
    books: BooksService,
    as_of: date,
    events: list[EventUplift],
    buffer_cents: int | None,
) -> ForecastAssembly:
    """ORC-07. Opening cash = cash balance (1000 + 1010) at ``as_of``; 12 weeks of sales and
    COGS ratio; open invoices with the customer's lateness history; open bills except
    suspected fraud; recurring obligations; events; safety buffer (None = engine default)."""
    invoices = []
    for inv in books.open_invoices():
        cp = books.counterparty(inv.customer_id)
        invoices.append(
            InvoiceForecastItem(
                invoice_id=inv.id,
                customer_id=inv.customer_id,
                due_date=inv.due_date,
                outstanding_cents=inv.outstanding_cents,
                lateness_history_days=list(cp.lateness_history_days) if cp else [],
            )
        )
    bills, excluded = [], []
    for b in books.open_bills():
        payee = books.payee(b.payee_id) if b.payee_id else None
        if suspected_fraud(b, payee):
            excluded.append(b)
            continue
        bills.append(
            BillForecastItem(
                bill_id=b.id,
                supplier_id=b.supplier_id,
                due_date=b.due_date,
                outstanding_cents=b.outstanding_cents,
                category=b.category,
                late_penalty=b.late_penalty,
                essential=b.essential,
                grace_days=b.grace_days,
            )
        )
    inputs = ForecastInputs(
        as_of=as_of,
        opening_cash_cents=books.cash_balance(as_of),
        weekly_sales=books.weekly_sales(SALES_WEEKS, as_of),
        cogs_ratio=books.cogs_ratio(SALES_WEEKS, as_of),
        open_invoices=invoices,
        open_bills=bills,
        recurring=books.recurring_obligations(),
        events=events,
        safety_buffer_cents=buffer_cents,
    )
    return ForecastAssembly(inputs, excluded)


def avg_daily_card_sales_cents(books: BooksService, as_of: date) -> int:
    """ORC-08: card sales of the last 28 days (4 weeks of ``weekly_sales``) / 28, HALF_EVEN."""
    weeks = books.weekly_sales(CARD_SALES_DAYS // 7, as_of)
    total = sum(w.card_sales_cents for w in weeks)
    return to_cents(from_cents(total) / Decimal(CARD_SALES_DAYS))


def _expected_days_to_pay(due: date, lateness: list[int], as_of: date) -> int:
    """Days from ``as_of`` to due date + median historical lateness (never negative)."""
    late = int(statistics.median(lateness)) if lateness else 0
    return max(0, (due - as_of).days + late)


def financing_request(
    books: BooksService,
    plan: CashPlanDTO,
    forecast: ForecastDTO,
    as_of: date,
    avg_daily_card_cents: int,
) -> FinancingRequest:
    """ORC-08: the cash plan's financing request; when the plan needs no borrowing, a
    request for the recommended gap (or 1,000.00) over the needed term (or 30 days), so
    the comparison page always has content."""
    if plan.financing_request is not None:
        return plan.financing_request
    eligible = []
    for inv in books.open_invoices():
        if inv.days_overdue(as_of) > IMPAIRED_AFTER_DAYS:
            continue
        cp = books.counterparty(inv.customer_id)
        eligible.append(
            EligibleInvoice(
                invoice_id=inv.id,
                outstanding_cents=inv.outstanding_cents,
                expected_days_to_pay=_expected_days_to_pay(
                    inv.due_date, list(cp.lateness_history_days) if cp else [], as_of
                ),
            )
        )
    return FinancingRequest(
        amount_cents=forecast.recommended_gap_cents or DEFAULT_FINANCING_CENTS,
        term_days=forecast.needed_term_days or DEFAULT_TERM_DAYS,
        as_of=as_of,
        avg_daily_card_sales_cents=avg_daily_card_cents,
        eligible_invoices=eligible,
        # same rule as WP4's cash plan (D3): any open bill the forecast keeps (not suspected fraud)
        supplier_terms_available=any(
            not suspected_fraud(b, books.payee(b.payee_id) if b.payee_id else None)
            for b in books.open_bills()
        ),
    )


def history_start(books: BooksService, as_of: date) -> date:
    """First journal date (the opening entry), else ``as_of - 183 days``."""
    entries = books.journal()
    return min((e.date for e in entries), default=as_of - timedelta(days=HISTORY_DAYS_FALLBACK))


def accounting_policies(framework: Framework) -> list[str]:
    """Policy text from ``books.policies`` when WP3 provides it, else a neutral default.

    Accepted shapes: ``policies(framework) -> list[str]`` or a module-level
    ``ACCOUNTING_POLICIES`` list.
    """
    try:
        mod = importlib.import_module("books.policies")
    except ImportError:
        return list(DEFAULT_POLICIES)
    fn = getattr(mod, "policies", None)
    if callable(fn):
        return [str(p) for p in fn(framework)]
    text = getattr(mod, "ACCOUNTING_POLICIES", None)
    return [str(p) for p in text] if text else list(DEFAULT_POLICIES)


def going_concern_note(forecast: ForecastDTO) -> str | None:
    if not forecast.going_concern_flag:
        return None
    return (
        "The 13-week forecast shows cash below zero in the low scenario without new "
        "financing. Management is seeking the working-capital facility described in this "
        "pack; the statements are prepared on a going-concern basis."
    )


def loan_pack_content(
    *,
    books: BooksService,
    bank: BankPort,
    business_name: str,
    as_of: date,
    framework: Framework,
    amount_cents: int,
    term_days: int,
    purpose: str,
    forecast: ForecastDTO,
    readiness: ReadinessDTO,
    financing: FinancingComparisonDTO | None,
    audit_final_hash: str | None,
    audit_chain_ok: bool,
) -> LoanPackContent:
    """ORC-09. Statements cover the full history to the day before ``as_of``; the
    reconciliation uses MockBank's live balance and transactions."""
    start = history_start(books, as_of)
    end = as_of - timedelta(days=1)
    bank_txns = bank.list_transactions(OPERATING_ACCOUNT_ID, start, as_of)
    return LoanPackContent(
        business_name=business_name,
        as_of=as_of,
        framework=framework,
        requested_amount_cents=amount_cents,
        term_days=term_days,
        purpose=purpose,
        pnl=books.profit_and_loss(start, end),
        cash_flow=books.cash_flow_statement(start, end),
        balance_sheet=books.balance_sheet(end),
        forecast=forecast,
        reconciliation=books.reconcile(bank_txns, bank.get_balance(OPERATING_ACCOUNT_ID), as_of),
        ageing=books.receivables_ageing(as_of),
        weekly_sales=books.weekly_sales(PACK_SALES_WEEKS, as_of),
        readiness=readiness,
        financing=financing,
        going_concern_note=going_concern_note(forecast),
        accounting_policies=accounting_policies(framework),
        audit_final_hash=audit_final_hash,
        audit_chain_ok=audit_chain_ok,
    )


def pack_path(path: str) -> Path:
    from infra.paths import REPO_ROOT

    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p
