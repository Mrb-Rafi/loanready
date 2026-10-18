"""Execution tickets (PRD/03 §4; GOV-09..11; CTL-AC-06).

The HMAC key is created at import, held only in process memory, never written, logged or
exposed. ``_mint`` is private and referenced only from ``governance/gate.py`` (static test),
so no code path can obtain a ticket without passing the gate.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import timedelta

from contracts.actions import Action, ExecutionTicket, binding_for
from contracts.canonical import canonical_json, params_hash
from contracts.clock import Clock
from contracts.enums import ActionType
from contracts.errors import TicketInvalid
from contracts.ids import IdFactory
from contracts.ports import TicketVerifier
from governance.repo import GovernanceRepo

_KEY = secrets.token_bytes(32)
TICKET_TTL_SECONDS = 60


def _sign(fields: dict) -> str:
    return hmac.new(_KEY, canonical_json(fields).encode("utf-8"), hashlib.sha256).hexdigest()


def _mint(action: Action, repo: GovernanceRepo, clock: Clock, ids: IdFactory) -> ExecutionTicket:
    """Mint and register a single-use ticket for this exact action (gate only)."""
    issued = clock.now()
    fields = {
        "ticket_id": ids.new("tkt"),
        "action_id": action.action_id,
        "action_type": action.type,
        "params_hash": params_hash(action.params),
        "binding": binding_for(action.type, action.params),
        "issued_at": issued,
        "expires_at": issued + timedelta(seconds=TICKET_TTL_SECONDS),
    }
    ticket = ExecutionTicket(**fields, signature=_sign(fields))
    repo.insert_ticket(ticket.ticket_id, ticket.action_id, ticket.issued_at, ticket.expires_at)
    return ticket


def verifier(repo: GovernanceRepo, clock: Clock) -> TicketVerifier:
    """The function adapters call before every sandbox write (GOV-10)."""

    def verify(
        ticket: ExecutionTicket, expected_type: ActionType, binding: Mapping[str, str]
    ) -> None:
        fields = ticket.model_dump(mode="python", exclude={"signature"})
        if not hmac.compare_digest(_sign(fields), ticket.signature):
            raise TicketInvalid("ticket signature invalid")
        if clock.now() > ticket.expires_at:
            raise TicketInvalid("ticket expired")
        if ticket.action_type != expected_type:
            raise TicketInvalid("ticket is for a different action type")
        if dict(binding) != dict(ticket.binding):
            diff = sorted(
                k
                for k in set(binding) | set(ticket.binding)
                if binding.get(k) != ticket.binding.get(k)
            )
            raise TicketInvalid(f"request does not match the approved action: {', '.join(diff)}")
        if not repo.use_ticket(ticket.ticket_id, clock.now()):
            raise TicketInvalid("ticket unknown or already used")

    return verify
