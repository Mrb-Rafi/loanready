"""The permission gate (PRD/03 §3; CTL-AC-01..07, CTL-SD-01, CTL-AU-01/02, CTL-LOG-02).

``evaluate`` runs these steps strictly in order; the first failure writes one BLOCKED row
and returns:

1. kill switch (blocks everything except Tier 0)       5. params schema
2. blocked set / unknown type (fail closed)             6. entity checks
3. origin and provenance                                7. caps
4. role                                                 8. tier decision

Tier 0 -> ALLOWED, Tier 1 -> DRAFTED, Tier 2 -> proposal PENDING_APPROVAL, Tier 3 (test
type, only when enabled) -> executed within caps, else downgraded to a proposal.
``execute`` re-checks everything, consumes the token before calling the executor, and is
the only place an ExecutionTicket is minted.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import ValidationError

from contracts.actions import (
    INSTRUCTION_FIELDS,
    PARAMS_MODELS,
    Action,
    ApprovalSummary,
    ApprovalToken,
    ExecutionResult,
    GateDecision,
    Principal,
    Proposal,
    mask_account,
    mask_contact,
)
from contracts.canonical import params_hash
from contracts.clock import Clock
from contracts.dto_app import CapUsage, TierRow
from contracts.enums import (
    ActionType,
    Decision,
    Origin,
    PrincipalKind,
    ProposalStatus,
    ReasonCode,
    Tier,
)
from contracts.errors import (
    ApprovalError,
    LoanReadyError,
    PermissionDenied,
)
from contracts.ids import IdFactory
from contracts.ports import TicketVerifier
from contracts.services import ActionExecutor, EntityLookup
from governance import approvals, caps, checks, tickets
from governance.audit import AuditLog
from governance.repo import GovernanceRepo
from governance.settings_store import SettingsStore
from governance.tiers import (
    MONEY_ACTIONS,
    PAYMENT_TYPES,
    describe,
    may_execute,
    may_request,
    tier_of,
)

BASE_CTL = ("CTL-AC-01", "CTL-LOG-01", "CTL-LOG-02")
SANDBOX_TARGETS = {
    ActionType.SCHEDULE_SUPPLIER_PAYMENT: "MockBank POST /bank/payments/schedule",
    ActionType.CANCEL_PAYMENT: "MockBank POST /bank/payments/{ref}/cancel",
    ActionType.SEND_REMINDER: "Messaging POST /messaging/send",
    ActionType.SEND_DELAY_REQUEST: "Messaging POST /messaging/send",
    ActionType.SUBMIT_LOAN_PACK: "Pretend lender POST /lender/applications",
    ActionType.TEST_T3_NOTE: "none (test-only, no sandbox call)",
}
CAP_KEYS = (
    "cap_per_action_cents",
    "cap_per_day_cents",
    "cap_per_payee_week_cents",
    "cap_msgs_per_customer_week",
)
_REASON_CTL = {
    ReasonCode.KILL_SWITCH: ["CTL-AC-07"],
    ReasonCode.BLOCKED_TYPE: ["CTL-AC-02"],
    ReasonCode.UNTRUSTED_ORIGIN: ["CTL-AC-03"],
    ReasonCode.ROLE_NOT_ALLOWED: ["CTL-AC-04"],
    ReasonCode.NEW_PAYEE: ["CTL-PAY-01"],
    ReasonCode.PAYEE_BLOCKED: ["CTL-PAY-01"],
    ReasonCode.ACCOUNT_CHANGED: ["CTL-PAY-02"],
    ReasonCode.AMOUNT_EXCEEDS_OUTSTANDING: ["CTL-PAY-03"],
    ReasonCode.PERIOD_LOCKED: ["CTL-FR-03"],
    ReasonCode.PACK_HASH_MISMATCH: ["CTL-FIN-04"],
    ReasonCode.DRAFT_MISMATCH: ["CTL-AI-01"],
    ReasonCode.AMOUNT_MISMATCH: ["CTL-AI-01"],
    ReasonCode.CAP_PER_ACTION: ["CTL-LIM-01"],
    ReasonCode.CAP_PER_DAY: ["CTL-LIM-02"],
    ReasonCode.CAP_PER_PAYEE_WEEK: ["CTL-LIM-03"],
    ReasonCode.CAP_MSGS_PER_CUSTOMER: ["CTL-LIM-04"],
    ReasonCode.RATE_LIMIT_REMINDER: ["CTL-LIM-05"],
    ReasonCode.SELF_APPROVAL_FORBIDDEN: ["CTL-SD-01"],
    ReasonCode.APPROVAL_PARAMS_CHANGED: ["CTL-AU-01"],
    ReasonCode.APPROVAL_REPLAY: ["CTL-AU-02"],
    ReasonCode.APPROVAL_EXPIRED: ["CTL-AU-02"],
    ReasonCode.APPROVAL_REQUIRED: ["CTL-AU-01"],
    ReasonCode.APPROVAL_INVALID: ["CTL-AU-01"],
    ReasonCode.TICKET_INVALID: ["CTL-AC-06"],
}
_TYPE_CTL = {ActionType.ACCEPT_LOAN: ["CTL-FIN-04"], ActionType.CHANGE_LIMITS: ["CTL-CHG-01"]}


class _Stop(Exception):
    """Internal: a pipeline step refused the action."""

    def __init__(self, reason: ReasonCode, message: str, ctl: list[str] | None = None):
        super().__init__(message)
        self.reason, self.message, self.ctl = reason, message, ctl or []


class Gate:
    """GateService (WP1)."""

    def __init__(
        self,
        repo: GovernanceRepo,
        audit: AuditLog,
        settings: SettingsStore,
        clock: Clock,
        ids: IdFactory,
        lookup: EntityLookup,
        executor: ActionExecutor,
        currency: str = "USD",
    ):
        self._repo, self._audit, self._settings = repo, audit, settings
        self._clock, self._ids, self._lookup, self._executor = clock, ids, lookup, executor
        self._currency = currency

    # -- logging helpers ---------------------------------------------------------------
    def _log(
        self,
        action: Action,
        decision: Decision,
        reason: ReasonCode,
        tier: Tier,
        ctl: list[str],
        **extra: Any,
    ) -> int:
        return self._audit.append_decision(
            action_id=action.action_id,
            action_type=action.type.value,
            decision=decision,
            reason_code=reason,
            principal_id=extra.pop("principal_id", action.requested_by.id),
            tier=int(tier),
            origin=action.origin,
            params=action.params,
            control_ids=[*BASE_CTL, *ctl],
            **extra,
        )

    def _decision(
        self,
        action: Action,
        decision: Decision,
        reason: ReasonCode,
        tier: Tier,
        message: str,
        ctl: list[str],
        proposal_id: str | None = None,
        **extra: Any,
    ) -> GateDecision:
        result: ExecutionResult | None = extra.pop("result_obj", None)
        summary: ApprovalSummary | None = extra.pop("summary", None)
        seq = self._log(action, decision, reason, tier, ctl, **extra)
        return GateDecision(
            action_id=action.action_id,
            proposal_id=proposal_id,
            decision=decision,
            tier=tier,
            reason_code=reason,
            message=message,
            log_seq=seq,
            result=result,
            summary=summary,
        )

    # -- pipeline steps 1-7 --------------------------------------------------------------
    def _effective_tier(self, action: Action) -> Tier:
        tier = tier_of(action.type)
        if tier == Tier.T3_AUTONOMOUS and action.type.value not in self._settings.tier3_enabled():
            return Tier.BLOCKED
        return tier

    def _limits(self) -> dict[str, int]:
        return {k: self._settings.get_int(k) for k in CAP_KEYS}

    def _others(self, exclude_proposal: str | None) -> list[Proposal]:
        return [p for p in self._repo.proposals(caps.COUNTED) if p.proposal_id != exclude_proposal]

    def _executed_payment(self, action_id: str) -> tuple[str | None, date | None] | None:
        prop = self._repo.proposal_by_action(action_id)
        if prop is None or prop.status != ProposalStatus.EXECUTED:
            return None
        sbx_ref = next(
            (
                r.sbx_ref
                for r in self._audit.rows()
                if r.action_id == action_id and r.decision == Decision.EXECUTED
            ),
            None,
        )
        vd = prop.action.params.get("value_date")
        return sbx_ref, (date.fromisoformat(str(vd)) if vd else None)

    def _run_checks(
        self,
        action: Action,
        tier: Tier,
        *,
        exclude_proposal: str | None = None,
        caps_too: bool = True,
    ) -> tuple[Any, list[str]]:
        """Steps 1-7. Returns (validated params, CTL ids exercised) or raises _Stop."""
        if self._settings.kill_switch() and tier != Tier.T0_OBSERVE:
            raise _Stop(ReasonCode.KILL_SWITCH, "kill switch is on: only observation is allowed")
        if tier == Tier.BLOCKED:
            raise _Stop(
                ReasonCode.BLOCKED_TYPE,
                f"{action.type.value} is not allowed",
                _TYPE_CTL.get(action.type, []),
            )
        self._check_origin(action, tier)
        if not may_request(action.requested_by, tier, action.on_behalf_of):
            raise _Stop(
                ReasonCode.ROLE_NOT_ALLOWED,
                f"{action.requested_by.role.value} may not request a {tier.name} action",
            )
        try:
            params = PARAMS_MODELS[action.type].model_validate(action.params)
        except ValidationError as exc:
            raise _Stop(
                ReasonCode.INVALID_PARAMS, f"invalid parameters: {exc.error_count()} error(s)"
            ) from exc
        today = self._clock.today()
        vd = getattr(params, "value_date", None)
        if vd is not None and vd < today:
            raise _Stop(ReasonCode.INVALID_PARAMS, "value date is in the past")
        ctl: list[str] = []
        try:
            ctl += checks.check_entities(
                action.type, params, self._lookup, today, self._executed_payment
            )
        except LoanReadyError as exc:
            raise _Stop(exc.reason_code, exc.message) from exc
        if caps_too:
            ctl += self._check_caps(action, exclude_proposal)
        return params, ctl

    def _check_caps(self, action: Action, exclude_proposal: str | None) -> list[str]:
        try:
            return caps.check_caps(
                action,
                self._others(exclude_proposal),
                self._limits(),
                self._clock.today(),
                self._clock.now(),
            )
        except LoanReadyError as exc:
            raise _Stop(exc.reason_code, exc.message) from exc

    @staticmethod
    def _check_origin(action: Action, tier: Tier) -> None:
        if action.origin == Origin.UNTRUSTED_DATA:
            raise _Stop(ReasonCode.UNTRUSTED_ORIGIN, "actions cannot originate from document text")
        if action.origin == Origin.SCHEDULE and tier != Tier.T3_AUTONOMOUS:
            raise _Stop(
                ReasonCode.UNTRUSTED_ORIGIN, "scheduled origin is only for enabled Tier 3 types"
            )
        if action.origin == Origin.SYSTEM and tier != Tier.T0_OBSERVE:
            raise _Stop(ReasonCode.UNTRUSTED_ORIGIN, "system origin is only for Tier 0")
        for ref in action.provenance:
            if not ref.trusted and (ref.field is None or ref.field in INSTRUCTION_FIELDS):
                raise _Stop(
                    ReasonCode.UNTRUSTED_ORIGIN,
                    f"{ref.field or 'a parameter'} comes from untrusted {ref.source.value} text",
                )

    # -- approval summary ----------------------------------------------------------------
    def _summary(self, proposal_id: str, action: Action, tier: Tier) -> ApprovalSummary:
        p = action.params
        name: str | None = None
        account: str | None = None
        warnings: list[str] = []
        if action.type == ActionType.SCHEDULE_SUPPLIER_PAYMENT:
            payee = self._lookup.payee(p["payee_id"])
            if payee is not None:
                name, account = payee.name, mask_account(payee.bank_account_ref)
                today = self._clock.today()
                if payee.first_paid_on is None or (today - payee.first_paid_on).days > 30:
                    warnings.append("First payment to this payee in 30 days")
        elif action.type == ActionType.SEND_REMINDER:
            inv = self._lookup.invoice(p["invoice_id"])
            cp = self._lookup.counterparty(inv.customer_id) if inv else None
            name, account = (cp.name if cp else None), mask_contact(p["to_ref"])
        elif action.type == ActionType.SEND_DELAY_REQUEST:
            bill = self._lookup.bill(p["bill_id"])
            cp = self._lookup.counterparty(bill.supplier_id) if bill else None
            name, account = (cp.name if cp else None), mask_contact(p["to_ref"])
        elif action.type == ActionType.SUBMIT_LOAN_PACK:
            name = p.get("lender_id")
        usage = self.cap_usage()
        amount = (
            p.get("amount_cents")
            if action.type in MONEY_ACTIONS | {ActionType.SEND_REMINDER}
            else None
        )
        if action.type in MONEY_ACTIONS and amount and amount * 2 > usage.per_day_cents:
            warnings.append("Amount is more than 50% of today's cap")
        return ApprovalSummary(
            proposal_id=proposal_id,
            action_type=action.type,
            tier=tier,
            amount_cents=amount,
            currency=self._currency,
            counterparty_name=name,
            payee_account_masked=account,
            reason=action.reason,
            sandbox_target=SANDBOX_TARGETS.get(action.type, ""),
            cap_used_today_cents=usage.used_today_cents,
            cap_per_day_cents=usage.per_day_cents,
            cap_per_action_cents=usage.per_action_cents,
            warnings=warnings,
            expires_at=None,
        )

    def _new_proposal(self, action: Action, tier: Tier) -> Proposal:
        pid = self._ids.new("prp")
        proposal = Proposal(
            proposal_id=pid,
            action=action,
            tier=tier,
            status=ProposalStatus.PENDING_APPROVAL,
            params_hash=params_hash(action.params),
            created_at=self._clock.now(),
            summary=self._summary(pid, action, tier),
        )
        self._repo.insert_proposal(proposal)
        return proposal

    # -- GateService ------------------------------------------------------------------------
    def evaluate(self, action: Action) -> GateDecision:
        tier = self._effective_tier(action)
        try:
            _params, ctl = self._run_checks(action, tier, caps_too=tier != Tier.T3_AUTONOMOUS)
        except _Stop as stop:
            return self._decision(
                action,
                Decision.BLOCKED,
                stop.reason,
                tier,
                stop.message,
                _REASON_CTL.get(stop.reason, []) + stop.ctl,
            )
        if tier == Tier.T0_OBSERVE:
            return self._decision(action, Decision.ALLOWED, ReasonCode.OK, tier, "allowed", ctl)
        if tier == Tier.T1_PROPOSE:
            return self._decision(
                action, Decision.DRAFTED, ReasonCode.OK, tier, "draft allowed", ctl
            )
        if tier == Tier.T3_AUTONOMOUS:
            return self._tier3(action, ctl)
        proposal = self._new_proposal(action, tier)
        return self._decision(
            action,
            Decision.PENDING_APPROVAL,
            ReasonCode.APPROVAL_REQUIRED,
            tier,
            "waiting for human approval",
            ctl,
            proposal.proposal_id,
            summary=proposal.summary,
        )

    def _tier3(self, action: Action, ctl: list[str]) -> GateDecision:
        """Tier 3: execute immediately within caps; over a cap, downgrade to a proposal."""
        try:
            ctl += self._check_caps(action, None)
        except _Stop as stop:
            self._log(
                action,
                Decision.EVENT,
                ReasonCode.TIER3_DOWNGRADED,
                Tier.T3_AUTONOMOUS,
                _REASON_CTL.get(stop.reason, []),
            )
            proposal = self._new_proposal(action, Tier.T2_APPROVE)
            return self._decision(
                action,
                Decision.PENDING_APPROVAL,
                ReasonCode.TIER3_DOWNGRADED,
                Tier.T2_APPROVE,
                f"over cap ({stop.reason.value}): needs approval",
                ctl,
                proposal.proposal_id,
                summary=proposal.summary,
            )
        ticket = tickets._mint(action, self._repo, self._clock, self._ids)
        result = self._executor.execute(action, ticket)
        decision = Decision.EXECUTED if result.ok else Decision.FAILED
        reason = (
            ReasonCode.EXECUTED if result.ok else (result.error_code or ReasonCode.SANDBOX_ERROR)
        )
        return self._decision(
            action,
            decision,
            reason,
            Tier.T3_AUTONOMOUS,
            reason.value,
            ctl + ["CTL-AC-06"],
            approved_by="policy:tier3",
            approved_at=ticket.issued_at,
            result=result.result,
            sbx_ref=result.sbx_ref,
            result_obj=result,
        )

    def _blocked_approval(
        self, proposal: Proposal, principal: Principal, exc: LoanReadyError
    ) -> None:
        self._log(
            proposal.action,
            Decision.BLOCKED,
            exc.reason_code,
            proposal.tier,
            _REASON_CTL.get(exc.reason_code, []) + ["CTL-SD-01"],
            principal_id=principal.id,
        )

    def approve(self, proposal_id: str, principal: Principal) -> ApprovalToken:
        proposal = self._repo.get_proposal(proposal_id)
        if proposal is None or proposal.status != ProposalStatus.PENDING_APPROVAL:
            raise ApprovalError(
                "no pending proposal with this id", reason_code=ReasonCode.APPROVAL_INVALID
            )
        try:
            approvals.check_approver(proposal, principal)
        except LoanReadyError as exc:
            self._blocked_approval(proposal, principal, exc)
            raise
        now = self._clock.now()
        token = approvals.issue(
            self._repo, proposal, principal, now, self._settings.get_int("approval_ttl_seconds")
        )
        self._repo.set_status(proposal_id, ProposalStatus.APPROVED, now)
        self._log(
            proposal.action,
            Decision.APPROVED,
            ReasonCode.OK,
            proposal.tier,
            ["CTL-SD-01", "CTL-AU-01", "CTL-AU-02"],
            principal_id=principal.id,
            approved_by=principal.id,
            approved_at=token.approved_at,
        )
        return token

    def reject(self, proposal_id: str, principal: Principal, note: str = "") -> GateDecision:
        proposal = self._repo.get_proposal(proposal_id)
        if proposal is None or proposal.status not in (
            ProposalStatus.PENDING_APPROVAL,
            ProposalStatus.APPROVED,
        ):
            raise ApprovalError(
                "no open proposal with this id", reason_code=ReasonCode.APPROVAL_INVALID
            )
        if principal.kind != PrincipalKind.HUMAN:
            raise PermissionDenied(
                "only a human may reject", reason_code=ReasonCode.ROLE_NOT_ALLOWED
            )
        self._repo.set_status(proposal_id, ProposalStatus.REJECTED, self._clock.now())
        return self._decision(
            proposal.action,
            Decision.REJECTED,
            ReasonCode.REJECTED_BY_USER,
            proposal.tier,
            note or "rejected",
            ["CTL-SD-01"],
            proposal_id,
            principal_id=principal.id,
            result={"note": note} if note else None,
        )

    def execute(self, proposal_id: str, token_id: str | None, principal: Principal) -> GateDecision:
        proposal = self._repo.get_proposal(proposal_id)
        if proposal is None:
            raise ApprovalError("unknown proposal", reason_code=ReasonCode.APPROVAL_INVALID)
        action, tier = proposal.action, proposal.tier

        def stop(
            reason: ReasonCode,
            message: str,
            status: ProposalStatus | None = None,
            ctl: list[str] | None = None,
        ) -> GateDecision:
            if status is not None:
                self._repo.set_status(proposal_id, status, self._clock.now())
            return self._decision(
                action,
                Decision.BLOCKED,
                reason,
                tier,
                message,
                _REASON_CTL.get(reason, []) + (ctl or []),
                proposal_id,
                principal_id=principal.id,
            )

        if not may_execute(principal):
            return stop(ReasonCode.ROLE_NOT_ALLOWED, f"{principal.role.value} may not execute")
        # 1. kill switch (a switch turned on after approval still stops execution)
        if self._settings.kill_switch():
            return stop(ReasonCode.KILL_SWITCH, "kill switch is on")
        # 2. token
        try:
            row = approvals.verify(self._repo, token_id, proposal, self._clock.now())
        except ApprovalError as exc:
            expired = exc.reason_code == ReasonCode.APPROVAL_EXPIRED
            return stop(exc.reason_code, exc.message, ProposalStatus.EXPIRED if expired else None)
        # 3. dynamic linking: the approved params hash must equal the stored action's hash
        current = params_hash(action.params)
        if current != row.params_hash or current != proposal.params_hash:
            return stop(
                ReasonCode.APPROVAL_PARAMS_CHANGED,
                "parameters changed after approval",
                ProposalStatus.FAILED,
            )
        # 4. re-run steps 1-7 (entities and caps may have changed since approval)
        try:
            # role/tier rules use the action's own tier (a downgraded Tier 3 stays a SYSTEM action)
            _params, ctl = self._run_checks(
                action, self._effective_tier(action), exclude_proposal=proposal_id
            )
        except _Stop as s:
            return stop(s.reason, s.message, ProposalStatus.BLOCKED, s.ctl)
        # 5. consume the token before calling the executor
        assert token_id is not None
        approvals.consume(self._repo, token_id, self._clock.now())
        # 6. mint the single-use ticket
        ticket = tickets._mint(action, self._repo, self._clock, self._ids)
        # 7. execute
        exec_ctl = ["CTL-AC-06", "CTL-SD-01", "CTL-AU-01", "CTL-AU-02", *ctl]
        if action.type in PAYMENT_TYPES:
            exec_ctl.append("CTL-PAY-04")
        try:
            result = self._executor.execute(action, ticket)
        except LoanReadyError as exc:
            self._repo.set_status(proposal_id, ProposalStatus.FAILED, self._clock.now())
            return self._decision(
                action,
                Decision.FAILED,
                exc.reason_code,
                tier,
                exc.message,
                exec_ctl,
                proposal_id,
                principal_id=principal.id,
                approved_by=row.approved_by,
                approved_at=row.approved_at,
            )
        except Exception as exc:  # never retry automatically; idempotency makes a manual retry safe
            self._repo.set_status(proposal_id, ProposalStatus.FAILED, self._clock.now())
            return self._decision(
                action,
                Decision.FAILED,
                ReasonCode.SANDBOX_ERROR,
                tier,
                f"execution error: {type(exc).__name__}",
                exec_ctl,
                proposal_id,
                principal_id=principal.id,
                approved_by=row.approved_by,
                approved_at=row.approved_at,
            )
        # 8. record the outcome
        ok = result.ok
        self._repo.set_status(
            proposal_id, ProposalStatus.EXECUTED if ok else ProposalStatus.FAILED, self._clock.now()
        )
        reason = ReasonCode.EXECUTED if ok else (result.error_code or ReasonCode.SANDBOX_ERROR)
        return self._decision(
            action,
            Decision.EXECUTED if ok else Decision.FAILED,
            reason,
            tier,
            reason.value,
            exec_ctl,
            proposal_id,
            principal_id=principal.id,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            result=result.result,
            sbx_ref=result.sbx_ref,
            result_obj=result,
        )

    def pending(self) -> list[Proposal]:
        return self._repo.proposals((ProposalStatus.PENDING_APPROVAL,))

    def proposal(self, proposal_id: str) -> Proposal | None:
        return self._repo.get_proposal(proposal_id)

    def cap_usage(self) -> CapUsage:
        return caps.usage(self._others(None), self._limits(), self._clock.today())

    def set_kill_switch(self, on: bool, principal: Principal) -> None:
        self._settings.set_kill_switch(on, principal)

    def update_setting(self, key: str, value: str, principal: Principal) -> None:
        self._settings.update(key, value, principal)

    def settings(self) -> dict[str, str]:
        return self._settings.all()

    def tier_table(self) -> list[TierRow]:
        return describe()

    def ticket_verifier(self) -> TicketVerifier:
        return tickets.verifier(self._repo, self._clock)
