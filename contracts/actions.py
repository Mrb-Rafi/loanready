"""Actions, approvals, tickets and audit records (CON-08, CON-09, CON-10)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from contracts._model import Frozen
from contracts.canonical import canonical_json
from contracts.enums import (
    ActionType,
    Channel,
    Decision,
    Origin,
    PrincipalKind,
    ProposalStatus,
    ReasonCode,
    Role,
    SourceKind,
    Tier,
)
from contracts.untrusted import UNTRUSTED_FIELDS, Untrusted

# --- 6.1 core models ---------------------------------------------------------------


class Principal(Frozen):
    id: str  # "owner-rina", "bookkeeper-1", "agent", "system"
    kind: PrincipalKind
    role: Role
    display_name: str


OWNER = Principal(
    id="owner-rina", kind=PrincipalKind.HUMAN, role=Role.OWNER, display_name="Rina (owner)"
)
BOOKKEEPER = Principal(
    id="bookkeeper-1", kind=PrincipalKind.HUMAN, role=Role.BOOKKEEPER, display_name="Bookkeeper"
)
AGENT = Principal(
    id="agent", kind=PrincipalKind.AGENT, role=Role.AGENT, display_name="LoanReady agent"
)
SYSTEM = Principal(id="system", kind=PrincipalKind.SYSTEM, role=Role.SYSTEM, display_name="System")

PRINCIPALS_BY_ROLE: dict[Role, Principal] = {
    Role.OWNER: OWNER,
    Role.BOOKKEEPER: BOOKKEEPER,
    Role.AGENT: AGENT,
    Role.SYSTEM: SYSTEM,
}


class ProvenanceRef(Frozen):
    source: SourceKind
    ref_id: str
    field: str | None = None
    trusted: bool  # False for anything derived from Untrusted text


class Action(Frozen):
    action_id: str
    type: ActionType
    params: dict[str, Any]  # validated against PARAMS_MODELS[type] by the gate
    origin: Origin
    provenance: list[ProvenanceRef] = []
    reason: str  # written by code (may be worded by the LLM from facts)
    requested_by: Principal
    on_behalf_of: Principal | None = None
    requested_at: datetime


# --- 6.2 params models ---------------------------------------------------------------


class _Params(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RawTxnIdsParams(_Params):
    raw_txn_ids: list[str]


class PeriodParams(_Params):
    start: date
    end: date


class AsOfParams(_Params):
    as_of: date


class CompareFinancingParams(_Params):
    amount_cents: PositiveInt
    term_days: int = Field(ge=1, le=1825)


class DraftReminderParams(_Params):
    invoice_id: str


class DraftDelayRequestParams(_Params):
    bill_id: str
    proposed_date: date


class DraftLoanPackParams(_Params):
    requested_amount_cents: PositiveInt
    term_days: int = Field(ge=1, le=1825)
    purpose: str = Field(max_length=120)


class SendReminderParams(_Params):
    invoice_id: str
    draft_id: str
    channel: Channel
    to_ref: str
    amount_cents: PositiveInt
    body_sha256: str


class SendDelayRequestParams(_Params):
    bill_id: str
    draft_id: str
    channel: Channel
    to_ref: str
    proposed_date: date
    body_sha256: str


class ScheduleSupplierPaymentParams(_Params):
    bill_id: str
    payee_id: str
    amount_cents: PositiveInt
    value_date: date
    bank_account_ref: str
    from_account_id: str


class CancelPaymentParams(_Params):
    sbx_ref: str
    original_action_id: str


class SubmitLoanPackParams(_Params):
    pack_id: str
    pack_sha256: str
    lender_id: str


class TestT3NoteParams(_Params):
    amount_cents: PositiveInt
    note: str


class BlockedParams(BaseModel):
    """Anything; blocked types are never executed."""

    model_config = ConfigDict(frozen=True, extra="allow")


PARAMS_MODELS: dict[ActionType, type[BaseModel]] = {
    ActionType.CATEGORISE: RawTxnIdsParams,
    ActionType.SEPARATE_PERSONAL: RawTxnIdsParams,
    ActionType.BUILD_STATEMENTS: PeriodParams,
    ActionType.RECONCILE: AsOfParams,
    ActionType.RUN_FORECAST: AsOfParams,
    ActionType.PLAN_CASH: AsOfParams,
    ActionType.COMPARE_FINANCING: CompareFinancingParams,
    ActionType.SCORE_READINESS: AsOfParams,
    ActionType.DRAFT_REMINDER: DraftReminderParams,
    ActionType.DRAFT_DELAY_REQUEST: DraftDelayRequestParams,
    ActionType.DRAFT_LOAN_PACK: DraftLoanPackParams,
    ActionType.SEND_REMINDER: SendReminderParams,
    ActionType.SEND_DELAY_REQUEST: SendDelayRequestParams,
    ActionType.SCHEDULE_SUPPLIER_PAYMENT: ScheduleSupplierPaymentParams,
    ActionType.CANCEL_PAYMENT: CancelPaymentParams,
    ActionType.SUBMIT_LOAN_PACK: SubmitLoanPackParams,
    ActionType.TEST_T3_NOTE: TestT3NoteParams,
    ActionType.PAY_NEW_PAYEE: BlockedParams,
    ActionType.ACCEPT_LOAN: BlockedParams,
    ActionType.CHANGE_LIMITS: BlockedParams,
    ActionType.DISABLE_GATE: BlockedParams,
    ActionType.DISABLE_KILL_SWITCH: BlockedParams,
    ActionType.EDIT_LOG: BlockedParams,
    ActionType.DELETE_LOG: BlockedParams,
    ActionType.ADD_PAYEE: BlockedParams,
    ActionType.CHANGE_PAYEE_ACCOUNT: BlockedParams,
    ActionType.APPROVE_OWN_PROPOSAL: BlockedParams,
    ActionType.EXPORT_RAW_DATA_REMOTE: BlockedParams,
}

MONEY_FIELD = "amount_cents"
PAYEE_FIELDS = ("payee_id", "bank_account_ref")
# Params that carry instructions: untrusted provenance on any of them blocks the action.
INSTRUCTION_FIELDS = (
    "payee_id",
    "bank_account_ref",
    "amount_cents",
    "to_ref",
    "value_date",
    "lender_id",
)

# --- 6.3 proposal, approval, ticket, decision ---------------------------------------


class ApprovalSummary(Frozen):
    """Everything the approval dialog must show (UI-07)."""

    proposal_id: str
    action_type: ActionType
    tier: Tier
    amount_cents: int | None
    currency: str
    counterparty_name: str | None
    payee_account_masked: str | None  # last 4 only, "****1234"
    reason: str
    sandbox_target: str  # "MockBank POST /bank/payments/schedule"
    cap_used_today_cents: int
    cap_per_day_cents: int
    cap_per_action_cents: int
    warnings: list[str]
    expires_at: datetime | None


class Proposal(Frozen):
    proposal_id: str
    action: Action
    tier: Tier
    status: ProposalStatus
    params_hash: str
    created_at: datetime
    summary: ApprovalSummary


class ApprovalToken(Frozen):
    token_id: str  # secrets.token_hex(16); never deterministic, never logged in full
    proposal_id: str
    params_hash: str
    approved_by: Principal
    approved_at: datetime
    expires_at: datetime


class ExecutionTicket(Frozen):
    ticket_id: str
    action_id: str
    action_type: ActionType
    params_hash: str
    binding: dict[str, str]  # security-relevant fields the adapter must see unchanged (CON-09)
    issued_at: datetime
    expires_at: datetime  # issued_at + 60 s
    signature: str  # HMAC-SHA256(governance key, canonical_json(all fields except signature))


class ExecutionResult(Frozen):
    action_id: str
    ok: bool
    sbx_ref: str | None
    result: dict[str, Any]
    error_code: ReasonCode | None = None


class GateDecision(Frozen):
    action_id: str
    proposal_id: str | None
    decision: Decision
    tier: Tier
    reason_code: ReasonCode
    message: str
    log_seq: int
    result: ExecutionResult | None = None
    summary: ApprovalSummary | None = None


# --- 6.4 ticket binding --------------------------------------------------------------

TICKET_BINDING_FIELDS: dict[ActionType, tuple[str, ...]] = {
    ActionType.SCHEDULE_SUPPLIER_PAYMENT: (
        "payee_id",
        "bank_account_ref",
        "amount_cents",
        "value_date",
        "from_account_id",
    ),
    ActionType.CANCEL_PAYMENT: ("sbx_ref",),
    ActionType.SEND_REMINDER: ("to_ref", "body_sha256", "channel"),
    ActionType.SEND_DELAY_REQUEST: ("to_ref", "body_sha256", "channel"),
    ActionType.SUBMIT_LOAN_PACK: ("pack_sha256", "lender_id"),
}


def binding_value(value: Any) -> str:
    """Stringify one binding field with canonical rules (dates ISO, enums by value)."""
    rendered = canonical_json(value)
    return rendered[1:-1] if rendered.startswith('"') and rendered.endswith('"') else rendered


def binding_for(action_type: ActionType, params: dict[str, Any]) -> dict[str, str]:
    """The ``ExecutionTicket.binding`` for these params (CON-09)."""
    return {f: binding_value(params[f]) for f in TICKET_BINDING_FIELDS.get(action_type, ())}


# --- 6.5 audit record -----------------------------------------------------------------


class AuditRecord(Frozen):
    seq: int
    ts: datetime
    action_id: str | None
    action_type: str  # ActionType value, or EventType value for events
    tier: int | None
    origin: Origin | None
    principal_id: str
    decision: Decision
    reason_code: ReasonCode
    approved_by: str | None
    approved_at: datetime | None
    params_masked: dict[str, Any]
    result_masked: dict[str, Any]
    sbx_ref: str | None
    control_ids: list[str]  # CTL-* ids exercised by this decision (PRD/14 §3)
    prev_hash: str
    row_hash: str  # sha256_hex(prev_hash + canonical_json(record without row_hash))


CSV_COLUMNS = [
    "seq",
    "ts",
    "action_id",
    "action_type",
    "tier",
    "origin",
    "principal_id",
    "decision",
    "reason_code",
    "approved_by",
    "approved_at",
    "params_masked",
    "result_masked",
    "sbx_ref",
    "control_ids",
    "prev_hash",
    "row_hash",
]

_TEXT_KEYS = frozenset({"description", "memo", "note", "ocr_text", "body"}) | UNTRUSTED_FIELDS
_SECRET_KEYS = frozenset(
    {"token_id", "signature", "token", "api_key", "authorization", "password", "secret"}
)
_ACCOUNT_KEYS = frozenset({"iban", "account_number"})


def mask_account(ref: str) -> str:
    """``"****" + last 4``."""
    s = str(ref)
    return "****" + s[-4:] if len(s) > 4 else "****"


def mask_contact(ref: str) -> str:
    """Emails -> first char + "***@" + domain; phones -> first char + "***" + last 2 digits."""
    s = str(ref)
    if "@" in s:
        local, _, domain = s.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = [c for c in s if c.isdigit()]
    return f"{s[:1]}***{''.join(digits[-2:])}" if s else s


def _truncate(text: str) -> str:
    return text if len(text) <= 80 else text[:80] + "…"


def _mask_value(key: str, value: Any) -> Any:
    lk = key.lower()
    if lk in _SECRET_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return mask_params(value)
    if isinstance(value, list):
        return [_mask_value(key, v) for v in value]
    if value is None:
        return None
    if lk.endswith("_account_ref") or lk in _ACCOUNT_KEYS:
        return mask_account(value)
    if lk == "to_ref":
        return mask_contact(value)
    if isinstance(value, Untrusted):
        return _truncate(str(value))
    if lk in _TEXT_KEYS and isinstance(value, str):
        return _truncate(value)
    return value


def mask_params(params: dict[str, Any]) -> dict[str, Any]:
    """Mask a params/result dict before hashing and storage (GOV-15, CTL-LOG-04).

    Account refs -> "****" + last 4; to_ref -> masked contact; untrusted or free text
    (description, memo, note, ocr_text, body, ...) -> 80 chars + "…"; tokens and
    signatures -> "[redacted]". Nested dicts and lists are masked recursively.
    """
    return {str(k): _mask_value(str(k), v) for k, v in params.items()}
