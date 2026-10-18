"""Governance (WP1): permission gate, tiers, approvals, execution tickets, caps, kill
switch, hash-chained audit log and the controls registry (PRD/03)."""

from governance.audit import AuditLog
from governance.controls import Controls
from governance.gate import Gate
from governance.service import build_governance

REQUIRES_CONTRACTS = "1"

__all__ = ["REQUIRES_CONTRACTS", "AuditLog", "Controls", "Gate", "build_governance"]
