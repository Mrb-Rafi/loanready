"""Tiny DTO builders for tests. Every builder has sensible defaults from the tiny fixture."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from contracts.actions import AGENT, OWNER, Action, Principal
from contracts.canonical import params_hash, sha256_hex
from contracts.clock import get_clock
from contracts.dto_books import (
    BillDTO,
    CounterpartyDTO,
    InvoiceDTO,
    PayeeDTO,
    RecurringObligation,
    WeeklySales,
)
from contracts.dto_financing import ReadinessInputs
from contracts.dto_outputs import LoanPackDTO, MessageDraftDTO
from contracts.dto_planning import BillForecastItem, ForecastInputs, InvoiceForecastItem
from contracts.enums import (
    ActionType,
    BillStatus,
    Channel,
    CounterpartyKind,
    DraftKind,
    GeneratedBy,
    InvoiceStatus,
    Origin,
    PayeeStatus,
)
from contracts.ids import get_id_factory
from contracts.seed import SeedBundle
from contracts.testing import TINY_BUSINESS_PATH

AS_OF = date(2026, 10, 14)
FIXED_TS = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)


def load_tiny_bundle() -> SeedBundle:
    """``tiny_business.json`` (PRD/15 §3.1) as a SeedBundle."""
    return SeedBundle.model_validate(json.loads(TINY_BUSINESS_PATH.read_text(encoding="utf-8")))


def make_action(
    type: ActionType,
    params: dict[str, Any],
    origin: Origin = Origin.USER_REQUEST,
    principal: Principal = AGENT,
    on_behalf_of: Principal | None = OWNER,
    *,
    reason: str = "test action",
    provenance: list[Any] | None = None,
    action_id: str | None = None,
    requested_at: datetime | None = None,
) -> Action:
    return Action(
        action_id=action_id or get_id_factory().new("act"),
        type=type,
        params=params,
        origin=origin,
        provenance=provenance or [],
        reason=reason,
        requested_by=principal,
        on_behalf_of=on_behalf_of,
        requested_at=requested_at or get_clock().now(),
    )


def make_counterparty(**kw: Any) -> CounterpartyDTO:
    base: dict[str, Any] = dict(
        id="cpt_tiny_catering",
        name="Tiny Catering",
        kind=CounterpartyKind.CUSTOMER,
        payment_terms_days=14,
        lateness_history_days=[10, 20, 30],
        contact_ref="ap@tiny-catering.test",
    )
    base.update(kw)
    return CounterpartyDTO(**base)


def make_invoice(**kw: Any) -> InvoiceDTO:
    base: dict[str, Any] = dict(
        id="inv_tiny_1",
        number="INV-1",
        customer_id="cpt_tiny_catering",
        issue_date=date(2026, 9, 25),
        due_date=date(2026, 10, 9),
        amount_cents=60000,
        paid_cents=0,
        status=InvoiceStatus.OPEN,
    )
    base.update(kw)
    return InvoiceDTO(**base)


def make_bill(**kw: Any) -> BillDTO:
    base: dict[str, Any] = dict(
        id="bil_tiny_1",
        number="BILL-1",
        supplier_id="cpt_tiny_mills",
        payee_id="pye_tiny_mills",
        issue_date=date(2026, 10, 1),
        due_date=date(2026, 10, 20),
        amount_cents=80000,
        paid_cents=0,
        status=BillStatus.OPEN,
        category="5000",
        late_penalty=True,
        grace_days=0,
        essential=False,
        claimed_bank_account_ref=None,
        description="Flour delivery",
    )
    base.update(kw)
    return BillDTO(**base)


def make_payee(**kw: Any) -> PayeeDTO:
    base: dict[str, Any] = dict(
        id="pye_tiny_mills",
        counterparty_id="cpt_tiny_mills",
        name="Tiny Mills",
        bank_account_ref="SBX-TM-00000001",
        first_paid_on=None,
        status=PayeeStatus.KNOWN,
        preregistered_by_owner=True,
    )
    base.update(kw)
    return PayeeDTO(**base)


def make_draft(**kw: Any) -> MessageDraftDTO:
    body = kw.pop(
        "body",
        "Hello Tiny Catering,\n\nThis is a friendly reminder that invoice INV-1 for $600.00 was "
        "due on 09 Oct 2026. If you have already paid, thank you and please ignore this "
        "message.\n\nKind regards,\nTiny Bakery\n\nGenerated from a LoanReady template.",
    )
    base: dict[str, Any] = dict(
        draft_id="drf_tiny_1",
        kind=DraftKind.REMINDER,
        channel=Channel.EMAIL,
        to_ref="ap@tiny-catering.test",
        subject="Friendly reminder: invoice INV-1",
        body=body,
        body_sha256=sha256_hex(body),
        facts_hash=params_hash({"invoice_id": "inv_tiny_1"}),
        generated_by=GeneratedBy.TEMPLATE,
        ai_label="Generated from a LoanReady template.",
        related_id="inv_tiny_1",
        created_at=FIXED_TS,
        status="draft",
        sbx_ref=None,
    )
    base.update(kw)
    return MessageDraftDTO(**base)


def make_loan_pack(**kw: Any) -> LoanPackDTO:
    base: dict[str, Any] = dict(
        pack_id="pck_tiny_1",
        path="var/packs/loan_pack_2026-10-14_pck_tiny_1.pdf",
        sha256="0" * 64,
        sections=[
            "cover",
            "pnl",
            "cash_flow",
            "balance_sheet",
            "forecast",
            "reconciliation",
            "ageing",
            "readiness",
            "policies",
        ],
        created_at=FIXED_TS,
        metadata={"business": "Tiny Bakery", "as_of": "2026-10-14"},
    )
    base.update(kw)
    return LoanPackDTO(**base)


def make_forecast_inputs_tiny(crunch: bool = False) -> ForecastInputs:
    """Forecast inputs for the tiny fixture (PRD/15 §3.1). ``crunch`` adds BILL-2
    (5,000.00 due 2026-10-28, no penalty, not essential, grace 0)."""
    bills = [
        BillForecastItem(
            bill_id="bil_tiny_1",
            supplier_id="cpt_tiny_mills",
            due_date=date(2026, 10, 20),
            outstanding_cents=80000,
            category="5000",
            late_penalty=True,
            essential=False,
            grace_days=0,
        )
    ]
    if crunch:
        bills.append(
            BillForecastItem(
                bill_id="bil_tiny_2",
                supplier_id="cpt_tiny_mills",
                due_date=date(2026, 10, 28),
                outstanding_cents=500000,
                category="5000",
                late_penalty=False,
                essential=False,
                grace_days=0,
            )
        )
    return ForecastInputs(
        as_of=AS_OF,
        opening_cash_cents=381000,
        weekly_sales=[
            WeeklySales(
                week_start=date(2026, 9, 23),
                cash_cents=100000,
                card_sales_cents=0,
                total_cents=100000,
            ),
            WeeklySales(
                week_start=date(2026, 9, 30),
                cash_cents=120000,
                card_sales_cents=0,
                total_cents=120000,
            ),
            WeeklySales(
                week_start=date(2026, 10, 7),
                cash_cents=110000,
                card_sales_cents=0,
                total_cents=110000,
            ),
        ],
        cogs_ratio=Decimal("0.3000"),
        open_invoices=[
            InvoiceForecastItem(
                invoice_id="inv_tiny_1",
                customer_id="cpt_tiny_catering",
                due_date=date(2026, 10, 9),
                outstanding_cents=60000,
                lateness_history_days=[10, 20, 30],
            )
        ],
        open_bills=bills,
        recurring=[
            RecurringObligation(
                name="Rent",
                category="6000",
                amount_cents=50000,
                first_date=date(2026, 10, 1),
                schedule="monthly",
                day_of_month=1,
                every_days=None,
                end_date=None,
                essential=True,
            )
        ],
        events=[],
        safety_buffer_cents=None,
    )


def make_readiness_inputs(**kw: Any) -> ReadinessInputs:
    base: dict[str, Any] = dict(
        as_of=AS_OF,
        months_of_records=6,
        categorised_share=Decimal("0.95"),
        reconciled_share=Decimal("0.90"),
        personal_separated_share=Decimal("1"),
        unconfirmed_count=2,
        weekly_sales=[240000] * 12,
        weekly_operating_cf=[30000] * 10 + [-10000] * 2,
        receivables_cents=315000,
        overdue_receivables_cents=315000,
        monthly_sales_cents=1040000,
        monthly_debt_service_cents=0,
        monthly_operating_cf_cents=120000,
    )
    base.update(kw)
    return ReadinessInputs(**base)
