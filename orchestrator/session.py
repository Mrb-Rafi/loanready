"""UI session state (PRD/11 §1): the acting principal, approval tokens held in memory, and the
data version the UI uses as a cache key (UI-12)."""

from __future__ import annotations

from contracts.actions import OWNER, PRINCIPALS_BY_ROLE, Principal
from contracts.enums import Role

UI_ROLES = (Role.OWNER, Role.BOOKKEEPER)


class Session:
    """One per App. Tokens never leave the process and are never logged in full."""

    def __init__(self, principal: Principal = OWNER):
        self._principal = principal
        self._tokens: dict[str, str] = {}
        self._version = 0

    def current(self) -> Principal:
        return self._principal

    def set_role(self, role: Role) -> None:
        if role not in UI_ROLES:
            raise ValueError("UI principal must be OWNER or BOOKKEEPER")
        self._principal = PRINCIPALS_BY_ROLE[role]
        self.bump()

    def hold_token(self, proposal_id: str, token_id: str) -> None:
        self._tokens[proposal_id] = token_id

    def pop_token(self, proposal_id: str) -> str | None:
        return self._tokens.pop(proposal_id, None)

    def bump(self) -> int:
        self._version += 1
        return self._version

    @property
    def version(self) -> int:
        return self._version
