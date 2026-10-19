"""In-memory fakes for every port and service protocol (CON-22).

They let each WP build and test alone. They are deliberately simple and are **not** the
real implementations: ``FakeForecast`` is a small engine that reproduces the tiny-fixture
golden numbers of PRD/15 §3.1, and ``FakeAppFacade`` returns realistic canned DTOs built
from ``tiny_business.json`` so the UI renders every page.
"""

from __future__ import annotations

import base64
import csv
import io
import math
import secrets
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from contracts.actions import (
    BOOKKEEPER,
    CSV_COLUMNS,
    OWNER,
    PARAMS_MODELS,
    PRINCIPALS_BY_ROLE,
    SYSTEM,
    Action,
    ApprovalSummary,
    ApprovalToken,
    AuditRecord,
    ExecutionResult,
    ExecutionTicket,
    GateDecision,
    Principal,
    Proposal,
    binding_for,
    mask_account,
    mask_params,
)
from contracts.canonical import (
    ZERO_HASH,
    bytes_sha256,
    canonical_json,
    file_sha256,
    params_hash,
    sha256_hex,
)
from contracts.clock import Clock, FixedClock, get_clock, month_key, week_of, week_range
from contracts.dto_agent import LLMRequest, LLMResponse, RouterResult
from contracts.dto_app import CapUsage, ChatReply, ControlRow, DashboardDTO, TierRow
from contracts.dto_books import (
    AGEING_BUCKETS,
    AgeingRow,
    BalanceSheetDTO,
    BillDTO,
    CashFlowStatementDTO,
    CategorisationResult,
    ConfirmItemDTO,
    CounterpartyDTO,
    InvoiceDTO,
    JournalEntryDTO,
    PayeeDTO,
    ProfitAndLossDTO,
    RawTransactionDTO,
    ReceiptDTO,
    ReceivablesAgeingDTO,
    ReconciliationDTO,
    ReconMatch,
    RecurringObligation,
    StatementLine,
    WeeklySales,
)
from contracts.dto_financing import (
    CashFlowPoint,
    CatalogueDTO,
    DisclosureDTO,
    EligibleInvoice,
    FinancingComparisonDTO,
    FinancingOption,
    FinancingRequest,
    Improvement,
    ProductDTO,
    ReadinessComponent,
    ReadinessDTO,
    ReadinessInputs,
)
from contracts.dto_outputs import (
    DelayFacts,
    LoanPackContent,
    LoanPackDTO,
    MessageDraftDTO,
    ReminderFacts,
)
from contracts.dto_planning import (
    BillForecastItem,
    CashPlanDTO,
    CashPlanStep,
    DriversDTO,
    ForecastDTO,
    ForecastInputs,
    InvoiceForecastItem,
    ProposedDraft,
    ScenarioForecast,
    WeekRow,
)
from contracts.enums import (
    ActionType,
    BillStatus,
    CategorisationMethod,
    Channel,
    ConfirmKind,
    Decision,
    DraftKind,
    EventType,
    Framework,
    GeneratedBy,
    Intent,
    InvoiceStatus,
    Lever,
    LLMBackend,
    LLMTask,
    OcrBackend,
    ProductKind,
    ProposalStatus,
    RawStatus,
    ReadinessBand,
    ReasonCode,
    ReceiptStatus,
    Role,
    Scenario,
    SourceKind,
    Tier,
    Tone,
)
from contracts.errors import ApprovalError, SandboxError, TicketInvalid
from contracts.ids import get_id_factory
from contracts.money import fmt, from_cents, to_cents
from contracts.ports import Ports, TicketVerifier
from contracts.registry import CONTROL_IDS, OPERATING_ACCOUNT_ID
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
from contracts.testing.builders import load_tiny_bundle
from contracts.untrusted import ScanFlag, Untrusted, as_untrusted, scan_untrusted

FakeClock = FixedClock

AI_LABEL_TEMPLATE = "Generated from a LoanReady template."
FINANCING_NOTICES = [
    "Illustrative products dated 2026-10-14; not offers.",
    "LoanReady compares costs and prepares documents. It does not lend, arrange or accept loans.",
    "This is not financial advice.",
]
READINESS_DISCLAIMER = (
    "Self-assessment to help prepare a loan application. It is not a credit decision or a "
    "credit score from a lender."
)

# A copy of the PRD/03 tier table for fakes only; governance.tiers is the real one.
_FAKE_TIERS: dict[ActionType, Tier] = {
    **{
        t: Tier.T0_OBSERVE
        for t in (
            ActionType.CATEGORISE,
            ActionType.SEPARATE_PERSONAL,
            ActionType.BUILD_STATEMENTS,
            ActionType.RECONCILE,
            ActionType.RUN_FORECAST,
            ActionType.PLAN_CASH,
            ActionType.COMPARE_FINANCING,
            ActionType.SCORE_READINESS,
        )
    },
    **{
        t: Tier.T1_PROPOSE
        for t in (
            ActionType.DRAFT_REMINDER,
            ActionType.DRAFT_DELAY_REQUEST,
            ActionType.DRAFT_LOAN_PACK,
        )
    },
    **{
        t: Tier.T2_APPROVE
        for t in (
            ActionType.SEND_REMINDER,
            ActionType.SEND_DELAY_REQUEST,
            ActionType.SCHEDULE_SUPPLIER_PAYMENT,
            ActionType.CANCEL_PAYMENT,
            ActionType.SUBMIT_LOAN_PACK,
        )
    },
}
_SANDBOX_TARGETS = {
    ActionType.SCHEDULE_SUPPLIER_PAYMENT: "MockBank POST /bank/payments/schedule",
    ActionType.CANCEL_PAYMENT: "MockBank POST /bank/payments/{ref}/cancel",
    ActionType.SEND_REMINDER: "Messaging POST /messaging/send",
    ActionType.SEND_DELAY_REQUEST: "Messaging POST /messaging/send",
    ActionType.SUBMIT_LOAN_PACK: "Pretend lender POST /lender/applications",
}


def _tier_of(action_type: ActionType) -> Tier:
    return _FAKE_TIERS.get(action_type, Tier.BLOCKED)


# --- ticket verifiers ---------------------------------------------------------------------


def accept_all_verifier(
    ticket: ExecutionTicket, expected: ActionType, binding: Mapping[str, str]
) -> None:
    """A verifier that accepts every ticket (wiring tests only)."""


def make_simple_verifier(clock: Clock | None = None) -> TicketVerifier:
    """Checks type, expiry, single use and binding fields (no signature: fakes have no key)."""
    used: set[str] = set()

    def verify(ticket: ExecutionTicket, expected: ActionType, binding: Mapping[str, str]) -> None:
        now = (clock or get_clock()).now()
        if ticket.ticket_id in used:
            raise TicketInvalid("ticket already used")
        if ticket.action_type != expected:
            raise TicketInvalid("ticket action type mismatch")
        if now > ticket.expires_at:
            raise TicketInvalid("ticket expired")
        for key, value in binding.items():
            if ticket.binding.get(key) != value:
                raise TicketInvalid(f"binding mismatch on {key}")
        used.add(ticket.ticket_id)

    return verify


def make_fake_ticket(action: Action, clock: Clock | None = None) -> ExecutionTicket:
    """An unsigned ticket for fakes (the real one is minted only by governance)."""
    now = (clock or get_clock()).now()
    return ExecutionTicket(
        ticket_id=get_id_factory().new("tkt"),
        action_id=action.action_id,
        action_type=action.type,
        params_hash=params_hash(action.params),
        binding=binding_for(action.type, action.params),
        issued_at=now,
        expires_at=now + timedelta(seconds=60),
        signature="fake-unsigned",
    )


# --- ports ----------------------------------------------------------------------------------


class FakeBankPort:
    """In-memory MockBank: same-day payments settle at once; known payees only."""

    def __init__(
        self,
        bundle: SeedBundle | None = None,
        verifier: TicketVerifier | None = None,
        today: date | None = None,
    ):
        b = bundle or load_tiny_bundle()
        self.verifier = verifier or accept_all_verifier
        self.today = today or b.as_of
        self.accounts = {a.id: a for a in b.bank_accounts}
        self.payees = {p.id: p for p in b.bank_payees}
        self.payments: dict[str, PaymentOut] = {}
        self.txns: list[BankTxnOut] = []
        for acc in b.bank_accounts:
            seed_sum = sum(t.amount_cents for t in b.bank_transactions if t.account_id == acc.id)
            opening = acc.balance_cents - seed_sum
            self.txns.append(
                BankTxnOut(
                    id=f"txn_open_{acc.id}",
                    account_id=acc.id,
                    date=b.history_start,
                    amount_cents=opening,
                    description="OPENING BALANCE",
                    counterparty=None,
                    kind="opening",
                    payment_ref=None,
                    balance_after_cents=opening,
                )
            )
        self.txns.extend(b.bank_transactions)
        self.calls: list[tuple[str, Any]] = []

    def list_accounts(self) -> list[BankAccountOut]:
        return [
            a.model_copy(update={"balance_cents": self.get_balance(a.id)})
            for a in self.accounts.values()
        ]

    def get_balance(self, account_id: str) -> int:
        if account_id not in self.accounts:
            raise SandboxError(
                "unknown account",
                reason_code=ReasonCode.UNKNOWN_ENTITY,
                http_status=404,
                sandbox_code="UNKNOWN_ACCOUNT",
            )
        return sum(t.amount_cents for t in self.txns if t.account_id == account_id)

    def list_transactions(self, account_id: str, start: date, end: date) -> list[BankTxnOut]:
        return [t for t in self.txns if t.account_id == account_id and start <= t.date <= end]

    def list_payees(self) -> list[PayeeOut]:
        return list(self.payees.values())

    def schedule_payment(self, ticket: ExecutionTicket, req: PaymentScheduleIn) -> PaymentOut:
        self.verifier(
            ticket,
            ActionType.SCHEDULE_SUPPLIER_PAYMENT,
            binding_for(ActionType.SCHEDULE_SUPPLIER_PAYMENT, req.model_dump()),
        )
        self.calls.append(("schedule_payment", req))
        sbx_ref = "pay_" + sha256_hex(ticket.action_id)[:12]
        if sbx_ref in self.payments:  # idempotent replay
            return self.payments[sbx_ref]
        payee = self.payees.get(req.payee_id)
        if payee is None or payee.status != "known":
            raise SandboxError(
                "payee unknown",
                reason_code=ReasonCode.NEW_PAYEE,
                http_status=403,
                sandbox_code="PAYEE_UNKNOWN",
            )
        if payee.bank_account_ref != req.bank_account_ref:
            raise SandboxError(
                "account mismatch",
                reason_code=ReasonCode.ACCOUNT_CHANGED,
                http_status=403,
                sandbox_code="ACCOUNT_MISMATCH",
            )
        if req.amount_cents > self.get_balance(req.from_account_id):
            raise SandboxError(
                "insufficient funds",
                reason_code=ReasonCode.INSUFFICIENT_FUNDS,
                http_status=409,
                sandbox_code="INSUFFICIENT_FUNDS",
            )
        status = "scheduled"
        settled_on = None
        if req.value_date <= self.today:
            status, settled_on = "settled", req.value_date
            balance = self.get_balance(req.from_account_id) - req.amount_cents
            self.txns.append(
                BankTxnOut(
                    id="txn_" + sha256_hex(sbx_ref)[:12],
                    account_id=req.from_account_id,
                    date=req.value_date,
                    amount_cents=-req.amount_cents,
                    description=f"{payee.name} {req.reference}",
                    counterparty=payee.name,
                    kind="payment",
                    payment_ref=sbx_ref,
                    balance_after_cents=balance,
                )
            )
            if payee.first_paid_on is None:
                self.payees[payee.id] = payee.model_copy(update={"first_paid_on": req.value_date})
        out = PaymentOut(
            sbx_ref=sbx_ref,
            from_account_id=req.from_account_id,
            payee_id=req.payee_id,
            amount_cents=req.amount_cents,
            value_date=req.value_date,
            reference=req.reference,
            status=status,
            failure_code=None,
            settled_on=settled_on,
            created_at=get_clock().now(),
        )
        self.payments[sbx_ref] = out
        return out

    def cancel_payment(self, ticket: ExecutionTicket, sbx_ref: str) -> PaymentOut:
        self.verifier(ticket, ActionType.CANCEL_PAYMENT, {"sbx_ref": sbx_ref})
        p = self.get_payment(sbx_ref)
        if p.status != "scheduled" or p.value_date <= self.today:
            raise SandboxError("not cancellable", http_status=409, sandbox_code="NOT_CANCELLABLE")
        out = p.model_copy(update={"status": "cancelled"})
        self.payments[sbx_ref] = out
        return out

    def list_payments(self, status: str | None = None) -> list[PaymentOut]:
        return [p for p in self.payments.values() if status is None or p.status == status]

    def get_payment(self, sbx_ref: str) -> PaymentOut:
        if sbx_ref not in self.payments:
            raise SandboxError(
                "unknown payment",
                reason_code=ReasonCode.UNKNOWN_ENTITY,
                http_status=404,
                sandbox_code="UNKNOWN_PAYMENT",
            )
        return self.payments[sbx_ref]


