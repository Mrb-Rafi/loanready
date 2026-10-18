"""SQL access to the governance tables: proposals, approvals, tickets, settings (ARC-27).

The action log is written only by ``governance.audit`` (append only)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import Session, select

from contracts.actions import Action, ApprovalSummary, Proposal
from contracts.enums import ProposalStatus, Tier
from contracts.schema import ApprovalRow, ProposalRow, SettingRow, TicketRow


class GovernanceRepo:
    def __init__(self, engine: Engine):
        self.engine = engine

    # proposals ------------------------------------------------------------------------
    @staticmethod
    def _to_proposal(row: ProposalRow) -> Proposal:
        return Proposal(
            proposal_id=row.id,
            action=Action.model_validate_json(row.action_json),
            tier=Tier(row.tier),
            status=ProposalStatus(row.status),
            params_hash=row.params_hash,
            created_at=row.created_at,
            summary=ApprovalSummary.model_validate_json(row.summary_json),
        )

    def insert_proposal(self, p: Proposal) -> None:
        with Session(self.engine) as s:
            s.add(
                ProposalRow(
                    id=p.proposal_id,
                    action_id=p.action.action_id,
                    action_json=p.action.model_dump_json(),
                    tier=int(p.tier),
                    status=p.status.value,
                    params_hash=p.params_hash,
                    summary_json=p.summary.model_dump_json(),
                    created_at=p.created_at,
                )
            )
            s.commit()

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with Session(self.engine) as s:
            row = s.get(ProposalRow, proposal_id)
            return self._to_proposal(row) if row else None

    def proposal_by_action(self, action_id: str) -> Proposal | None:
        with Session(self.engine) as s:
            row = s.exec(select(ProposalRow).where(ProposalRow.action_id == action_id)).first()
            return self._to_proposal(row) if row else None

    def set_status(self, proposal_id: str, status: ProposalStatus, decided_at: datetime) -> None:
        with Session(self.engine) as s:
            row = s.get(ProposalRow, proposal_id)
            if row is None:
                return
            row.status = status.value
            row.decided_at = decided_at
            s.add(row)
            s.commit()

    def proposals(self, statuses: tuple[ProposalStatus, ...] | None = None) -> list[Proposal]:
        with Session(self.engine) as s:
            q = select(ProposalRow)
            if statuses:
                q = q.where(ProposalRow.status.in_([x.value for x in statuses]))  # type: ignore[attr-defined]
            rows = s.exec(q.order_by(ProposalRow.created_at, ProposalRow.id)).all()
            return [self._to_proposal(r) for r in rows]

    # approvals ------------------------------------------------------------------------
    def insert_approval(self, row: ApprovalRow) -> None:
        with Session(self.engine) as s:
            s.add(row)
            s.commit()

    def get_approval(self, token_hash: str) -> ApprovalRow | None:
        with Session(self.engine) as s:
            row = s.get(ApprovalRow, token_hash)
            if row is not None:
                s.expunge(row)
            return row

    def mark_approval_used(self, token_hash: str, used_at: datetime) -> None:
        with Session(self.engine) as s:
            row = s.get(ApprovalRow, token_hash)
            if row is not None:
                row.used_at = used_at
                s.add(row)
                s.commit()

    # tickets --------------------------------------------------------------------------
    def insert_ticket(
        self, ticket_id: str, action_id: str, issued_at: datetime, expires_at: datetime
    ) -> None:
        with Session(self.engine) as s:
            s.add(
                TicketRow(
                    ticket_id=ticket_id,
                    action_id=action_id,
                    issued_at=issued_at,
                    expires_at=expires_at,
                )
            )
            s.commit()

    def use_ticket(self, ticket_id: str, used_at: datetime) -> bool:
        """Mark a known, unused ticket as used. False if unknown or already used."""
        with Session(self.engine) as s:
            row = s.get(TicketRow, ticket_id)
            if row is None or row.used_at is not None:
                return False
            row.used_at = used_at
            s.add(row)
            s.commit()
            return True

    # settings -------------------------------------------------------------------------
    def settings(self) -> dict[str, str]:
        with Session(self.engine) as s:
            return {r.key: r.value for r in s.exec(select(SettingRow)).all()}

    def put_setting(self, key: str, value: str, updated_by: str, updated_at: datetime) -> None:
        with Session(self.engine) as s:
            row = s.get(SettingRow, key)
            if row is None:
                row = SettingRow(key=key, value=value, updated_by=updated_by, updated_at=updated_at)
            else:
                row.value, row.updated_by, row.updated_at = value, updated_by, updated_at
            s.add(row)
            s.commit()
