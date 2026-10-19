"""App (PRD/11 §3): the one ``AppFacade`` the UI and scripts call.

Every method acts as ``session.current()``. Reads that the PRD maps to a Tier 0 action
(forecast, statements, financing, readiness, ...) pass through the gate first, so the audit
log shows them as ALLOWED. Drafting is Tier 1; anything that leaves the app is a Tier 2
proposal that only a human can approve and execute. ``data_version()`` increases on every
write so the UI can cache reads (UI-12).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from contracts import CONTRACTS_VERSION
from contracts.actions import (
    AuditRecord,
    GateDecision,
    Principal,
    Proposal,
)
from contracts.dto_app import CapUsage, ChatReply, ControlRow, DashboardDTO, TierRow
from contracts.dto_books import BillDTO, ConfirmItemDTO, ReceiptDTO, ReceivablesAgeingDTO
from contracts.dto_financing import FinancingComparisonDTO, ReadinessDTO
from contracts.dto_outputs import LoanPackDTO, MessageDraftDTO
from contracts.dto_planning import CashPlanDTO, ForecastDTO
from contracts.enums import (
    ActionType,
    Decision,
    DraftKind,
    EventType,
    Framework,
    GeneratedBy,
    LLMTask,
    ReasonCode,
    Role,
    Scenario,
)
from contracts.errors import EntityCheckFailed, InvalidParams, PermissionDenied
from contracts.registry import OPERATING_ACCOUNT_ID
from orchestrator import assemble, sync
from orchestrator.container import Components
from orchestrator.executors import PackRequest
from orchestrator.requests import (
    build_action,
    delay_send_params,
    payment_params,
    reminder_send_params,
    submit_pack_params,
    trusted,
)
from orchestrator.session import Session

REMINDER_MIN_DAYS, REMINDER_MAX_DAYS = 1, 60


class App:
    """``contracts.services.AppFacade`` over real services or their fakes."""

    def __init__(self, c: Components, session: Session | None = None):
        self.c = c
        self.session = session or Session()
        self.books = c.books
        self.outputs = c.outputs
        self.gate = c.gate
        self.audit = c.audit
        self.ports = c.ports
        self.agent = c.agent
        self.sink = c.sink
        self._delay_dates: dict[str, date] = {}

    # -- plumbing ----------------------------------------------------------------------------
    @property
    def as_of(self) -> date:
        return self.c.clock.today()

    def close(self) -> None:
        if self.c.transport is not None:
            self.c.transport.close()
            self.c.transport = None

    def _bump(self) -> None:
        self.session.bump()

    def _evaluate(
        self,
        type_: ActionType,
        params: dict[str, Any],
        reason: str,
        provenance: list[Any] | None = None,
    ) -> GateDecision:
        return self.gate.evaluate(build_action(self.session, type_, params, reason, provenance))

    def _observe(self, type_: ActionType, params: dict[str, Any], reason: str) -> GateDecision:
        """Tier 0/1 gate pass; anything but ALLOWED/DRAFTED stops the call."""
        d = self._evaluate(type_, params, reason)
        if d.decision not in (Decision.ALLOWED, Decision.DRAFTED):
            raise PermissionDenied(d.message, reason_code=d.reason_code)
        return d

    def _require_owner(self) -> Principal:
        p = self.session.current()
        if p.role != Role.OWNER:
            raise PermissionDenied("owner only", reason_code=ReasonCode.ROLE_NOT_ALLOWED)
        return p

    def _gate_settings(self) -> dict[str, str]:
        s = getattr(self.gate, "settings", {})
        return dict(s() if callable(s) else s)

    def _kill_switch_on(self) -> bool:
        if hasattr(self.gate, "kill_switch") and isinstance(self.gate.kill_switch, bool):
            return self.gate.kill_switch
        return self._gate_settings().get("kill_switch", "false") == "true"

    def _chain_ok(self) -> bool:
        return self.audit.verify_chain() is None

    def _final_hash(self) -> str | None:
        last = getattr(self.audit, "last_hash", None)
        if callable(last):
            return str(last())
        rows = self.audit.rows()
        return rows[-1].row_hash if rows else None

    def _framework(self) -> Framework:
        return Framework(self._gate_settings().get("framework") or self.c.settings.framework)

    # -- health and dashboard ----------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            sandbox: dict[str, Any] = dict(self.ports.admin.health())
        except Exception as exc:  # the UI shows the problem instead of crashing
            sandbox = {"status": "down", "error": type(exc).__name__}
        return {
            "sandbox": sandbox,
            "sandbox_mode": self.c.settings.sandbox_mode,
            "packages": dict(self.c.modes),
            "fake": any(m == "fake" for m in self.c.modes.values()),
            "contracts": CONTRACTS_VERSION,
            "chain_ok": self._chain_ok(),
            "kill_switch": self._kill_switch_on(),
            "ai": self.agent.status(),
            "as_of": self.as_of.isoformat(),
        }

    def dashboard(self) -> DashboardDTO:
        self._observe(ActionType.RUN_FORECAST, {"as_of": self.as_of}, "Dashboard figures")
        f = self._forecast()
        return DashboardDTO(
            as_of=self.as_of,
            cash_cents=self.books.cash_balance(self.as_of),
            first_negative_week_base=f.scenarios[Scenario.BASE].first_negative_week,
            recommended_gap_cents=f.recommended_gap_cents,
            readiness_score=self._readiness().score,
            pending_approvals=len(self.gate.pending()),
            confirm_queue=len(self.books.confirm_queue()),
            blocked_count=self.audit.counts().get(Decision.BLOCKED.value, 0),
            chain_ok=self._chain_ok(),
            kill_switch=self._kill_switch_on(),
        )

    # -- books -------------------------------------------------------------------------------
    def sync_all(self) -> dict[str, Any]:
        """Pull POS + bank, ingest, scan untrusted text and log flags, categorise, post."""
        res = sync.pull_and_ingest(self.books, self.ports, self.agent, self.sink, self.as_of)
        self._observe(
            ActionType.CATEGORISE, {"raw_txn_ids": res.raw_ids}, "Categorise new transactions"
        )
        sync.categorise_and_post(self.books, self.agent, self.sink, res)
        self._bump()
        return res.as_dict()

    def capture_receipt(self, path: Path) -> ReceiptDTO:
        receipt = self.books.ingest_receipt(Path(path))
        sync.scan_receipt(self.sink, receipt)
        self._observe(ActionType.CATEGORISE, {"raw_txn_ids": [receipt.id]}, "Categorise receipt")
        self._bump()
        return receipt

    def confirm_queue(self) -> list[ConfirmItemDTO]:
        return self.books.confirm_queue()

    def resolve(self, item_id: str, choice: str) -> None:
        """Human only (BKS-15); logs ITEM_CONFIRMED (CTL-FR-06)."""
        principal = self.session.current()
        self.books.resolve(item_id, choice, principal)
        self.sink.log(EventType.ITEM_CONFIRMED, {"item_id": item_id, "choice": choice}, principal)
        self._bump()

    def books_view(self, start: date | None = None, end: date | None = None) -> dict[str, Any]:
        s = start or assemble.history_start(self.books, self.as_of)
        e = end or (self.as_of - timedelta(days=1))
        self._observe(ActionType.BUILD_STATEMENTS, {"start": s, "end": e}, "Build statements")
        self._observe(ActionType.RECONCILE, {"as_of": self.as_of}, "Reconcile with MockBank")
        bank = self.ports.bank
        txns = bank.list_transactions(OPERATING_ACCOUNT_ID, s, self.as_of)
        return {
            "start": s,
            "end": e,
            "journal": self.books.journal(s, e),
            "pnl": self.books.profit_and_loss(s, e),
            "cash_flow": self.books.cash_flow_statement(s, e),
            "balance_sheet": self.books.balance_sheet(e),
            "reconciliation": self.books.reconcile(
                txns, bank.get_balance(OPERATING_ACCOUNT_ID), self.as_of
            ),
        }

    def receivables(self) -> ReceivablesAgeingDTO:
        self._observe(ActionType.BUILD_STATEMENTS, self._period(), "Receivables ageing")
        return self.books.receivables_ageing(self.as_of)

    def _period(self) -> dict[str, date]:
        return {
            "start": assemble.history_start(self.books, self.as_of),
            "end": self.as_of - timedelta(days=1),
        }

    def close_period(self, month: str) -> None:
        principal = self.session.current()
        self.books.close_period(month, principal)
        self.sink.log(EventType.PERIOD_CLOSED, {"month": month}, principal)
        self._bump()

    # -- forecast and plan ---------------------------------------------------------------------
    def _assembly(self) -> assemble.ForecastAssembly:
        return assemble.forecast_inputs(
            self.books,
            self.as_of,
            assemble.load_events(self.as_of),
            assemble.safety_buffer(self._gate_settings()),
        )

    def _forecast(self) -> ForecastDTO:
        return self.c.forecaster.forecast(self._assembly().inputs)

    def excluded_bills(self) -> list[Any]:
        """Bills left out of the forecast as suspected fraud ("excluded pending verification")."""
        return self._assembly().excluded_bills

    def forecast(self) -> ForecastDTO:
        self._observe(ActionType.RUN_FORECAST, {"as_of": self.as_of}, "13-week forecast")
        return self._forecast()

    def forecast_explanation(self) -> str:
        return self.agent.explain_forecast(self.forecast())

    def _avg_card(self) -> int:
        return assemble.avg_daily_card_sales_cents(self.books, self.as_of)

    def _cash_plan(self) -> CashPlanDTO:
        inputs = self._assembly().inputs
        f = self.c.forecaster.forecast(inputs)
        return self.c.cash_planner.plan(inputs, f, self._avg_card())

    def cash_plan(self) -> CashPlanDTO:
        self._observe(ActionType.PLAN_CASH, {"as_of": self.as_of}, "Cash action plan")
        return self._cash_plan()

    # -- drafts and sends ----------------------------------------------------------------------
    def _open_draft(self, kind: DraftKind, related_id: str) -> MessageDraftDTO | None:
        for d in self.outputs.drafts():
            if d.kind == kind and d.related_id == related_id and d.status == "draft":
                return d
        return None

    def _log_fallback(self, draft: MessageDraftDTO, task: LLMTask) -> None:
        mode = str(self.agent.status().get("mode", "MOCK")).upper()
        if draft.generated_by == GeneratedBy.TEMPLATE and mode not in ("MOCK", "TEMPLATE"):
            self.sink.log(
                EventType.LLM_FALLBACK_USED,
                {"task": task.value, "draft_id": draft.draft_id, "to": "TEMPLATE"},
            )

    def draft_reminders(self) -> list[MessageDraftDTO]:
        """One reminder per invoice 1..60 days overdue (older ones are impaired, S9)."""
        out = []
        for inv in self.books.open_invoices():
            days = inv.days_overdue(self.as_of)
            if not REMINDER_MIN_DAYS <= days <= REMINDER_MAX_DAYS:
                continue
            existing = self._open_draft(DraftKind.REMINDER, inv.id)
            if existing is not None:
                out.append(existing)
                continue
            customer = self.books.counterparty(inv.customer_id)
            if customer is None:
                continue
            self._observe(
                ActionType.DRAFT_REMINDER,
                {"invoice_id": inv.id},
                f"Invoice {inv.number} is {days} days overdue",
            )
            facts = self.outputs.reminder_facts(inv, customer, self.as_of)
            draft = self.outputs.draft_reminder(facts, self.agent.wording())
            self._log_fallback(draft, LLMTask.DRAFT_REMINDER)
            out.append(draft)
        self._bump()
        return out

    def draft_delay_requests(self) -> list[MessageDraftDTO]:
        """One delay request per DELAY draft in the cash plan."""
        out = []
        plan = self._cash_plan()
        for pd in plan.drafts:
            if pd.action_type != ActionType.DRAFT_DELAY_REQUEST:
                continue
            bill_id = str(pd.params["bill_id"])
            proposed = date.fromisoformat(str(pd.params["proposed_date"]))
            existing = self._open_draft(DraftKind.DELAY_REQUEST, bill_id)
            if existing is not None:
                self._delay_dates.setdefault(existing.draft_id, proposed)
                out.append(existing)
                continue
            bill = self.books.bill(bill_id)
            supplier = self.books.counterparty(bill.supplier_id) if bill else None
            if bill is None or supplier is None:
                continue
            self._observe(
                ActionType.DRAFT_DELAY_REQUEST,
                {"bill_id": bill_id, "proposed_date": proposed},
                pd.reason,
            )
            facts = self.outputs.delay_facts(bill, supplier, proposed)
            draft = self.outputs.draft_delay_request(facts, self.agent.wording())
            self._log_fallback(draft, LLMTask.DRAFT_DELAY_REQUEST)
            self._delay_dates[draft.draft_id] = proposed
            out.append(draft)
        self._bump()
        return out

    def _delay_date(self, draft: MessageDraftDTO) -> date:
        if draft.draft_id in self._delay_dates:
            return self._delay_dates[draft.draft_id]
        for pd in self._cash_plan().drafts:
            if str(pd.params.get("bill_id")) == draft.related_id and "proposed_date" in pd.params:
                return date.fromisoformat(str(pd.params["proposed_date"]))
        raise InvalidParams("no proposed date for this delay request; redraft it")

    def drafts(self) -> list[MessageDraftDTO]:
        """Every draft, oldest first, with status draft/sent (contracts 1.1)."""
        return self.outputs.drafts()

    def open_bills(self) -> list[BillDTO]:
        """Open bills for the payment form; the gate still checks every payment (S6)."""
        return self.books.open_bills()

    def propose_send(self, draft_id: str) -> GateDecision:
        draft = self.outputs.draft(draft_id)
        if draft is None:
            raise EntityCheckFailed("draft not found", reason_code=ReasonCode.UNKNOWN_ENTITY)
        if draft.kind == DraftKind.REMINDER:
            inv = self.books.invoice(draft.related_id)
            params = reminder_send_params(draft, inv)
            type_ = ActionType.SEND_REMINDER
            what = f"reminder for invoice {inv.number if inv else draft.related_id}"
        else:
            params = delay_send_params(draft, self._delay_date(draft))
            type_ = ActionType.SEND_DELAY_REQUEST
            bill = self.books.bill(draft.related_id)
            what = f"delay request for bill {bill.number if bill else draft.related_id}"
        prov = trusted(draft.draft_id, *[k for k in params if k != "draft_id"])
        d = self._evaluate(type_, params, f"Send {what}", prov)
        self._bump()
        return d

    # -- payments ------------------------------------------------------------------------------
    def propose_supplier_payment(
        self, bill_id: str, value_date: date | None = None
    ) -> GateDecision:
        """Params from the stored bill and stored payee only (ORC-04); the S6 fraud bill
        reaches the gate and is blocked with ACCOUNT_CHANGED."""
        bill = self.books.bill(bill_id)
        if bill is None:
            raise EntityCheckFailed("bill not found", reason_code=ReasonCode.UNKNOWN_ENTITY)
        payee = self.books.payee(bill.payee_id) if bill.payee_id else None
        params = payment_params(bill, payee, value_date or self.as_of)
        name = payee.name if payee else bill.supplier_id
        prov = trusted(bill.id, "payee_id", "bank_account_ref", "amount_cents", "value_date")
        d = self._evaluate(
            ActionType.SCHEDULE_SUPPLIER_PAYMENT, params, f"Pay {bill.number} to {name}", prov
        )
        self._bump()
        return d

    def _original_payment_action(self, sbx_ref: str) -> str | None:
        for row in reversed(self.audit.rows()):
            if (
                row.sbx_ref == sbx_ref
                and row.decision == Decision.EXECUTED
                and row.action_type == ActionType.SCHEDULE_SUPPLIER_PAYMENT.value
            ):
                return row.action_id
        return None

    def cancel_payment(self, sbx_ref: str) -> GateDecision:
        original = self._original_payment_action(sbx_ref) or "act_unknown"
        d = self._evaluate(
            ActionType.CANCEL_PAYMENT,
            {"sbx_ref": sbx_ref, "original_action_id": original},
            f"Cancel scheduled payment {sbx_ref}",
            trusted(original, "sbx_ref"),
        )
        self._bump()
        return d

    # -- approvals -----------------------------------------------------------------------------
    def pending(self) -> list[Proposal]:
        return self.gate.pending()

    def approve(self, proposal_id: str) -> str:
        token = self.gate.approve(proposal_id, self.session.current())
        self.session.hold_token(proposal_id, token.token_id)
        self._bump()
        return token.token_id

    def reject(self, proposal_id: str, note: str = "") -> GateDecision:
        d = self.gate.reject(proposal_id, self.session.current(), note)
        self._bump()
        return d

    def execute(self, proposal_id: str) -> GateDecision:
        proposal = self.gate.proposal(proposal_id)
        token = self.session.pop_token(proposal_id)
        d = self.gate.execute(proposal_id, token, self.session.current())
        if (
            d.decision == Decision.EXECUTED
            and proposal is not None
            and proposal.action.type == ActionType.SCHEDULE_SUPPLIER_PAYMENT
        ):
            # the books learn about the payment from the bank feed, never from the executor
            # (PRD/11 §4): a same-day payment is settled and posts against the bill now
            self.sync_all()
        self._bump()
        return d

    def approve_and_execute(self, proposal_id: str) -> GateDecision:
        self.approve(proposal_id)
        return self.execute(proposal_id)

    # -- financing and loan pack -----------------------------------------------------------------
    def _financing(
        self, amount_cents: int | None = None, term_days: int | None = None
    ) -> FinancingComparisonDTO:
        inputs = self._assembly().inputs
        f = self.c.forecaster.forecast(inputs)
        avg = self._avg_card()
        plan = self.c.cash_planner.plan(inputs, f, avg)
        req = assemble.financing_request(self.books, plan, f, self.as_of, avg)
        update = {
            k: v
            for k, v in (("amount_cents", amount_cents), ("term_days", term_days))
            if v is not None
        }
        if update:
            req = req.model_copy(update=update)
        self._observe(
            ActionType.COMPARE_FINANCING,
            {"amount_cents": req.amount_cents, "term_days": req.term_days},
            "Compare financing options",
        )
        return self.c.financing.compare(req)

    def financing(
        self, amount_cents: int | None = None, term_days: int | None = None
    ) -> FinancingComparisonDTO:
        return self._financing(amount_cents, term_days)

    def financing_explanation(self) -> str:
        return self.agent.explain_financing(self._financing())

    def _readiness(self) -> ReadinessDTO:
        return self.c.readiness.score(self.books.readiness_inputs(self.as_of))

    def readiness(self) -> ReadinessDTO:
        self._observe(ActionType.SCORE_READINESS, {"as_of": self.as_of}, "Loan readiness")
        return self._readiness()

    def build_loan_pack(self, amount_cents: int, term_days: int, purpose: str) -> LoanPackDTO:
        self._observe(
            ActionType.DRAFT_LOAN_PACK,
            {"requested_amount_cents": amount_cents, "term_days": term_days, "purpose": purpose},
            "Build loan pack",
        )
        content = assemble.loan_pack_content(
            books=self.books,
            bank=self.ports.bank,
            business_name=self.c.business.get("name", "LoanReady business"),
            as_of=self.as_of,
            framework=self._framework(),
            amount_cents=amount_cents,
            term_days=term_days,
            purpose=purpose,
            forecast=self._forecast(),
            readiness=self._readiness(),
            financing=self._financing(amount_cents, term_days),
            audit_final_hash=self._final_hash(),
            audit_chain_ok=self._chain_ok(),
        )
        pack = self.outputs.build_loan_pack(content)
        self.c.executor.pack_requests[pack.pack_id] = PackRequest(amount_cents, term_days, purpose)
        self._bump()
        return pack

    def propose_submit_pack(self, pack_id: str) -> GateDecision:
        pack = self.outputs.loan_pack(pack_id)
        if pack is None:
            raise EntityCheckFailed("loan pack not found", reason_code=ReasonCode.UNKNOWN_ENTITY)
        params = submit_pack_params(pack)
        d = self._evaluate(
            ActionType.SUBMIT_LOAN_PACK,
            params,
            "Submit the loan pack to the pretend lender",
            trusted(pack_id, "lender_id"),
        )
        self._bump()
        return d

    # -- chat ----------------------------------------------------------------------------------
    def chat(self, message: str) -> ChatReply:
        from orchestrator.chat import handle

        reply = handle(self, message)
        self._bump()
        return reply

    # -- security tests ------------------------------------------------------------------------
    def try_blocked(self, action_type: ActionType, params: dict[str, Any]) -> GateDecision:
        d = self._evaluate(
            ActionType(action_type), dict(params), "Security test: attempt a blocked action"
        )
        self._bump()
        return d

    # -- audit ---------------------------------------------------------------------------------
    def audit_rows(self, limit: int | None = None) -> list[AuditRecord]:
        return self.audit.rows(limit)

    def verify_chain(self) -> int | None:
        return self.audit.verify_chain()

    def export_csv(self, path: Path) -> Path:
        out = self.audit.export_csv(Path(path))
        self.sink.log(
            EventType.EXPORT_CREATED,
            {"file": Path(out).name, "chain_ok": self._chain_ok()},
            self.session.current(),
        )
        self._bump()
        return out

    def audit_counts(self) -> dict[str, int]:
        return self.audit.counts()

    # -- settings ------------------------------------------------------------------------------
    def cap_usage(self) -> CapUsage:
        return self.gate.cap_usage()

    def settings(self) -> dict[str, str]:
        out = self._gate_settings()
        out["kill_switch"] = "true" if self._kill_switch_on() else "false"
        return out

    def update_setting(self, key: str, value: str) -> None:
        self.gate.update_setting(key, value, self._require_owner())
        self._bump()

    def set_kill_switch(self, on: bool) -> None:
        self.gate.set_kill_switch(on, self._require_owner())
        self._bump()

    def tier_table(self) -> list[TierRow]:
        return self.gate.tier_table()

    def controls_table(self) -> list[ControlRow]:
        return self.c.controls.controls()

    def set_principal(self, role: Role) -> None:
        self.session.set_role(Role(role))

    def principal(self) -> Principal:
        return self.session.current()

    def data_version(self) -> int:
        return self.session.version
