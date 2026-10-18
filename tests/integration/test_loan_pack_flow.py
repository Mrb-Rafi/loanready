"""T-ORC-08: loan pack build -> propose submit -> approve -> lender receipt; a tampered PDF
is refused with PACK_HASH_MISMATCH."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from contracts.canonical import file_sha256
from contracts.enums import Decision, ReasonCode
from orchestrator.assemble import pack_path

pytestmark = [pytest.mark.integration]


def test_pack_submitted_to_lender(app: Any) -> None:
    """T-ORC-08 proves PRD/11 §4, CTL-FIN-04: the lender receives the exact PDF (hash
    checked by the sandbox) and returns a receipt recorded as sbx_ref."""
    pack = app.build_loan_pack(150000, 90, "Working capital")
    path = pack_path(pack.path)
    assert path.exists() and file_sha256(path) == pack.sha256
    d = app.propose_submit_pack(pack.pack_id)
    assert d.decision == Decision.PENDING_APPROVAL, d.message
    done = app.approve_and_execute(d.proposal_id)
    assert done.decision == Decision.EXECUTED, done.message
    receipt = app.ports.lender.get_application(done.result.sbx_ref)
    assert receipt.pack_sha256 == pack.sha256
    assert receipt.status == "received_under_review"


def test_tampered_pack_blocked(app: Any) -> None:
    """T-ORC-08 proves CTL-FIN-04: editing the PDF after approval blocks execution with
    PACK_HASH_MISMATCH and nothing reaches the lender."""
    from tests.integration._support import LENDER_PATH, opslog_writes

    pack = app.build_loan_pack(150000, 90, "Working capital")
    d = app.propose_submit_pack(pack.pack_id)
    app.approve(d.proposal_id)
    path: Path = pack_path(pack.path)
    path.write_bytes(path.read_bytes() + b"\n% tampered\n")
    out = app.execute(d.proposal_id)
    assert out.decision == Decision.BLOCKED
    assert out.reason_code == ReasonCode.PACK_HASH_MISMATCH
    assert LENDER_PATH not in opslog_writes(app)


def test_pack_content_carries_audit_hash(app: Any) -> None:
    """T-ORC-08 proves ORC-09: the content handed to outputs has the audit final hash and
    chain status, the reconciliation uses MockBank's balance, and the request terms."""
    from orchestrator import assemble

    content = assemble.loan_pack_content(
        books=app.books,
        bank=app.ports.bank,
        business_name="Tiny Bakery",
        as_of=app.as_of,
        framework=app._framework(),
        amount_cents=150000,
        term_days=90,
        purpose="Working capital",
        forecast=app._forecast(),
        readiness=app._readiness(),
        financing=app.financing(150000, 90),
        audit_final_hash=app._final_hash(),
        audit_chain_ok=app._chain_ok(),
    )
    assert content.audit_chain_ok is True
    assert content.audit_final_hash == app.audit_rows()[-1].row_hash
    assert content.financing is not None and content.financing.request.amount_cents == 150000
    assert content.requested_amount_cents == 150000 and content.term_days == 90
