"""Permission tiers (PRD/03 §2). Hard-coded, not loaded from config (GOV-01, GOV-02)."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from contracts.actions import Principal
from contracts.dto_app import TierRow
from contracts.enums import ActionType, PrincipalKind, Role, Tier

_T0 = (
    ActionType.CATEGORISE,
    ActionType.SEPARATE_PERSONAL,
    ActionType.BUILD_STATEMENTS,
    ActionType.RECONCILE,
    ActionType.RUN_FORECAST,
    ActionType.PLAN_CASH,
    ActionType.COMPARE_FINANCING,
    ActionType.SCORE_READINESS,
)
_T1 = (ActionType.DRAFT_REMINDER, ActionType.DRAFT_DELAY_REQUEST, ActionType.DRAFT_LOAN_PACK)
_T2 = (
    ActionType.SEND_REMINDER,
    ActionType.SEND_DELAY_REQUEST,
    ActionType.SCHEDULE_SUPPLIER_PAYMENT,
    ActionType.CANCEL_PAYMENT,
    ActionType.SUBMIT_LOAN_PACK,
)
_T3 = (ActionType.TEST_T3_NOTE,)

BLOCKED_TYPES: frozenset[ActionType] = frozenset(
    {
        ActionType.PAY_NEW_PAYEE,
        ActionType.ACCEPT_LOAN,
        ActionType.CHANGE_LIMITS,
        ActionType.DISABLE_GATE,
        ActionType.DISABLE_KILL_SWITCH,
        ActionType.EDIT_LOG,
        ActionType.DELETE_LOG,
        ActionType.ADD_PAYEE,
        ActionType.CHANGE_PAYEE_ACCOUNT,
        ActionType.APPROVE_OWN_PROPOSAL,
        ActionType.EXPORT_RAW_DATA_REMOTE,
    }
)

TIER_TABLE: Mapping[ActionType, Tier] = MappingProxyType(
    {
        **dict.fromkeys(_T0, Tier.T0_OBSERVE),
        **dict.fromkeys(_T1, Tier.T1_PROPOSE),
        **dict.fromkeys(_T2, Tier.T2_APPROVE),
        **dict.fromkeys(_T3, Tier.T3_AUTONOMOUS),
        **dict.fromkeys(BLOCKED_TYPES, Tier.BLOCKED),
    }
)

# Tier 2 types only the owner may approve (segregation of duties, CTL-SD-01).
PAYMENT_TYPES: frozenset[ActionType] = frozenset(
    {ActionType.SCHEDULE_SUPPLIER_PAYMENT, ActionType.CANCEL_PAYMENT}
)
# Actions that move money and count toward the money caps (MONEY_FIELD = amount_cents).
MONEY_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.SCHEDULE_SUPPLIER_PAYMENT, ActionType.TEST_T3_NOTE}
)
MESSAGE_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.SEND_REMINDER, ActionType.SEND_DELAY_REQUEST}
)

# Who may request which tier (PRD/03 §2 role rules). AGENT only on behalf of a human.
ROLE_RULES: Mapping[str, frozenset[Role]] = MappingProxyType(
    {
        "request_t0_t1": frozenset({Role.OWNER, Role.BOOKKEEPER, Role.AGENT, Role.SYSTEM}),
        "request_t2": frozenset({Role.OWNER, Role.BOOKKEEPER, Role.AGENT}),
        "request_t3": frozenset({Role.SYSTEM}),
        "approve_payment": frozenset({Role.OWNER}),
        "approve_other_t2": frozenset({Role.OWNER, Role.BOOKKEEPER}),
        "execute": frozenset({Role.OWNER, Role.BOOKKEEPER}),
        "settings": frozenset({Role.OWNER}),
    }
)

_DESCRIPTIONS: Mapping[ActionType, tuple[str, tuple[str, ...]]] = MappingProxyType(
    {
        ActionType.CATEGORISE: (
            "Categorise bank and POS lines",
            ("Rules first; low confidence goes to the owner",),
        ),
        ActionType.SEPARATE_PERSONAL: (
            "Flag personal spending",
            ("Owner confirms before posting to drawings",),
        ),
        ActionType.BUILD_STATEMENTS: (
            "Build P&L, cash flow, balance sheet",
            ("Read-only on the books",),
        ),
        ActionType.RECONCILE: ("Reconcile bank and books", ("Read-only",)),
        ActionType.RUN_FORECAST: (
            "13-week cash forecast",
            ("Ranges and assumptions always shown",),
        ),
        ActionType.PLAN_CASH: (
            "Cash plan: collect, delay, borrow",
            ("Produces Tier 1 drafts only",),
        ),
        ActionType.COMPARE_FINANCING: ("Compare financing costs", ("Neutral ranking; not advice",)),
        ActionType.SCORE_READINESS: ("Lender-readiness self-assessment", ("Business data only",)),
        ActionType.DRAFT_REMINDER: (
            "Draft a customer reminder",
            ("Local draft; numbers post-checked", "Invoice must be overdue"),
        ),
        ActionType.DRAFT_DELAY_REQUEST: (
            "Draft a supplier delay request",
            ("Only penalty-free, non-essential bills", "Within grace period"),
        ),
        ActionType.DRAFT_LOAN_PACK: ("Build the loan pack PDF", ("Local file with content hash",)),
        ActionType.SEND_REMINDER: (
            "Send a reminder via the messaging sandbox",
            (
                "Human approval bound to the body hash",
                "Recipient from stored contact only",
                "Max 1 per invoice per 7 days",
                "Messages per customer cap",
            ),
        ),
        ActionType.SEND_DELAY_REQUEST: (
            "Send a delay request to a supplier",
            (
                "Human approval bound to the body hash",
                "Recipient from stored contact only",
                "Messages per customer cap",
            ),
        ),
        ActionType.SCHEDULE_SUPPLIER_PAYMENT: (
            "Schedule a supplier payment in MockBank",
            (
                "Owner approval bound to amount, payee, account and date",
                "Known payee and stored account only (changed details blocked)",
                "Amount <= bill outstanding",
                "Per-action, per-day and per-payee caps",
                "Single-use execution ticket; idempotent bank write",
            ),
        ),
        ActionType.CANCEL_PAYMENT: (
            "Cancel a scheduled payment",
            ("Owner approval", "Only before the value date"),
        ),
        ActionType.SUBMIT_LOAN_PACK: (
            "Submit the loan pack to the pretend lender",
            (
                "Human approval bound to the pack hash",
                "File hash re-verified",
                "Lender never approves or accepts",
            ),
        ),
        ActionType.TEST_T3_NOTE: (
            "Test-only autonomous action",
            ("Disabled unless enabled in settings", "Caps apply; over cap -> needs approval"),
        ),
    }
)


def tier_of(action_type: ActionType) -> Tier:
    """Tier from the hard-coded table; anything unknown is BLOCKED (fail closed)."""
    return TIER_TABLE.get(action_type, Tier.BLOCKED)


def may_request(principal: Principal, tier: Tier, on_behalf_of: Principal | None) -> bool:
    """Role rule for creating an action of this tier (CTL-AC-04)."""
    if principal.role == Role.AGENT and (
        on_behalf_of is None or on_behalf_of.kind != PrincipalKind.HUMAN
    ):
        return False
    if tier in (Tier.T0_OBSERVE, Tier.T1_PROPOSE):
        return principal.role in ROLE_RULES["request_t0_t1"]
    if tier == Tier.T2_APPROVE:
        return principal.role in ROLE_RULES["request_t2"]
    if tier == Tier.T3_AUTONOMOUS:
        return principal.role in ROLE_RULES["request_t3"]
    return False


def may_approve(principal: Principal, action_type: ActionType) -> bool:
    """Only humans approve; payments only the owner (CTL-SD-01)."""
    if principal.kind != PrincipalKind.HUMAN:
        return False
    key = "approve_payment" if action_type in PAYMENT_TYPES else "approve_other_t2"
    return principal.role in ROLE_RULES[key]


def may_execute(principal: Principal) -> bool:
    return principal.kind == PrincipalKind.HUMAN and principal.role in ROLE_RULES["execute"]


def may_change_settings(principal: Principal) -> bool:
    return principal.kind == PrincipalKind.HUMAN and principal.role in ROLE_RULES["settings"]


def describe() -> list[TierRow]:
    """One row per ActionType for the UI, docs and the security self-assessment (GOV-03)."""
    rows = []
    for t in ActionType:
        tier = tier_of(t)
        if tier == Tier.BLOCKED:
            desc, safeguards = (
                "Blocked: refused and logged",
                ("Hard-coded blocked set; no setting can enable it",),
            )
        else:
            desc, safeguards = _DESCRIPTIONS[t]
        rows.append(
            TierRow(
                action_type=t.value, tier=tier.name, description=desc, safeguards=list(safeguards)
            )
        )
    return rows
