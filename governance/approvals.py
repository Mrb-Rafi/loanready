"""Approval tokens (PRD/03 §3.2; CTL-SD-01, CTL-AU-01, CTL-AU-02; REG-PAY-01).

A token is random (``secrets.token_hex(16)``), bound to the proposal's ``params_hash``
(dynamic linking), single use and short-lived. Only ``sha256(token_id)`` is stored; the
plain token lives in memory / the UI session only.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from contracts.actions import ApprovalToken, Principal, Proposal
from contracts.canonical import sha256_hex
from contracts.enums import PrincipalKind, ReasonCode
from contracts.errors import ApprovalError, PermissionDenied
from contracts.schema import ApprovalRow
from governance.repo import GovernanceRepo
from governance.tiers import may_approve


def token_hash(token_id: str) -> str:
    return sha256_hex(token_id)


def check_approver(proposal: Proposal, principal: Principal) -> None:
    """Role rule and maker-checker (GOV-04). Raises PermissionDenied or ApprovalError.

    The approver must be a human allowed for this type, and must not be the human who
    requested it. When the agent is the maker on the owner's behalf, the owner may approve
    (agent = maker, owner = checker)."""
    action = proposal.action
    if not may_approve(principal, action.type):
        raise PermissionDenied(
            f"{principal.role.value} may not approve {action.type.value}",
            reason_code=ReasonCode.ROLE_NOT_ALLOWED,
        )
    maker = action.requested_by
    if maker.kind == PrincipalKind.HUMAN and maker.id == principal.id:
        raise ApprovalError(
            "the requester cannot approve their own proposal",
            reason_code=ReasonCode.SELF_APPROVAL_FORBIDDEN,
        )


def issue(
    repo: GovernanceRepo, proposal: Proposal, principal: Principal, now: datetime, ttl_seconds: int
) -> ApprovalToken:
    token = ApprovalToken(
        token_id=secrets.token_hex(16),
        proposal_id=proposal.proposal_id,
        params_hash=proposal.params_hash,
        approved_by=principal,
        approved_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    repo.insert_approval(
        ApprovalRow(
            token_hash=token_hash(token.token_id),
            proposal_id=token.proposal_id,
            params_hash=token.params_hash,
            approved_by=principal.id,
            approved_at=token.approved_at,
            expires_at=token.expires_at,
        )
    )
    return token


def verify(
    repo: GovernanceRepo, token_id: str | None, proposal: Proposal, now: datetime
) -> ApprovalRow:
    """Token present, known, for this proposal, unused and unexpired. Raises ApprovalError."""
    if token_id is None:
        raise ApprovalError("approval required", reason_code=ReasonCode.APPROVAL_REQUIRED)
    row = repo.get_approval(token_hash(token_id))
    if row is None or row.proposal_id != proposal.proposal_id:
        raise ApprovalError("unknown approval token", reason_code=ReasonCode.APPROVAL_INVALID)
    if row.used_at is not None:
        raise ApprovalError("approval token already used", reason_code=ReasonCode.APPROVAL_REPLAY)
    if now > row.expires_at:
        raise ApprovalError("approval expired", reason_code=ReasonCode.APPROVAL_EXPIRED)
    return row


def consume(repo: GovernanceRepo, token_id: str, now: datetime) -> None:
    """Mark used **before** the executor runs, so a crash cannot allow a replay."""
    repo.mark_approval_used(token_hash(token_id), now)