class FakePosPort:
    def __init__(self, bundle: SeedBundle | None = None):
        self.sales = list((bundle or load_tiny_bundle()).pos_sales)

    def list_sales(self, start: date, end: date) -> list[PosSaleOut]:
        return [s for s in self.sales if start <= s.ts.date() <= end]


class FakeMessagePort:
    def __init__(self, verifier: TicketVerifier | None = None):
        self.verifier = verifier or accept_all_verifier
        self.messages: dict[str, MessageOut] = {}

    def send(self, ticket: ExecutionTicket, req: MessageSendIn) -> MessageOut:
        expected = ticket.action_type
        if expected not in (ActionType.SEND_REMINDER, ActionType.SEND_DELAY_REQUEST):
            raise TicketInvalid("ticket is not for a message")
        binding = {
            "to_ref": req.to_ref,
            "body_sha256": sha256_hex(req.body),
            "channel": req.channel,
        }
        self.verifier(ticket, expected, binding)
        sbx_ref = "msg_" + sha256_hex(ticket.action_id)[:12]
        out = self.messages.get(sbx_ref) or MessageOut(
            sbx_ref=sbx_ref,
            channel=req.channel,
            to_ref=req.to_ref,
            subject=req.subject,
            body_sha256=sha256_hex(req.body),
            status="delivered_sandbox",
            created_at=get_clock().now(),
        )
        self.messages[sbx_ref] = out
        return out

    def list_messages(self) -> list[MessageOut]:
        return list(self.messages.values())


class FakeLenderPort:
    def __init__(self, verifier: TicketVerifier | None = None):
        self.verifier = verifier or accept_all_verifier
        self.applications: dict[str, LenderReceiptOut] = {}

    def submit_application(
        self, ticket: ExecutionTicket, req: LenderApplicationIn
    ) -> LenderReceiptOut:
        pdf = base64.b64decode(req.pdf_base64)
        self.verifier(
            ticket,
            ActionType.SUBMIT_LOAN_PACK,
            {"pack_sha256": bytes_sha256(pdf), "lender_id": req.lender_id},
        )
        if bytes_sha256(pdf) != req.pack_sha256:
            raise SandboxError(
                "pack hash mismatch",
                reason_code=ReasonCode.PACK_HASH_MISMATCH,
                http_status=422,
                sandbox_code="PACK_HASH_MISMATCH",
            )
        receipt_id = "rcv_" + sha256_hex(ticket.action_id)[:12]
        required = [
            "cover",
            "pnl",
            "cash_flow",
            "balance_sheet",
            "forecast",
            "reconciliation",
            "ageing",
            "readiness",
            "policies",
        ]
        out = LenderReceiptOut(
            receipt_id=receipt_id,
            status="received_under_review",
            pack_sha256=req.pack_sha256,
            received_at=get_clock().now(),
            missing_sections=[s for s in required if s not in req.sections],
        )
        self.applications[receipt_id] = out
        return out

    def get_application(self, receipt_id: str) -> LenderReceiptOut:
        if receipt_id not in self.applications:
            raise SandboxError(
                "unknown application",
                reason_code=ReasonCode.UNKNOWN_ENTITY,
                http_status=404,
                sandbox_code="UNKNOWN_APPLICATION",
            )
        return self.applications[receipt_id]


class FakeSandboxAdmin:
    def __init__(self, today: date = date(2026, 10, 14)):
        self.today = today
        self.bundles: list[SeedBundle] = []

    def reset(self, bundle: SeedBundle) -> dict[str, int]:
        self.bundles.append(bundle)
        self.today = bundle.as_of
        return {
            "accounts": len(bundle.bank_accounts),
            "transactions": len(bundle.bank_transactions),
            "payees": len(bundle.bank_payees),
            "pos_sales": len(bundle.pos_sales),
        }

    def health(self) -> dict[str, str]:
        return {
            "status": "ok",
            "today": self.today.isoformat(),
            "version": "1",
            "openapi_sha256": "fake",
        }

    def get_clock(self) -> date:
        return self.today

    def set_clock(self, today: date) -> date:
        if today < self.today:
            raise SandboxError("clock backwards", http_status=422, sandbox_code="CLOCK_BACKWARDS")
        self.today = today
        return today

    def opslog_csv(self) -> str:
        cols = (
            "seq,ts,subapp,method,path,scope,idempotency_key,ticket_id,status_code,error_code,"
            "request_sha256,response_sha256,prev_hash,row_hash"
        )
        return cols + "\n"

    def opslog_verify(self) -> dict[str, int | None]:
        return {"bank": None, "pos": None, "messaging": None, "lender": None}


def make_fake_ports(
    bundle: SeedBundle | None = None, verifier: TicketVerifier | None = None
) -> Ports:
    b = bundle or load_tiny_bundle()
    return Ports(
        bank=FakeBankPort(b, verifier),
        pos=FakePosPort(b),
        messaging=FakeMessagePort(verifier),
        lender=FakeLenderPort(verifier),
        admin=FakeSandboxAdmin(b.as_of),
    )


# --- governance fakes -----------------------------------------------------------------------


