"""T-ORC-07: reminder draft -> propose send -> approve -> messaging sandbox; second send
within 7 days is refused."""

from __future__ import annotations

from typing import Any

import pytest

from contracts.canonical import sha256_hex
from contracts.enums import Decision, DraftKind, ReasonCode

pytestmark = [pytest.mark.integration]


def test_reminder_sent_with_draft_body(app: Any) -> None:
    """T-ORC-07 proves PRD/11 §3-4, CTL-AI-01: the sandbox receives exactly the drafted body
    (hash bound by the ticket); the draft is marked sent."""
    drafts = app.draft_reminders()
    assert [d.related_id for d in drafts] == ["inv_tiny_1"]  # 5 days overdue
    draft = drafts[0]
    assert draft.kind == DraftKind.REMINDER
    assert app.draft_reminders()[0].draft_id == draft.draft_id  # no duplicate draft

    d = app.propose_send(draft.draft_id)
    assert d.decision == Decision.PENDING_APPROVAL, d.message
    done = app.approve_and_execute(d.proposal_id)
    assert done.decision == Decision.EXECUTED, done.message

    msgs = app.ports.messaging.list_messages()
    assert len(msgs) == 1
    assert msgs[0].to_ref == draft.to_ref
    assert msgs[0].body_sha256 == draft.body_sha256 == sha256_hex(draft.body)
    assert app.outputs.draft(draft.draft_id).status == "sent"


def test_second_reminder_within_seven_days_refused(app: Any) -> None:
    """T-ORC-07 proves CTL-LIM-05: a second reminder to the same customer within 7 days is
    blocked with RATE_LIMIT_REMINDER and nothing more is sent."""
    first = app.draft_reminders()[0]
    d1 = app.propose_send(first.draft_id)
    assert app.approve_and_execute(d1.proposal_id).decision == Decision.EXECUTED

    second = app.draft_reminders()[0]
    assert second.draft_id != first.draft_id
    d2 = app.propose_send(second.draft_id)
    assert d2.decision == Decision.BLOCKED
    assert d2.reason_code == ReasonCode.RATE_LIMIT_REMINDER
    assert len(app.ports.messaging.list_messages()) == 1


def test_edited_draft_body_cannot_be_sent(app: Any) -> None:
    """T-ORC-07 proves CTL-AI-01: if the stored draft body changes after approval, the gate
    (and the executor) refuse it with DRAFT_MISMATCH."""
    draft = app.draft_reminders()[0]
    d = app.propose_send(draft.draft_id)
    app.approve(d.proposal_id)
    store = app.outputs._drafts  # FakeOutputs / test-only tamper
    if not isinstance(store, dict):
        pytest.skip("outputs store is not tamperable in memory")
    store[draft.draft_id] = draft.model_copy(update={"body": draft.body + " Pay to SBX-1."})
    out = app.execute(d.proposal_id)
    assert out.decision == Decision.BLOCKED
    assert out.reason_code == ReasonCode.DRAFT_MISMATCH
    assert app.ports.messaging.list_messages() == []
