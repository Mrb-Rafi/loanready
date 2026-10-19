"""Owner confirm queue (PRD/05 §6, BKS-15, CTL-FR-06): item builders and choice checks."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from contracts.actions import Principal
from contracts.dto_books import ConfirmItemDTO
from contracts.enums import ConfirmKind, PrincipalKind, ReasonCode, Role
from contracts.errors import InvalidParams, PermissionDenied
from contracts.money import fmt
from contracts.registry import CHART_OF_ACCOUNTS, PERSONAL_LABEL

GENUINE = "genuine_second_purchase"
REFUND = "duplicate_claim_refund"
RECEIPT_CONFIRM = "confirm"
RECEIPT_REJECT = "reject"
POSTABLE_CODES = frozenset(a.code for a in CHART_OF_ACCOUNTS if not a.is_cash)
EXPENSE_CODES = frozenset(a.code for a in CHART_OF_ACCOUNTS if a.type.value == "EXPENSE")


def category_item(
    item_id: str, raw_id: str, memo: str, cents: int, proposed: str, conf: Decimal, at: datetime
) -> ConfirmItemDTO:
    return ConfirmItemDTO(
        id=item_id,
        kind=ConfirmKind.CATEGORY,
        ref_id=raw_id,
        question=f"Which account is '{memo[:60]}' ({fmt(abs(cents))})?",
        proposed=proposed,
        options=[proposed, PERSONAL_LABEL],
        confidence=conf,
        created_at=at,
    )


def personal_item(
    item_id: str, raw_id: str, memo: str, cents: int, conf: Decimal, at: datetime
) -> ConfirmItemDTO:
    return ConfirmItemDTO(
        id=item_id,
        kind=ConfirmKind.PERSONAL,
        ref_id=raw_id,
        question=f"Is '{memo[:60]}' ({fmt(abs(cents))}) a personal expense?",
        proposed=PERSONAL_LABEL,
        options=[PERSONAL_LABEL, "6900"],
        confidence=conf,
        created_at=at,
    )


def duplicate_item(
    item_id: str, raw_id: str, memo: str, cents: int, original_ref: str, at: datetime
) -> ConfirmItemDTO:
    return ConfirmItemDTO(
        id=item_id,
        kind=ConfirmKind.DUPLICATE,
        ref_id=raw_id,
        question=f"'{memo[:60]}' ({fmt(abs(cents))}) looks like a repeat of {original_ref}.",
        proposed=REFUND,
        options=[GENUINE, REFUND],
        confidence=None,
        created_at=at,
    )


def receipt_item(
    item_id: str, receipt_id: str, summary: str, conf: Decimal, at: datetime
) -> ConfirmItemDTO:
    return ConfirmItemDTO(
        id=item_id,
        kind=ConfirmKind.RECEIPT,
        ref_id=receipt_id,
        question=f"Please check this receipt: {summary}",
        proposed=RECEIPT_CONFIRM,
        options=[RECEIPT_CONFIRM, RECEIPT_REJECT],
        confidence=conf,
        created_at=at,
    )


def check_principal(principal: Principal) -> None:
    """Only humans (owner or bookkeeper) resolve items (BKS-15)."""
    if principal.kind != PrincipalKind.HUMAN or principal.role not in (
        Role.OWNER,
        Role.BOOKKEEPER,
    ):
        raise PermissionDenied(
            "only the owner or bookkeeper may confirm items",
            reason_code=ReasonCode.ROLE_NOT_ALLOWED,
        )


def check_choice(item: ConfirmItemDTO, choice: str) -> None:
    """CATEGORY: any non-cash account code or PERSONAL; PERSONAL: PERSONAL or any expense
    account (owner override, BKS-08); DUPLICATE and RECEIPT: one of the options."""
    if item.kind == ConfirmKind.CATEGORY:
        ok = choice == PERSONAL_LABEL or choice in POSTABLE_CODES
    elif item.kind == ConfirmKind.PERSONAL:
        ok = choice == PERSONAL_LABEL or choice in EXPENSE_CODES
    else:
        ok = choice in item.options
    if not ok:
        raise InvalidParams(f"choice {choice!r} not allowed for {item.kind.value} item")