class FakeAudit:
    """In-memory hash-chained log with the same row-hash rule as WP1 (GOV-13)."""

    def __init__(self, clock: Clock | None = None):
        self._clock = clock
        self._rows: list[AuditRecord] = []

    def _now(self) -> datetime:
        return (self._clock or get_clock()).now()

    def append(
        self,
        *,
        action_id: str | None,
        action_type: str,
        decision: Decision,
        reason_code: ReasonCode,
        principal_id: str,
        tier: int | None = None,
        origin: Any = None,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        params: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        sbx_ref: str | None = None,
        control_ids: Sequence[str] = (),
    ) -> int:
        prev = self._rows[-1].row_hash if self._rows else ZERO_HASH
        record = dict(
            seq=len(self._rows) + 1,
            ts=self._now(),
            action_id=action_id,
            action_type=action_type,
            tier=tier,
            origin=origin,
            principal_id=principal_id,
            decision=decision,
            reason_code=reason_code,
            approved_by=approved_by,
            approved_at=approved_at,
            params_masked=mask_params(params or {}),
            result_masked=mask_params(result or {}),
            sbx_ref=sbx_ref,
            control_ids=list(control_ids),
            prev_hash=prev,
        )
        row_hash = sha256_hex(prev + canonical_json(record))
        self._rows.append(AuditRecord(**record, row_hash=row_hash))
        return len(self._rows)

    def log_event(
        self,
        event: EventType,
        details: dict[str, Any],
        principal: Principal,
        control_ids: Sequence[str] = (),
    ) -> int:
        return self.append(
            action_id=None,
            action_type=event.value,
            decision=Decision.EVENT,
            reason_code=ReasonCode.OK,
            principal_id=principal.id,
            params=details,
            control_ids=control_ids,
        )

    def rows(self, limit: int | None = None) -> list[AuditRecord]:
        return list(self._rows if limit is None else self._rows[-limit:])

    def verify_chain(self) -> int | None:
        prev = ZERO_HASH
        for i, row in enumerate(self._rows, start=1):
            data = row.model_dump(mode="python", exclude={"row_hash"})
            if (
                row.seq != i
                or row.prev_hash != prev
                or sha256_hex(prev + canonical_json(data)) != row.row_hash
            ):
                return i
            prev = row.row_hash
        return None

    def export_csv(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for r in self._rows:
            d = r.model_dump(mode="python")
            writer.writerow(
                [
                    canonical_json(d[c])
                    if isinstance(d[c], (dict, list))
                    else ("" if d[c] is None else canonical_json(d[c]).strip('"'))
                    for c in CSV_COLUMNS
                ]
            )
        path.write_text(buf.getvalue(), encoding="utf-8", newline="")
        self.log_event(EventType.EXPORT_CREATED, {"path": path.name}, SYSTEM, ["CTL-LOG-03"])
        return path

    def counts(self) -> dict[str, int]:
        out = {d.value: 0 for d in Decision}
        for r in self._rows:
            out[r.decision.value] += 1
        return out


class FakeControls:
    def controls(self) -> list[ControlRow]:
        return [
            ControlRow(
                control_id=c,
                title=f"{c} (fake)",
                regulation_refs=[],
                implemented_in="fake",
                tests=[],
            )
            for c in CONTROL_IDS
        ]


class FakeExecutor:
    """Records calls; returns a successful result with a deterministic sbx_ref."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[Action, ExecutionTicket]] = []
        self.fail = fail

    def execute(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        self.calls.append((action, ticket))
        if self.fail:
            return ExecutionResult(
                action_id=action.action_id,
                ok=False,
                sbx_ref=None,
                result={},
                error_code=ReasonCode.SANDBOX_ERROR,
            )
        return ExecutionResult(
            action_id=action.action_id,
            ok=True,
            sbx_ref="sbx_" + sha256_hex(action.action_id)[:12],
            result={"status": "ok"},
        )


class FakeGate:
    """Allows everything that is not blocked, records calls. Not the real gate (WP1)."""

    def __init__(
        self, executor: Any = None, audit: FakeAudit | None = None, clock: Clock | None = None
    ):
        self.executor = executor or FakeExecutor()
        self.audit = audit or FakeAudit(clock)
        self._clock = clock
        self.calls: list[tuple[str, Any]] = []
        self.proposals: dict[str, Proposal] = {}
        self.tokens: dict[str, ApprovalToken] = {}
        self.kill_switch = False
        self.settings: dict[str, str] = {
            "cap_per_action_cents": "500000",
            "cap_per_day_cents": "1000000",
            "cap_per_payee_week_cents": "800000",
            "cap_msgs_per_customer_week": "2",
        }
        self.used_today_cents = 0

    def _now(self) -> datetime:
        return (self._clock or get_clock()).now()

    def _decide(
        self,
        action: Action,
        decision: Decision,
        reason: ReasonCode,
        tier: Tier,
        proposal_id: str | None = None,
        summary: ApprovalSummary | None = None,
        result: ExecutionResult | None = None,
    ) -> GateDecision:
        seq = self.audit.append(
            action_id=action.action_id,
            action_type=action.type.value,
            decision=decision,
            reason_code=reason,
            principal_id=action.requested_by.id,
            tier=int(tier),
            origin=action.origin,
            params=action.params,
            sbx_ref=result.sbx_ref if result else None,
        )
        return GateDecision(
            action_id=action.action_id,
            proposal_id=proposal_id,
            decision=decision,
            tier=tier,
            reason_code=reason,
            message=reason.value,
            log_seq=seq,
            result=result,
            summary=summary,
        )

    def evaluate(self, action: Action) -> GateDecision:
        self.calls.append(("evaluate", action))
        tier = _tier_of(action.type)
        if tier == Tier.BLOCKED:
            return self._decide(action, Decision.BLOCKED, ReasonCode.BLOCKED_TYPE, tier)
        if self.kill_switch and tier != Tier.T0_OBSERVE:
            return self._decide(action, Decision.BLOCKED, ReasonCode.KILL_SWITCH, tier)
        if tier == Tier.T0_OBSERVE:
            return self._decide(action, Decision.ALLOWED, ReasonCode.OK, tier)
        if tier == Tier.T1_PROPOSE:
            return self._decide(action, Decision.DRAFTED, ReasonCode.OK, tier)
        pid = get_id_factory().new("prp")
        summary = ApprovalSummary(
            proposal_id=pid,
            action_type=action.type,
            tier=tier,
            amount_cents=action.params.get("amount_cents"),
            currency="USD",
            counterparty_name=action.params.get("payee_id") or action.params.get("to_ref"),
            payee_account_masked=mask_account(action.params["bank_account_ref"])
            if "bank_account_ref" in action.params
            else None,
            reason=action.reason,
            sandbox_target=_SANDBOX_TARGETS.get(action.type, ""),
            cap_used_today_cents=self.used_today_cents,
            cap_per_day_cents=1000000,
            cap_per_action_cents=500000,
            warnings=[],
            expires_at=None,
        )
        self.proposals[pid] = Proposal(
            proposal_id=pid,
            action=action,
            tier=tier,
            status=ProposalStatus.PENDING_APPROVAL,
            params_hash=params_hash(action.params),
            created_at=self._now(),
            summary=summary,
        )
        return self._decide(
            action,
            Decision.PENDING_APPROVAL,
            ReasonCode.APPROVAL_REQUIRED,
            tier,
            proposal_id=pid,
            summary=summary,
        )

    def approve(self, proposal_id: str, principal: Principal) -> ApprovalToken:
        self.calls.append(("approve", proposal_id))
        prop = self.proposals.get(proposal_id)
        if prop is None or prop.status != ProposalStatus.PENDING_APPROVAL:
            raise ApprovalError("no pending proposal", reason_code=ReasonCode.APPROVAL_INVALID)
        now = self._now()
        token = ApprovalToken(
            token_id=secrets.token_hex(16),
            proposal_id=proposal_id,
            params_hash=prop.params_hash,
            approved_by=principal,
            approved_at=now,
            expires_at=now + timedelta(seconds=900),
        )
        self.tokens[token.token_id] = token
        self.proposals[proposal_id] = prop.model_copy(update={"status": ProposalStatus.APPROVED})
        self.audit.append(
            action_id=prop.action.action_id,
            action_type=prop.action.type.value,
            decision=Decision.APPROVED,
            reason_code=ReasonCode.OK,
            principal_id=principal.id,
            tier=int(prop.tier),
            approved_by=principal.id,
            approved_at=now,
        )
        return token

    def reject(self, proposal_id: str, principal: Principal, note: str = "") -> GateDecision:
        prop = self.proposals[proposal_id]
        self.proposals[proposal_id] = prop.model_copy(update={"status": ProposalStatus.REJECTED})
        return self._decide(
            prop.action,
            Decision.REJECTED,
            ReasonCode.REJECTED_BY_USER,
            prop.tier,
            proposal_id=proposal_id,
        )

    def execute(self, proposal_id: str, token_id: str | None, principal: Principal) -> GateDecision:
        self.calls.append(("execute", proposal_id))
        prop = self.proposals[proposal_id]
        if token_id is None or token_id not in self.tokens:
            return self._decide(
                prop.action,
                Decision.BLOCKED,
                ReasonCode.APPROVAL_REQUIRED,
                prop.tier,
                proposal_id=proposal_id,
            )
        if self.kill_switch:
            return self._decide(
                prop.action,
                Decision.BLOCKED,
                ReasonCode.KILL_SWITCH,
                prop.tier,
                proposal_id=proposal_id,
            )
        del self.tokens[token_id]
        result = self.executor.execute(prop.action, make_fake_ticket(prop.action, self._clock))
        status = ProposalStatus.EXECUTED if result.ok else ProposalStatus.FAILED
        self.proposals[proposal_id] = prop.model_copy(update={"status": status})
        if result.ok:
            self.used_today_cents += int(prop.action.params.get("amount_cents", 0) or 0)
        return self._decide(
            prop.action,
            Decision.EXECUTED if result.ok else Decision.FAILED,
            ReasonCode.EXECUTED if result.ok else (result.error_code or ReasonCode.SANDBOX_ERROR),
            prop.tier,
            proposal_id=proposal_id,
            result=result,
        )

    def pending(self) -> list[Proposal]:
        return [p for p in self.proposals.values() if p.status == ProposalStatus.PENDING_APPROVAL]

    def proposal(self, proposal_id: str) -> Proposal | None:
        return self.proposals.get(proposal_id)

    def cap_usage(self) -> CapUsage:
        return CapUsage(
            per_action_cents=int(self.settings["cap_per_action_cents"]),
            per_day_cents=int(self.settings["cap_per_day_cents"]),
            used_today_cents=self.used_today_cents,
            per_payee_week_cents=int(self.settings["cap_per_payee_week_cents"]),
            msgs_per_customer_week=int(self.settings["cap_msgs_per_customer_week"]),
        )

    def set_kill_switch(self, on: bool, principal: Principal) -> None:
        self.kill_switch = on
        self.audit.log_event(EventType.KILL_SWITCH_TOGGLED, {"on": on}, principal, ["CTL-AC-07"])

    def update_setting(self, key: str, value: str, principal: Principal) -> None:
        before = self.settings.get(key)
        self.settings[key] = value
        self.audit.log_event(
            EventType.SETTINGS_CHANGED,
            {"key": key, "before": before, "after": value},
            principal,
            ["CTL-CHG-01"],
        )

    def tier_table(self) -> list[TierRow]:
        return [
            TierRow(
                action_type=t.value,
                tier=_tier_of(t).name,
                description=f"{t.value} (fake)",
                safeguards=[],
            )
            for t in ActionType
        ]

    def ticket_verifier(self) -> TicketVerifier:
        return accept_all_verifier


@dataclass
class FakeEntityLookup:
    payees: list[PayeeDTO] = field(default_factory=list)
    bills: list[BillDTO] = field(default_factory=list)
    invoices: list[InvoiceDTO] = field(default_factory=list)
    counterparties: list[CounterpartyDTO] = field(default_factory=list)
    drafts: list[MessageDraftDTO] = field(default_factory=list)
    packs: list[LoanPackDTO] = field(default_factory=list)
    closed_months: set[str] = field(default_factory=set)
    open_items: int = 0

    @classmethod
    def from_bundle(cls, bundle: SeedBundle | None = None) -> FakeEntityLookup:
        b = bundle or load_tiny_bundle()
        return cls(
            payees=list(b.payees),
            bills=list(b.bills),
            invoices=list(b.invoices),
            counterparties=list(b.counterparties),
        )

    def payee(self, payee_id: str) -> PayeeDTO | None:
        return next((p for p in self.payees if p.id == payee_id), None)

    def bill(self, bill_id: str) -> BillDTO | None:
        return next((b for b in self.bills if b.id == bill_id), None)

    def invoice(self, invoice_id: str) -> InvoiceDTO | None:
        return next((i for i in self.invoices if i.id == invoice_id), None)

    def counterparty(self, cp_id: str) -> CounterpartyDTO | None:
        return next((c for c in self.counterparties if c.id == cp_id), None)

    def draft(self, draft_id: str) -> MessageDraftDTO | None:
        return next((d for d in self.drafts if d.draft_id == draft_id), None)

    def loan_pack(self, pack_id: str) -> LoanPackDTO | None:
        return next((p for p in self.packs if p.pack_id == pack_id), None)

    def period_is_closed(self, d: date) -> bool:
        return month_key(d) in self.closed_months

    def reconciliation_open_items(self) -> int:
        return self.open_items


# --- agent fakes ------------------------------------------------------------------------------

_KEYWORDS = {
    "FLOUR": "5000",
    "GRAIN": "5000",
    "MILLS": "5000",
    "RENT": "6000",
    "PAYROLL": "6100",
    "POWER": "6200",
    "INTERNET": "6200",
    "PROMO": "6300",
    "FEE": "6400",
    "FRESHMART": "PERSONAL",
    "CINEPLEX": "PERSONAL",
    "TOPUP": "PERSONAL",
}


class FakeClassifier:
    """Keyword classifier: confidence 0.60 on a match (owner confirms), else 6900 at 0."""

    def classify(
        self, text: Untrusted, candidates: list[str], task: LLMTask
    ) -> CategorisationResult:
        upper = str(text).upper()
        for word, label in _KEYWORDS.items():
            if word in upper and label in candidates:
                return CategorisationResult(
                    raw_txn_id=text.source_ref,
                    label=label,
                    confidence=Decimal("0.60"),
                    method=CategorisationMethod.LLM,
                    needs_confirmation=True,
                    rule_id=None,
                )
        return CategorisationResult(
            raw_txn_id=text.source_ref,
            label="6900",
            confidence=Decimal("0"),
            method=CategorisationMethod.FALLBACK,
            needs_confirmation=True,
            rule_id=None,
        )


class FakeWording:
    """Template wording (the real templates live in outputs; these are close copies)."""

    def word_reminder(self, facts: ReminderFacts) -> tuple[str, str, GeneratedBy]:
        subject = f"Friendly reminder: invoice {facts.invoice_number}"
        body = (
            f"Hello {facts.customer_name},\n\nThis is a friendly reminder that invoice "
            f"{facts.invoice_number} for {facts.amount_str} was due on {facts.due_date_str}. "
            f"If you have already paid, thank you and please ignore this message.\n\n"
            f"Kind regards,\n{facts.business_name}\n\n{AI_LABEL_TEMPLATE}"
        )
        return subject, body, GeneratedBy.TEMPLATE

    def word_delay_request(self, facts: DelayFacts) -> tuple[str, str, GeneratedBy]:
        subject = f"Request to adjust payment date for {facts.bill_number}"
        body = (
            f"Hello {facts.supplier_name},\n\nThank you for invoice {facts.bill_number} for "
            f"{facts.amount_str}, due on {facts.due_date_str}. Could we pay on "
            f"{facts.proposed_date_str} instead, within your usual grace period? Please reply "
            f"to confirm.\n\nKind regards,\n{facts.business_name}\n\n{AI_LABEL_TEMPLATE}"
        )
        return subject, body, GeneratedBy.TEMPLATE


class FakeLLM:
    """Deterministic mock answers per task; records requests."""

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        if req.task in (LLMTask.CATEGORISE_TXN, LLMTask.PERSONAL_VS_BUSINESS):
            enum = req.output_schema.get("properties", {}).get("label", {}).get("enum", ["6900"])
            output: dict[str, Any] = {"label": enum[0] if enum else "6900", "confidence": 0}
        elif req.task in (LLMTask.DRAFT_REMINDER, LLMTask.DRAFT_DELAY_REQUEST):
            output = {"subject": "Reminder", "body": "Template body"}
        elif req.task == LLMTask.INTENT_PICK:
            output = {"intent": Intent.UNKNOWN.value, "confidence": 0}
        else:
            output = {"text": "Explanation from template."}
        return LLMResponse(
            task=req.task,
            backend=LLMBackend.MOCK,
            model="mock",
            output=output,
            cached=False,
            fallback_used=False,
            latency_ms=0,
            attempts=[LLMBackend.MOCK],
        )


class FakeAgent:
    def __init__(self) -> None:
        self._classifier = FakeClassifier()
        self._wording = FakeWording()

    def classifier(self) -> FakeClassifier:
        return self._classifier

    def wording(self) -> FakeWording:
        return self._wording

    def route(self, message: str) -> RouterResult:
        m = message.lower()
        rules = [
            ("remind", Intent.DRAFT_REMINDERS),
            ("owe", Intent.WHO_OWES_ME),
            ("loan pack", Intent.BUILD_LOAN_PACK),
            ("borrow", Intent.COMPARE_FINANCING),
            ("delay", Intent.DELAY_BILLS),
            ("cash", Intent.CASH_POSITION),
            ("ready", Intent.READINESS),
            ("log", Intent.SHOW_LOG),
            ("help", Intent.HELP),
        ]
        for word, intent in rules:
            if word in m:
                return RouterResult(
                    intent=intent, confidence=Decimal("0.9"), method="rule", params={}, missing=[]
                )
        return RouterResult(
            intent=Intent.UNKNOWN, confidence=Decimal("0"), method="fallback", params={}, missing=[]
        )

    def explain_forecast(self, forecast: ForecastDTO) -> str:
        f = forecast.drivers.facts
        if forecast.drivers.crossing_week is None:
            return "Cash stays above zero in all 13 weeks of the base case."
        return (
            f"Cash dips below zero in week {f.get('week')} (from {f.get('week_start')}) because "
            f"{f.get('main_outflow')} ({f.get('main_outflow_amount')}) is due before "
            f"{f.get('delayed_count')} customer invoices ({f.get('delayed_total')}) are expected "
            f"to be paid."
        )

    def explain_financing(self, comparison: FinancingComparisonDTO) -> str:
        by_id = {o.product_id: o for o in comparison.options}
        cheap = by_id.get(comparison.cheapest_id or "")
        dear = by_id.get(comparison.most_expensive_id or "")
        if not cheap or not dear:
            return "No eligible financing options for this request."
        return (
            f"The cheapest option is {cheap.name}; the most expensive is {dear.name}. "
            f"Compare the effective annual rates before deciding."
        )

    def scan(self, text: Untrusted) -> list[ScanFlag]:
        return scan_untrusted(text)

    def status(self) -> dict[str, Any]:
        return {
            "mode": "MOCK",
            "available": ["MOCK"],
            "models": {"MOCK": "mock"},
            "remote_calls_today": 0,
            "remote_ai": False,
        }


# --- books fake -------------------------------------------------------------------------------


def _line(key: str, label: str, values: dict[str, int], level: int = 1) -> StatementLine:
    return StatementLine(key=key, label=label, values_cents=values, level=level)


class FakeBooks:
    """Canned books for the tiny fixture (PRD/15 §3.1 numbers)."""

    def __init__(self, bundle: SeedBundle | None = None, extra_bills: Sequence[BillDTO] = ()):
        b = bundle or load_tiny_bundle()
        self.bundle = b
        self._cps = {c.id: c for c in b.counterparties}
        self._payees = {p.id: p for p in b.payees}
        self._invoices = {i.id: i for i in b.invoices}
        self._bills = {x.id: x for x in [*b.bills, *extra_bills]}
        self._confirm: list[ConfirmItemDTO] = []
        self._closed: set[str] = set()

    def load_master_data(
        self,
        counterparties: list[CounterpartyDTO],
        payees: list[PayeeDTO],
        invoices: list[InvoiceDTO],
        bills: list[BillDTO],
    ) -> None:
        self._cps.update({c.id: c for c in counterparties})
        self._payees.update({p.id: p for p in payees})
        self._invoices.update({i.id: i for i in invoices})
        self._bills.update({x.id: x for x in bills})

    def ingest_bank(self, txns: list[BankTxnOut]) -> list[RawTransactionDTO]:
        return [
            RawTransactionDTO(
                id=f"rtx_{t.id}",
                source=SourceKind.BANK,
                source_ref=t.id,
                date=t.date,
                amount_cents=t.amount_cents,
                description=t.description,
                counterparty_hint=t.counterparty,
                tender=None,
                payload_hash=params_hash(t),
                status=RawStatus.NEW,
            )
            for t in txns
        ]

    def ingest_pos(self, sales: list[PosSaleOut]) -> list[RawTransactionDTO]:
        return [
            RawTransactionDTO(
                id=f"rtx_{s.id}",
                source=SourceKind.POS,
                source_ref=s.id,
                date=s.ts.date(),
                amount_cents=s.amount_cents,
                description=s.note or "POS sale",
                counterparty_hint=None,
                tender=s.tender,
                payload_hash=params_hash(s),
                status=RawStatus.POSTED,
            )
            for s in sales
        ]

    def ingest_receipt(self, image_path: Path) -> ReceiptDTO:
        text = "RECEIPT\nTOTAL 0.00"
        flags = scan_untrusted(as_untrusted(text, SourceKind.RECEIPT, Path(image_path).name))
        return ReceiptDTO(
            id=get_id_factory().new("rcp"),
            image_path=str(image_path),
            ocr_text=text,
            ocr_backend=OcrBackend.SIDECAR,
            vendor=None,
            total_cents=None,
            date=None,
            confidence=Decimal("0.30"),
            status=ReceiptStatus.NEEDS_CONFIRMATION,
            flags=flags,
        )

    def categorise_pending(self, classifier: Any) -> list[CategorisationResult]:
        return []

    def post_ready(self) -> int:
        return 0

    def confirm_queue(self) -> list[ConfirmItemDTO]:
        return list(self._confirm)

    def resolve(self, item_id: str, choice: str, principal: Principal) -> None:
        self._confirm = [c for c in self._confirm if c.id != item_id]

    def record_customer_payment(
        self, invoice_id: str, amount_cents: int, d: date, ref: str
    ) -> None:
        inv = self._invoices[invoice_id]
        paid = inv.paid_cents + amount_cents
        self._invoices[invoice_id] = inv.model_copy(
            update={
                "paid_cents": paid,
                "status": InvoiceStatus.PAID if paid >= inv.amount_cents else InvoiceStatus.PARTIAL,
            }
        )

    def record_bill_payment(self, bill_id: str, amount_cents: int, d: date, ref: str) -> None:
        bill = self._bills[bill_id]
        paid = bill.paid_cents + amount_cents
        self._bills[bill_id] = bill.model_copy(
            update={
                "paid_cents": paid,
                "status": BillStatus.PAID if paid >= bill.amount_cents else BillStatus.PARTIAL,
            }
        )

    def journal(self, start: date | None = None, end: date | None = None) -> list[JournalEntryDTO]:
        return []

    def profit_and_loss(self, start: date, end: date) -> ProfitAndLossDTO:
        sep, octo = "2026-09", "2026-10"
        lines = [
            _line("revenue", "Revenue", {sep: 160000, octo: 230000, "total": 390000}),
            _line("cogs", "Cost of goods sold", {sep: 30000, octo: 149000, "total": 179000}),
            _line("gross_profit", "Gross profit", {sep: 130000, octo: 81000, "total": 211000}, 2),
            _line("6000", "Rent", {sep: 0, octo: 50000, "total": 50000}),
            _line(
                "operating_profit",
                "Operating profit",
                {sep: 130000, octo: 31000, "total": 161000},
                2,
            ),
            _line("net_profit", "Net profit", {sep: 130000, octo: 31000, "total": 161000}, 3),
        ]
        return ProfitAndLossDTO(
            framework=Framework.IFRS_SME,
            periods=[sep, octo],
            lines=lines,
            net_profit_cents={sep: 130000, octo: 31000},
            total_net_profit_cents=161000,
        )

    def cash_flow_statement(self, start: date, end: date) -> CashFlowStatementDTO:
        p = "period"
        lines = [
            _line("operating", "Operating activities", {}, 0),
            _line("received_customers", "Cash received from customers", {p: 330000}),
            _line("paid_suppliers", "Cash paid to suppliers", {p: -99000}),
            _line("rent_paid", "Rent paid", {p: -50000}),
            _line("net_operating", "Net cash from operating activities", {p: 181000}, 2),
        ]
        return CashFlowStatementDTO(
            framework=Framework.IFRS_SME,
            method="direct",
            start=start,
            end=end,
            opening_cash_cents=200000,
            lines=lines,
            net_operating_cents=181000,
            net_investing_cents=0,
            net_financing_cents=0,
            closing_cash_cents=381000,
        )

    def balance_sheet(self, as_of: date) -> BalanceSheetDTO:
        a = "as_of"
        lines = [
            _line("1000", "Cash at bank", {a: 271000}),
            _line("1010", "Cash on hand (till)", {a: 110000}),
            _line("1100", "Accounts receivable", {a: 60000}),
            _line("total_assets", "Total assets", {a: 441000}, 3),
            _line("2000", "Accounts payable", {a: 80000}),
            _line("total_liabilities", "Total liabilities", {a: 80000}, 3),
            _line("3000", "Owner's capital", {a: 200000}),
            _line("profit", "Profit for the period", {a: 161000}),
            _line("total_equity", "Total equity", {a: 361000}, 3),
        ]
        return BalanceSheetDTO(
            framework=Framework.IFRS_SME,
            as_of=as_of,
            lines=lines,
            total_assets_cents=441000,
            total_liabilities_cents=80000,
            total_equity_cents=361000,
        )

    def reconcile(
        self, bank_txns: list[BankTxnOut], bank_balance_cents: int, as_of: date
    ) -> ReconciliationDTO:
        matched = [
            ReconMatch(
                bank_txn_id=t.id,
                journal_entry_id=f"je_{t.id}",
                amount_cents=t.amount_cents,
                date_diff_days=0,
                score=Decimal("1"),
            )
            for t in bank_txns
            if t.kind != "opening"
        ]
        return ReconciliationDTO(
            as_of=as_of,
            matched=matched,
            bank_only=[],
            books_only=[],
            duplicates=[],
            ledger_cash_cents=bank_balance_cents,
            bank_balance_cents=bank_balance_cents,
            difference_cents=0,
            reconciling_items=[],
            is_reconciled=True,
        )

    def receivables_ageing(self, as_of: date) -> ReceivablesAgeingDTO:
        rows, buckets = [], dict.fromkeys(AGEING_BUCKETS, 0)
        for inv in self.open_invoices():
            days = inv.days_overdue(as_of)
            bucket = (
                "current"
                if days == 0
                else "1-30"
                if days <= 30
                else "31-60"
                if days <= 60
                else "61-90"
                if days <= 90
                else "90+"
            )
            buckets[bucket] += inv.outstanding_cents
            rows.append(
                AgeingRow(
                    invoice_id=inv.id,
                    customer_name=self._cps[inv.customer_id].name,
                    outstanding_cents=inv.outstanding_cents,
                    days_overdue=days,
                    bucket=bucket,
                )
            )
        return ReceivablesAgeingDTO(
            as_of=as_of, buckets_cents=buckets, rows=rows, ecl_rates={}, ecl_allowance_cents=0
        )

    def open_invoices(self) -> list[InvoiceDTO]:
        return [
            i
            for i in self._invoices.values()
            if i.status in (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)
        ]

    def open_bills(self) -> list[BillDTO]:
        return [
            b for b in self._bills.values() if b.status in (BillStatus.OPEN, BillStatus.PARTIAL)
        ]

    def counterparties(self) -> list[CounterpartyDTO]:
        return list(self._cps.values())

    def payees(self) -> list[PayeeDTO]:
        return list(self._payees.values())

    def weekly_sales(self, weeks: int, as_of: date) -> list[WeeklySales]:
        out = []
        for j in range(weeks, 0, -1):
            start, end = as_of - timedelta(days=7 * j - 1), as_of - timedelta(days=7 * (j - 1))
            total = sum(
                s.amount_cents for s in self.bundle.pos_sales if start <= s.ts.date() <= end
            )
            cash = sum(
                s.amount_cents
                for s in self.bundle.pos_sales
                if start <= s.ts.date() <= end and s.tender == "cash"
            )
            out.append(
                WeeklySales(
                    week_start=start,
                    cash_cents=cash,
                    card_sales_cents=total - cash,
                    total_cents=total,
                )
            )
        return out

    def cogs_ratio(self, weeks: int, as_of: date) -> Decimal:
        return Decimal("0.3000")

    def recurring_obligations(self) -> list[RecurringObligation]:
        return list(self.bundle.recurring)

    def cash_balance(self, as_of: date) -> int:
        return 381000

    def readiness_inputs(self, as_of: date) -> ReadinessInputs:
        from contracts.testing.builders import make_readiness_inputs

        return make_readiness_inputs(as_of=as_of)

    def close_period(self, month: str, principal: Principal) -> None:
        self._closed.add(month)

    def payee(self, payee_id: str) -> PayeeDTO | None:
        return self._payees.get(payee_id)

    def bill(self, bill_id: str) -> BillDTO | None:
        return self._bills.get(bill_id)

    def invoice(self, invoice_id: str) -> InvoiceDTO | None:
        return self._invoices.get(invoice_id)

    def counterparty(self, cp_id: str) -> CounterpartyDTO | None:
        return self._cps.get(cp_id)

    def period_is_closed(self, d: date) -> bool:
        return month_key(d) in self._closed

    def reconciliation_open_items(self) -> int:
        return 0


# --- planning fakes ---------------------------------------------------------------------------


def _percentile(values: Sequence[int], p: int) -> Decimal:
    """Linear interpolation percentile (numpy default) on Decimal."""
    xs = sorted(Decimal(v) for v in values)
    if not xs:
        return Decimal(0)
    h = Decimal(len(xs) - 1) * Decimal(p) / Decimal(100)
    lo = int(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def _roundup_100(cents: int) -> int:
    return 0 if cents <= 0 else math.ceil(cents / 10000) * 10000


def _recurring_dates(r: RecurringObligation, start: date, end: date) -> list[date]:
    out: list[date] = []
    if r.schedule == "monthly" and r.day_of_month:
        y, m = start.year, start.month
        while date(y, m, 1) <= end:
            nxt = date(y + (m == 12), m % 12 + 1, 1)
            day = min(r.day_of_month, (nxt - timedelta(days=1)).day)
            d = date(y, m, day)
            if start <= d <= end and d >= r.first_date and (r.end_date is None or d <= r.end_date):
                out.append(d)
            y, m = nxt.year, nxt.month
    elif r.schedule == "every_n_days" and r.every_days:
        d = r.first_date
        while d <= end:
            if d >= start and (r.end_date is None or d <= r.end_date):
                out.append(d)
            d += timedelta(days=r.every_days)
    return out


_RECURRING_BUCKET = {
    "6000": "rent",
    "6100": "wages",
    "6200": "utilities",
    "2500": "loan",
    "7000": "loan",
}


class FakeForecast:
    """Simplified 13-week engine (no events, no >60-day rule). Reproduces the tiny-fixture
    golden tables of PRD/15 §3.1. WP4 builds the real one."""

    def _collection_date(self, inv: InvoiceForecastItem, scenario: Scenario, as_of: date) -> date:
        hist = [max(0, d) for d in inv.lateness_history_days]
        overdue = inv.due_date < as_of
        if scenario == Scenario.HIGH:
            return as_of + timedelta(days=3) if overdue else inv.due_date
        p = 75 if scenario == Scenario.LOW else 50
        late = int(_percentile(hist, p)) if hist else (30 if scenario == Scenario.LOW else 14)
        if overdue:
            floor = as_of + timedelta(days=28 if scenario == Scenario.LOW else 14)
            return max(floor, inv.due_date + timedelta(days=late))
        return inv.due_date + timedelta(days=late)

    def _scenario(self, inputs: ForecastInputs, scenario: Scenario) -> ScenarioForecast:
        totals = [w.total_cents for w in inputs.weekly_sales]
        sales = {
            Scenario.LOW: _percentile(totals[-12:], 25),
            Scenario.BASE: _percentile(totals[-8:], 50),
            Scenario.HIGH: _percentile(totals[-12:], 75),
        }[scenario]
        weeks: list[WeekRow] = []
        opening = inputs.opening_cash_cents
        for k in range(1, 14):
            start, end = week_range(k, inputs.as_of)
            in_sales = to_cents(from_cents(int(sales)))
            cogs = to_cents(sales * inputs.cogs_ratio / 100)
            coll = sum(
                i.outstanding_cents
                for i in inputs.open_invoices
                if week_of(self._collection_date(i, scenario, inputs.as_of), inputs.as_of) == k
            )
            bills = sum(
                b.outstanding_cents
                for b in inputs.open_bills
                if (week_of(b.due_date, inputs.as_of) or (1 if b.due_date <= inputs.as_of else 0))
                == k
            )
            rec = dict.fromkeys(("rent", "wages", "utilities", "loan", "other"), 0)
            for r in inputs.recurring:
                rec[_RECURRING_BUCKET.get(r.category, "other")] += r.amount_cents * len(
                    _recurring_dates(r, start, end)
                )
            net = in_sales + coll - cogs - bills - sum(rec.values())
            weeks.append(
                WeekRow(
                    week=k,
                    start=start,
                    end=end,
                    opening_cents=opening,
                    in_sales_cents=in_sales,
                    in_collections_cents=coll,
                    out_cogs_cents=cogs,
                    out_bills_cents=bills,
                    out_rent_cents=rec["rent"],
                    out_wages_cents=rec["wages"],
                    out_utilities_cents=rec["utilities"],
                    out_loan_cents=rec["loan"],
                    out_other_cents=rec["other"],
                    net_cents=net,
                    closing_cents=opening + net,
                )
            )
            opening += net
        neg = next((w.week for w in weeks if w.closing_cents < 0), None)
        low = min(weeks, key=lambda w: (w.closing_cents, w.week))
        return ScenarioForecast(
            scenario=scenario,
            weeks=weeks,
            first_negative_week=neg,
            min_closing_cents=low.closing_cents,
            min_closing_week=low.week,
            assumptions=[
                f"Weekly sales {fmt(int(sales))} ({scenario.value.lower()} case)",
                f"Cash COGS {inputs.cogs_ratio} of sales",
            ],
        )

    def forecast(self, inputs: ForecastInputs) -> ForecastDTO:
        sc = {s: self._scenario(inputs, s) for s in Scenario}
        base, low = sc[Scenario.BASE], sc[Scenario.LOW]
        fixed = sum(
            w.out_rent_cents + w.out_wages_cents + w.out_utilities_cents for w in base.weeks
        )
        buffer = (
            inputs.safety_buffer_cents
            if inputs.safety_buffer_cents is not None
            else to_cents(from_cents(fixed) / 13)
        )
        recommended = (
            _roundup_100(-low.min_closing_cents + buffer) if low.min_closing_cents < 0 else 0
        )
        base_gap = _roundup_100(-base.min_closing_cents)
        term, recovered = 91, True
        if base.first_negative_week is not None:
            rec_week = next(
                (
                    w
                    for w in base.weeks
                    if w.week > base.first_negative_week and w.closing_cents >= 0
                ),
                None,
            )
            recovered = rec_week is not None
            if rec_week is not None:
                term = min(91, max(14, (rec_week.end - inputs.as_of).days))
        else:
            term = 14
        crossing = base.first_negative_week
        drivers = DriversDTO(
            scenario=Scenario.BASE,
            crossing_week=crossing,
            top_outflows=[],
            delayed_inflows=[],
            facts={}
            if crossing is None
            else {
                "week": str(crossing),
                "week_start": base.weeks[crossing - 1].start.strftime("%d %b %Y"),
                "main_outflow": "supplier bills",
                "main_outflow_amount": fmt(base.weeks[crossing - 1].out_bills_cents),
                "delayed_count": str(len(inputs.open_invoices)),
                "delayed_total": fmt(sum(i.outstanding_cents for i in inputs.open_invoices)),
            },
        )
        return ForecastDTO(
            as_of=inputs.as_of,
            scenarios=sc,
            drivers=drivers,
            recommended_gap_cents=recommended,
            base_gap_cents=base_gap,
            needed_term_days=term,
            going_concern_flag=(low.weeks[-1].closing_cents < 0) or not recovered,
            inputs_hash=params_hash(inputs),
        )


class FakeCashPlan:
    """COLLECT only (overdue invoices paid at AS_OF + 5), then BORROW for any remainder."""

    def plan(
        self, inputs: ForecastInputs, forecast: ForecastDTO, avg_daily_card_sales_cents: int
    ) -> CashPlanDTO:
        g0 = forecast.base_gap_cents
        if g0 == 0:
            return CashPlanDTO(
                as_of=inputs.as_of,
                initial_gap_cents=0,
                steps=[],
                drafts=[],
                financing_request=None,
                remaining_gap_cents=0,
            )
        overdue = [i for i in inputs.open_invoices if i.due_date < inputs.as_of]
        moved = inputs.model_copy(
            update={
                "open_invoices": [
                    i.model_copy(
                        update={
                            "due_date": inputs.as_of + timedelta(days=5),
                            "lateness_history_days": [0],
                        }
                    )
                    if i in overdue
                    else i
                    for i in inputs.open_invoices
                ]
            }
        )
        g1 = FakeForecast().forecast(moved).base_gap_cents
        steps = [
            CashPlanStep(
                lever=Lever.COLLECT,
                gap_before_cents=g0,
                gap_after_cents=g1,
                closed_cents=g0 - g1,
                item_refs=[i.invoice_id for i in overdue],
                explanation_facts={"closed": fmt(g0 - g1), "remaining": fmt(g1)},
            )
        ]
        drafts = [
            ProposedDraft(
                action_type=ActionType.DRAFT_REMINDER,
                params={"invoice_id": i.invoice_id},
                reason="Overdue invoice",
            )
            for i in overdue
        ]
        request = None
        if g1 > 0:
            steps.append(
                CashPlanStep(
                    lever=Lever.BORROW,
                    gap_before_cents=g1,
                    gap_after_cents=g1,
                    closed_cents=0,
                    item_refs=[],
                    explanation_facts={"remaining": fmt(g1)},
                )
            )
            request = FinancingRequest(
                amount_cents=g1,
                term_days=forecast.needed_term_days,
                as_of=inputs.as_of,
                avg_daily_card_sales_cents=avg_daily_card_sales_cents,
                eligible_invoices=[],
                supplier_terms_available=True,
            )
        return CashPlanDTO(
            as_of=inputs.as_of,
            initial_gap_cents=g0,
            steps=steps,
            drafts=drafts,
            financing_request=request,
            remaining_gap_cents=g1,
        )


# --- financing fakes ---------------------------------------------------------------------------


def _disclosure(
    received: int, cost: int, apr: str | None, ear: str | None, n: int, freq: str, term: int
) -> DisclosureDTO:
    return DisclosureDTO(
        funding_provided_cents=received,
        finance_charge_cents=cost,
        total_repayment_cents=received + cost,
        estimated_apr=Decimal(apr) if apr else None,
        ear=Decimal(ear) if ear else None,
        payment_amount_cents=(received + cost) // max(n, 1),
        payment_frequency=freq,
        number_of_payments=n,
        term_days=term,
        prepayment_note="Not specified in the illustrative catalogue",
        collateral_note="None stated",
        regimes=["REG-LND-01", "REG-LND-02", "REG-LND-03", "REG-LND-04"],
    )


class FakeFinancing:
    """Two canned options per request: supplier net 30 (EAR 0%) and an MCA at the PRD/07
    golden rates (APR 151.19%, EAR 352.12%), scaled to the requested amount."""

    def catalogue(self) -> CatalogueDTO:
        return CatalogueDTO(
            as_of=date(2026, 10, 14),
            source="Fake catalogue for tests. Not real offers.",
            currency="USD",
            products=[
                ProductDTO(
                    product_id="sup_goldenmills_net30",
                    kind=ProductKind.SUPPLIER_CREDIT,
                    name="Golden Mills 30-day trade credit",
                    provider="Golden Mills (supplier)",
                    params={"net_days": "30"},
                    min_amount_cents=10000,
                    max_amount_cents=800000,
                    min_term_days=1,
                    max_term_days=30,
                ),
                ProductDTO(
                    product_id="mca_swiftadvance",
                    kind=ProductKind.MERCHANT_CASH_ADVANCE,
                    name="Merchant cash advance",
                    provider="SwiftAdvance (fictional)",
                    params={"factor": "1.20", "holdback_rate": "0.15"},
                    min_amount_cents=50000,
                    max_amount_cents=5000000,
                    min_term_days=1,
                    max_term_days=540,
                ),
            ],
        )

    def compare(self, request: FinancingRequest) -> FinancingComparisonDTO:
        a = request.amount_cents
        mca_cost = to_cents(from_cents(a) * Decimal("0.20"))
        options = [
            FinancingOption(
                product_id="sup_goldenmills_net30",
                kind=ProductKind.SUPPLIER_CREDIT,
                name="Golden Mills 30-day trade credit",
                provider="Golden Mills (supplier)",
                eligible=True,
                ineligibility_reasons=[],
                flows=[
                    CashFlowPoint(day=0, amount_cents=a),
                    CashFlowPoint(day=30, amount_cents=-a),
                ],
                amount_received_cents=a,
                total_repaid_cents=a,
                total_cost_cents=0,
                daily_irr=Decimal("0"),
                nominal_apr=Decimal("0"),
                ear=Decimal("0"),
                simple_annualised=Decimal("0"),
                term_days=30,
                disclosure=_disclosure(a, 0, "0", "0", 1, "once", 30),
                flags=[],
                rank=1,
            ),
            FinancingOption(
                product_id="mca_swiftadvance",
                kind=ProductKind.MERCHANT_CASH_ADVANCE,
                name="Merchant cash advance",
                provider="SwiftAdvance (fictional)",
                eligible=True,
                ineligibility_reasons=[],
                flows=[
                    CashFlowPoint(day=0, amount_cents=a),
                    CashFlowPoint(day=90, amount_cents=-(a + mca_cost)),
                ],
                amount_received_cents=a,
                total_repaid_cents=a + mca_cost,
                total_cost_cents=mca_cost,
                daily_irr=Decimal("0.0041422"),
                nominal_apr=Decimal("1.5119"),
                ear=Decimal("3.5212"),
                simple_annualised=Decimal("0.8111"),
                term_days=90,
                disclosure=_disclosure(a, mca_cost, "1.5119", "3.5212", 90, "daily", 90),
                flags=["HIGH_COST", "FACTOR_RATE_PRODUCT"],
                rank=2,
            ),
        ]
        return FinancingComparisonDTO(
            request=request,
            options=options,
            cheapest_id="sup_goldenmills_net30",
            most_expensive_id="mca_swiftadvance",
            catalogue_as_of=date(2026, 10, 14),
            catalogue_source="Fake catalogue for tests. Not real offers.",
            notices=list(FINANCING_NOTICES),
        )


class FakeReadiness:
    def score(self, inputs: ReadinessInputs) -> ReadinessDTO:
        comps = [
            ReadinessComponent(
                key="history",
                label="Record history",
                weight=15,
                points=Decimal("15"),
                metric=f"{inputs.months_of_records} months",
                rule="0 -> 0, 6+ -> 15",
            ),
            ReadinessComponent(
                key="bookkeeping",
                label="Bookkeeping quality",
                weight=20,
                points=Decimal("18.5"),
                metric="95% categorised, 90% reconciled",
                rule="10 x categorised + 10 x reconciled",
            ),
            ReadinessComponent(
                key="separation",
                label="Personal vs business",
                weight=15,
                points=Decimal("13"),
                metric=f"{inputs.unconfirmed_count} unconfirmed",
                rule="15 x share - 1 per item",
            ),
            ReadinessComponent(
                key="stability",
                label="Sales stability",
                weight=15,
                points=Decimal("10"),
                metric="CV 0.25",
                rule="<= 0.10 -> 15, >= 0.60 -> 0",
            ),
            ReadinessComponent(
                key="cash_health",
                label="Cash health",
                weight=15,
                points=Decimal("2.5"),
                metric="10 of 12 weeks positive",
                rule="share of positive weeks",
            ),
            ReadinessComponent(
                key="receivables",
                label="Receivables",
                weight=10,
                points=Decimal("2"),
                metric="overdue share 100%",
                rule="see PRD/07 §7",
            ),
            ReadinessComponent(
                key="debt_service",
                label="Debt service",
                weight=10,
                points=Decimal("10"),
                metric="no debt",
                rule="no debt -> 10",
            ),
        ]
        return ReadinessDTO(
            score=71,
            band=ReadinessBand.FAIR,
            components=comps,
            improvements=[
                Improvement(
                    text="Collect 2 overdue invoices",
                    points_gain=Decimal("6.0"),
                    hint_action="DRAFT_REMINDER",
                ),
                Improvement(
                    text=f"Confirm {inputs.unconfirmed_count} pending items",
                    points_gain=Decimal("2.0"),
                    hint_action=None,
                ),
            ],
            dscr=None,
            disclaimer=READINESS_DISCLAIMER,
        )


# --- outputs fake -----------------------------------------------------------------------------

_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>"
    b"endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)


class FakeOutputs:
    """Template drafts and a minimal PDF; stores everything in memory."""

    def __init__(self, out_dir: Path | None = None, business_name: str = "Tiny Bakery"):
        self.out_dir = (
            Path(out_dir) if out_dir else Path(tempfile.gettempdir()) / "loanready_fake_packs"
        )
        self.business_name = business_name
        self._drafts: dict[str, MessageDraftDTO] = {}
        self._packs: dict[str, LoanPackDTO] = {}
        self.submissions: list[tuple[str, str, str]] = []

    def reminder_facts(
        self, invoice: InvoiceDTO, customer: CounterpartyDTO, as_of: date
    ) -> ReminderFacts:
        days = invoice.days_overdue(as_of)
        if days < 1:
            from contracts.errors import InvalidParams

            raise InvalidParams("invoice is not overdue", reason_code=ReasonCode.NOT_OVERDUE)
        tone = Tone.FRIENDLY if days <= 7 else Tone.FIRM if days <= 21 else Tone.FINAL
        to_ref = customer.contact_ref or ""
        return ReminderFacts(
            invoice_id=invoice.id,
            invoice_number=invoice.number,
            customer_name=customer.name,
            to_ref=to_ref,
            channel=Channel.SMS if to_ref.startswith("+") else Channel.EMAIL,
            amount_outstanding_cents=invoice.outstanding_cents,
            amount_str=fmt(invoice.outstanding_cents),
            currency="USD",
            due_date=invoice.due_date,
            due_date_str=invoice.due_date.strftime("%d %b %Y"),
            days_overdue=days,
            tone=tone,
            business_name=self.business_name,
        )

    def _store(
        self,
        kind: DraftKind,
        channel: Channel,
        to_ref: str,
        subject: str,
        body: str,
        by: GeneratedBy,
        facts: Any,
        related_id: str,
    ) -> MessageDraftDTO:
        d = MessageDraftDTO(
            draft_id=get_id_factory().new("drf"),
            kind=kind,
            channel=channel,
            to_ref=to_ref,
            subject=subject,
            body=body,
            body_sha256=sha256_hex(body),
            facts_hash=params_hash(facts),
            generated_by=by,
            ai_label=AI_LABEL_TEMPLATE,
            related_id=related_id,
            created_at=get_clock().now(),
            status="draft",
            sbx_ref=None,
        )
        self._drafts[d.draft_id] = d
        return d

    def draft_reminder(self, facts: ReminderFacts, wording: Any) -> MessageDraftDTO:
        subject, body, by = wording.word_reminder(facts)
        return self._store(
            DraftKind.REMINDER,
            facts.channel,
            facts.to_ref,
            subject,
            body,
            by,
            facts,
            facts.invoice_id,
        )

    def delay_facts(self, bill: BillDTO, supplier: CounterpartyDTO, proposed: date) -> DelayFacts:
        to_ref = supplier.contact_ref or ""
        return DelayFacts(
            bill_id=bill.id,
            bill_number=bill.number,
            supplier_name=supplier.name,
            to_ref=to_ref,
            channel=Channel.SMS if to_ref.startswith("+") else Channel.EMAIL,
            amount_cents=bill.outstanding_cents,
            amount_str=fmt(bill.outstanding_cents),
            due_date=bill.due_date,
            proposed_date=proposed,
            due_date_str=bill.due_date.strftime("%d %b %Y"),
            proposed_date_str=proposed.strftime("%d %b %Y"),
            business_name=self.business_name,
        )

    def draft_delay_request(self, facts: DelayFacts, wording: Any) -> MessageDraftDTO:
        subject, body, by = wording.word_delay_request(facts)
        return self._store(
            DraftKind.DELAY_REQUEST,
            facts.channel,
            facts.to_ref,
            subject,
            body,
            by,
            facts,
            facts.bill_id,
        )

    def draft(self, draft_id: str) -> MessageDraftDTO | None:
        return self._drafts.get(draft_id)

    def drafts(self) -> list[MessageDraftDTO]:
        return list(self._drafts.values())

    def mark_sent(self, draft_id: str, sbx_ref: str) -> None:
        self._drafts[draft_id] = self._drafts[draft_id].model_copy(
            update={"status": "sent", "sbx_ref": sbx_ref}
        )

    def build_loan_pack(self, content: LoanPackContent) -> LoanPackDTO:
        pack_id = get_id_factory().new("pck")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"loan_pack_{content.as_of.isoformat()}_{pack_id}.pdf"
        path.write_bytes(_MINIMAL_PDF)
        sections = [
            "cover",
            "summary",
            "pnl",
            "cash_flow",
            "balance_sheet",
            "forecast",
            "reconciliation",
            "ageing",
            "sales_trend",
            "readiness",
            "financing",
            "policies",
            "controls",
        ]
        pack = LoanPackDTO(
            pack_id=pack_id,
            path=str(path),
            sha256=file_sha256(path),
            sections=sections,
            created_at=get_clock().now(),
            metadata={
                "business": content.business_name,
                "as_of": content.as_of.isoformat(),
                "requested_amount_cents": content.requested_amount_cents,
                "framework": content.framework.value,
                "sections": sections,
            },
        )
        self._packs[pack_id] = pack
        return pack

    def loan_pack(self, pack_id: str) -> LoanPackDTO | None:
        return self._packs.get(pack_id)

    def verify_pack(self, pack_id: str) -> bool:
        pack = self._packs.get(pack_id)
        return (
            bool(pack) and Path(pack.path).exists() and file_sha256(Path(pack.path)) == pack.sha256
        )

    def record_submission(self, pack_id: str, lender_id: str, receipt_id: str) -> None:
        self.submissions.append((pack_id, lender_id, receipt_id))


# --- app facade fake ---------------------------------------------------------------------------


class _FakeAppExecutor:
    """Executes approved fake proposals against the fake ports."""

    def __init__(self, app: FakeAppFacade):
        self.app = app

    def execute(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        p = action.params
        if action.type == ActionType.SCHEDULE_SUPPLIER_PAYMENT:
            out = self.app.ports.bank.schedule_payment(
                ticket,
                PaymentScheduleIn(
                    from_account_id=p["from_account_id"],
                    payee_id=p["payee_id"],
                    bank_account_ref=p["bank_account_ref"],
                    amount_cents=p["amount_cents"],
                    value_date=p["value_date"],
                    reference=f"{p['bill_id']} via LoanReady",
                ),
            )
            if out.status == "settled":
                self.app.books.record_bill_payment(
                    p["bill_id"], p["amount_cents"], out.value_date, out.sbx_ref
                )
            return ExecutionResult(
                action_id=action.action_id,
                ok=out.status != "failed",
                sbx_ref=out.sbx_ref,
                result={"status": out.status},
            )
        if action.type in (ActionType.SEND_REMINDER, ActionType.SEND_DELAY_REQUEST):
            draft = self.app.outputs.draft(p["draft_id"])
            assert draft is not None
            msg = self.app.ports.messaging.send(
                ticket,
                MessageSendIn(
                    channel=draft.channel.value,
                    to_ref=draft.to_ref,
                    subject=draft.subject,
                    body=draft.body,
                ),
            )
            self.app.outputs.mark_sent(draft.draft_id, msg.sbx_ref)
            return ExecutionResult(
                action_id=action.action_id,
                ok=True,
                sbx_ref=msg.sbx_ref,
                result={"status": msg.status},
            )
        if action.type == ActionType.SUBMIT_LOAN_PACK:
            pack = self.app.outputs.loan_pack(p["pack_id"])
            assert pack is not None
            pdf = Path(pack.path).read_bytes()
            rec = self.app.ports.lender.submit_application(
                ticket,
                LenderApplicationIn(
                    lender_id=p["lender_id"],
                    business_name=self.app.bundle.business["name"],
                    requested_amount_cents=int(pack.metadata.get("requested_amount_cents", 0)),
                    term_days=90,
                    purpose="Working capital",
                    pack_sha256=pack.sha256,
                    sections=pack.sections,
                    pdf_base64=base64.b64encode(pdf).decode("ascii"),
                ),
            )
            self.app.outputs.record_submission(pack.pack_id, p["lender_id"], rec.receipt_id)
            return ExecutionResult(
                action_id=action.action_id,
                ok=True,
                sbx_ref=rec.receipt_id,
                result={"status": rec.status, "missing_sections": rec.missing_sections},
            )
        return ExecutionResult(action_id=action.action_id, ok=True, sbx_ref=None, result={})


class FakeAppFacade:
    """Every AppFacade method with realistic canned DTOs from the tiny fixture.

    ``crunch=True`` (default) adds BILL-2 (5,000.00 due 2026-10-28) so the forecast dips
    in week 2 and the action plan, financing and approval screens all have content.
    """

    def __init__(self, crunch: bool = True, out_dir: Path | None = None):
        self.bundle = load_tiny_bundle()
        self.as_of = self.bundle.as_of
        extra = []
        if crunch:
            extra.append(
                BillDTO(
                    id="bil_tiny_2",
                    number="BILL-2",
                    supplier_id="cpt_tiny_mills",
                    payee_id="pye_tiny_mills",
                    issue_date=date(2026, 10, 10),
                    due_date=date(2026, 10, 28),
                    amount_cents=500000,
                    paid_cents=0,
                    status=BillStatus.OPEN,
                    category="5000",
                    late_penalty=False,
                    grace_days=0,
                    essential=False,
                    claimed_bank_account_ref=None,
                    description="Large flour order",
                )
            )
        self.books = FakeBooks(self.bundle, extra)
        self.outputs = FakeOutputs(out_dir, self.bundle.business["name"])
        self.agent = FakeAgent()
        self.planner = FakeForecast()
        self.cashplanner = FakeCashPlan()
        self.fin = FakeFinancing()
        self.ready = FakeReadiness()
        self.audit = FakeAudit()
        self.gate = FakeGate(executor=_FakeAppExecutor(self), audit=self.audit)
        self.ports = make_fake_ports(self.bundle)
        self._principal = OWNER
        self._tokens: dict[str, str] = {}
        self._version = 0
        self._last_pack: LoanPackDTO | None = None

    # helpers
    def _bump(self) -> None:
        self._version += 1

    def _action(self, type_: ActionType, params: dict[str, Any], reason: str) -> Action:
        from contracts.actions import AGENT
        from contracts.enums import Origin

        return Action(
            action_id=get_id_factory().new("act"),
            type=type_,
            params=params,
            origin=Origin.USER_REQUEST,
            provenance=[],
            reason=reason,
            requested_by=AGENT,
            on_behalf_of=self._principal,
            requested_at=get_clock().now(),
        )

    def _inputs(self) -> ForecastInputs:
        return ForecastInputs(
            as_of=self.as_of,
            opening_cash_cents=self.books.cash_balance(self.as_of),
            weekly_sales=self.books.weekly_sales(3, self.as_of),
            cogs_ratio=self.books.cogs_ratio(3, self.as_of),
            open_invoices=[
                InvoiceForecastItem(
                    invoice_id=i.id,
                    customer_id=i.customer_id,
                    due_date=i.due_date,
                    outstanding_cents=i.outstanding_cents,
                    lateness_history_days=self.books.counterparty(
                        i.customer_id
                    ).lateness_history_days,
                )
                for i in self.books.open_invoices()
            ],
            open_bills=[
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
                for b in self.books.open_bills()
            ],
            recurring=self.books.recurring_obligations(),
            events=[],
        )

    # AppFacade
    def health(self) -> dict[str, Any]:
        return {
            "sandbox": self.ports.admin.health(),
            "packages": {"all": "fake"},
            "chain_ok": self.audit.verify_chain() is None,
            "ai": self.agent.status(),
            "fake": True,
        }

    def dashboard(self) -> DashboardDTO:
        f = self.forecast()
        return DashboardDTO(
            as_of=self.as_of,
            cash_cents=self.books.cash_balance(self.as_of),
            first_negative_week_base=f.scenarios[Scenario.BASE].first_negative_week,
            recommended_gap_cents=f.recommended_gap_cents,
            readiness_score=self.readiness().score,
            pending_approvals=len(self.pending()),
            confirm_queue=len(self.confirm_queue()),
            blocked_count=self.audit.counts()["BLOCKED"],
            chain_ok=self.verify_chain() is None,
            kill_switch=self.gate.kill_switch,
        )

    def sync_all(self) -> dict[str, Any]:
        flags = 0
        for t in self.bundle.bank_transactions:
            flags += len(scan_untrusted(as_untrusted(t.description, SourceKind.BANK, t.id)))
        self._bump()
        return {
            "bank": len(self.bundle.bank_transactions),
            "pos": len(self.bundle.pos_sales),
            "flags": flags,
            "posted": 0,
            "confirm_queue": len(self.confirm_queue()),
        }

    def capture_receipt(self, path: Path) -> ReceiptDTO:
        self._bump()
        return self.books.ingest_receipt(path)

    def confirm_queue(self) -> list[ConfirmItemDTO]:
        return [
            ConfirmItemDTO(
                id="cfm_fake_1",
                kind=ConfirmKind.PERSONAL,
                ref_id="rtx_fake_1",
                question="Is 'FRESHMART SUPERSTORE $86.40' a personal expense?",
                proposed="PERSONAL",
                options=["PERSONAL", "5000", "6900"],
                confidence=Decimal("0.70"),
                created_at=datetime(2026, 10, 14, 3, 0, tzinfo=UTC),
            )
        ]

    def resolve(self, item_id: str, choice: str) -> None:
        self.audit.log_event(
            EventType.ITEM_CONFIRMED,
            {"item_id": item_id, "choice": choice},
            self._principal,
            ["CTL-FR-06"],
        )
        self._bump()

    def books_view(self, start: date | None = None, end: date | None = None) -> dict[str, Any]:
        s, e = start or self.bundle.history_start, end or (self.as_of - timedelta(days=1))
        bank = self.ports.bank
        txns = bank.list_transactions(OPERATING_ACCOUNT_ID, s, e)
        return {
            "journal": self.books.journal(s, e),
            "pnl": self.books.profit_and_loss(s, e),
            "cash_flow": self.books.cash_flow_statement(s, e),
            "balance_sheet": self.books.balance_sheet(e),
            "reconciliation": self.books.reconcile(
                txns, bank.get_balance(OPERATING_ACCOUNT_ID), self.as_of
            ),
        }

    def receivables(self) -> ReceivablesAgeingDTO:
        return self.books.receivables_ageing(self.as_of)

    def forecast(self) -> ForecastDTO:
        return self.planner.forecast(self._inputs())

    def forecast_explanation(self) -> str:
        return self.agent.explain_forecast(self.forecast())

    def cash_plan(self) -> CashPlanDTO:
        return self.cashplanner.plan(self._inputs(), self.forecast(), 0)

    def drafts(self) -> list[MessageDraftDTO]:
        return self.outputs.drafts()

    def open_bills(self) -> list[BillDTO]:
        return self.books.open_bills()

    def draft_reminders(self) -> list[MessageDraftDTO]:
        out = []
        for inv in self.books.open_invoices():
            if 1 <= inv.days_overdue(self.as_of) <= 60:
                self.gate.evaluate(
                    self._action(
                        ActionType.DRAFT_REMINDER, {"invoice_id": inv.id}, "Overdue invoice"
                    )
                )
                facts = self.outputs.reminder_facts(
                    inv, self.books.counterparty(inv.customer_id), self.as_of
                )
                out.append(self.outputs.draft_reminder(facts, self.agent.wording()))
        self._bump()
        return out

    def draft_delay_requests(self) -> list[MessageDraftDTO]:
        return []

    def propose_send(self, draft_id: str) -> GateDecision:
        d = self.outputs.draft(draft_id)
        assert d is not None
        if d.kind == DraftKind.REMINDER:
            inv = self.books.invoice(d.related_id)
            params = {
                "invoice_id": d.related_id,
                "draft_id": d.draft_id,
                "channel": d.channel,
                "to_ref": d.to_ref,
                "amount_cents": inv.outstanding_cents if inv else 1,
                "body_sha256": d.body_sha256,
            }
            t = ActionType.SEND_REMINDER
        else:
            params = {
                "bill_id": d.related_id,
                "draft_id": d.draft_id,
                "channel": d.channel,
                "to_ref": d.to_ref,
                "proposed_date": self.as_of + timedelta(days=7),
                "body_sha256": d.body_sha256,
            }
            t = ActionType.SEND_DELAY_REQUEST
        self._bump()
        return self.gate.evaluate(
            self._action(t, params, f"Send {d.kind.value.lower()} {d.subject}")
        )

    def propose_supplier_payment(
        self, bill_id: str, value_date: date | None = None
    ) -> GateDecision:
        bill = self.books.bill(bill_id)
        assert bill is not None and bill.payee_id is not None
        payee = self.books.payee(bill.payee_id)
        assert payee is not None
        params = {
            "bill_id": bill.id,
            "payee_id": payee.id,
            "amount_cents": bill.outstanding_cents,
            "value_date": value_date or self.as_of,
            "bank_account_ref": payee.bank_account_ref,
            "from_account_id": OPERATING_ACCOUNT_ID,
        }
        self._bump()
        return self.gate.evaluate(
            self._action(
                ActionType.SCHEDULE_SUPPLIER_PAYMENT, params, f"Pay {bill.number} to {payee.name}"
            )
        )

    def pending(self) -> list[Proposal]:
        return self.gate.pending()

    def approve(self, proposal_id: str) -> str:
        token = self.gate.approve(proposal_id, self._principal)
        self._tokens[proposal_id] = token.token_id
        self._bump()
        return token.token_id

    def reject(self, proposal_id: str, note: str = "") -> GateDecision:
        self._bump()
        return self.gate.reject(proposal_id, self._principal, note)

    def execute(self, proposal_id: str) -> GateDecision:
        self._bump()
        return self.gate.execute(proposal_id, self._tokens.pop(proposal_id, None), self._principal)

    def approve_and_execute(self, proposal_id: str) -> GateDecision:
        self.approve(proposal_id)
        return self.execute(proposal_id)

    def cancel_payment(self, sbx_ref: str) -> GateDecision:
        self._bump()
        return self.gate.evaluate(
            self._action(
                ActionType.CANCEL_PAYMENT,
                {"sbx_ref": sbx_ref, "original_action_id": "act_unknown"},
                "Cancel payment",
            )
        )

    def financing(
        self, amount_cents: int | None = None, term_days: int | None = None
    ) -> FinancingComparisonDTO:
        plan = self.cash_plan()
        req = plan.financing_request or FinancingRequest(
            amount_cents=self.forecast().recommended_gap_cents or 100000,
            term_days=30,
            as_of=self.as_of,
            avg_daily_card_sales_cents=0,
            eligible_invoices=[
                EligibleInvoice(
                    invoice_id="inv_tiny_1", outstanding_cents=60000, expected_days_to_pay=15
                )
            ],
            supplier_terms_available=True,
        )
        req = req.model_copy(
            update={
                k: v
                for k, v in (("amount_cents", amount_cents), ("term_days", term_days))
                if v is not None
            }
        )
        return self.fin.compare(req)

    def financing_explanation(self) -> str:
        return self.agent.explain_financing(self.financing())

    def readiness(self) -> ReadinessDTO:
        return self.ready.score(self.books.readiness_inputs(self.as_of))

    def build_loan_pack(self, amount_cents: int, term_days: int, purpose: str) -> LoanPackDTO:
        self.gate.evaluate(
            self._action(
                ActionType.DRAFT_LOAN_PACK,
                {
                    "requested_amount_cents": amount_cents,
                    "term_days": term_days,
                    "purpose": purpose,
                },
                "Build loan pack",
            )
        )
        view = self.books_view()
        content = LoanPackContent(
            business_name=self.bundle.business["name"],
            as_of=self.as_of,
            framework=Framework.IFRS_SME,
            requested_amount_cents=amount_cents,
            term_days=term_days,
            purpose=purpose,
            pnl=view["pnl"],
            cash_flow=view["cash_flow"],
            balance_sheet=view["balance_sheet"],
            forecast=self.forecast(),
            reconciliation=view["reconciliation"],
            ageing=self.receivables(),
            weekly_sales=self.books.weekly_sales(3, self.as_of),
            readiness=self.readiness(),
            financing=self.financing(amount_cents, term_days),
            going_concern_note=None,
            accounting_policies=["Accrual basis (IFRS for SMEs Section 2)."],
        )
        self._last_pack = self.outputs.build_loan_pack(content)
        self._bump()
        return self._last_pack

    def propose_submit_pack(self, pack_id: str) -> GateDecision:
        pack = self.outputs.loan_pack(pack_id)
        assert pack is not None
        self._bump()
        return self.gate.evaluate(
            self._action(
                ActionType.SUBMIT_LOAN_PACK,
                {"pack_id": pack_id, "pack_sha256": pack.sha256, "lender_id": "lender_demo"},
                "Submit loan pack",
            )
        )

    def chat(self, message: str) -> ChatReply:
        r = self.agent.route(message)
        if r.intent == Intent.CASH_POSITION:
            cash = fmt(self.books.cash_balance(self.as_of))
            text = f"Cash today is {cash}. {self.forecast_explanation()}"
        elif r.intent == Intent.WHO_OWES_ME:
            text = (
                "; ".join(
                    f"{row.customer_name} owes {fmt(row.outstanding_cents)}"
                    for row in self.receivables().rows
                )
                or "Nobody owes you money."
            )
        else:
            text = (
                "I can show your cash position, who owes you, draft reminders, compare "
                "financing and build a loan pack."
            )
        return ChatReply(text=text, ai_label=None, intent=r.intent, attachments=[], proposals=[])

    def try_blocked(self, action_type: ActionType, params: dict[str, Any]) -> GateDecision:
        self._bump()
        return self.gate.evaluate(
            self._action(action_type, params, "Security test: blocked action")
        )

    def audit_rows(self, limit: int | None = None) -> list[AuditRecord]:
        return self.audit.rows(limit)

    def verify_chain(self) -> int | None:
        return self.audit.verify_chain()

    def export_csv(self, path: Path) -> Path:
        self._bump()
        return self.audit.export_csv(path)

    def audit_counts(self) -> dict[str, int]:
        return self.audit.counts()

    def cap_usage(self) -> CapUsage:
        return self.gate.cap_usage()

    def settings(self) -> dict[str, str]:
        return {
            **self.gate.settings,
            "kill_switch": "true" if self.gate.kill_switch else "false",
            "framework": "IFRS_SME",
            "approval_ttl_seconds": "900",
        }

    def update_setting(self, key: str, value: str) -> None:
        if self._principal.role != Role.OWNER:
            from contracts.errors import PermissionDenied

            raise PermissionDenied("owner only", reason_code=ReasonCode.ROLE_NOT_ALLOWED)
        self.gate.update_setting(key, value, self._principal)
        self._bump()

    def set_kill_switch(self, on: bool) -> None:
        if self._principal.role != Role.OWNER:
            from contracts.errors import PermissionDenied

            raise PermissionDenied("owner only", reason_code=ReasonCode.ROLE_NOT_ALLOWED)
        self.gate.set_kill_switch(on, self._principal)
        self._bump()

    def tier_table(self) -> list[TierRow]:
        return self.gate.tier_table()

    def controls_table(self) -> list[ControlRow]:
        return FakeControls().controls()

    def set_principal(self, role: Role) -> None:
        if role not in (Role.OWNER, Role.BOOKKEEPER):
            raise ValueError("UI principal must be OWNER or BOOKKEEPER")
        self._principal = PRINCIPALS_BY_ROLE[role]
        self._bump()

    def principal(self) -> Principal:
        return self._principal

    def data_version(self) -> int:
        return self._version

    def close_period(self, month: str) -> None:
        self.books.close_period(month, self._principal)
        self.audit.log_event(
            EventType.PERIOD_CLOSED, {"month": month}, self._principal, ["CTL-FR-03"]
        )
        self._bump()


# Names the conformance test checks against the protocols in contracts.services/ports.
FAKES_BY_PROTOCOL: dict[str, Callable[[], Any]] = {
    "BankPort": FakeBankPort,
    "PosPort": FakePosPort,
    "MessagePort": FakeMessagePort,
    "LenderPort": FakeLenderPort,
    "SandboxAdminPort": FakeSandboxAdmin,
    "GateService": FakeGate,
    "AuditLogService": FakeAudit,
    "ControlsRegistry": FakeControls,
    "EntityLookup": FakeEntityLookup,
    "ActionExecutor": FakeExecutor,
    "Classifier": FakeClassifier,
    "BooksService": FakeBooks,
    "ForecastService": FakeForecast,
    "CashPlanService": FakeCashPlan,
    "FinancingService": FakeFinancing,
    "ReadinessService": FakeReadiness,
    "WordingProvider": FakeWording,
    "OutputsService": FakeOutputs,
    "LLMClient": FakeLLM,
    "AgentService": FakeAgent,
    "AppFacade": FakeAppFacade,
}

__all__ = [
    "BOOKKEEPER",
    "FAKES_BY_PROTOCOL",
    "FakeAgent",
    "FakeAppFacade",
    "FakeAudit",
    "FakeBankPort",
    "FakeBooks",
    "FakeCashPlan",
    "FakeClassifier",
    "FakeClock",
    "FakeControls",
    "FakeEntityLookup",
    "FakeExecutor",
    "FakeFinancing",
    "FakeForecast",
    "FakeGate",
    "FakeLLM",
    "FakeLenderPort",
    "FakeMessagePort",
    "FakeOutputs",
    "FakePosPort",
    "FakeReadiness",
    "FakeSandboxAdmin",
    "FakeWording",
    "PARAMS_MODELS",
    "accept_all_verifier",
    "make_fake_ports",
    "make_fake_ticket",
    "make_simple_verifier",
]
