"""T-ORC-11: chat intents call the right facade methods and never use numbers from the LLM."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from typing import Any

import pytest

from contracts.dto_agent import RouterResult
from contracts.enums import Decision, Intent
from contracts.money import fmt
from orchestrator.chat import AI_LABEL, match_payee

pytestmark = [pytest.mark.integration]


ROUTER_BUILT = importlib.util.find_spec("agent.router") is not None


def _route(monkeypatch: pytest.MonkeyPatch, app: Any, intent: Intent, **params: Any) -> None:
    def route(message: str) -> RouterResult:
        return RouterResult(
            intent=intent, confidence=Decimal("0.9"), method="llm", params=params, missing=[]
        )

    monkeypatch.setattr(app.agent, "route", route)


def test_cash_position_numbers_from_code(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-ORC-11 proves ORC-05, ORC-06: the cash figure is formatted by code; the agent's
    explanation carries the AI label."""
    _route(monkeypatch, app, Intent.CASH_POSITION)
    reply = app.chat("what's my cash position?")
    assert reply.intent == Intent.CASH_POSITION
    assert fmt(app.books.cash_balance(app.as_of)) in reply.text
    assert reply.ai_label == AI_LABEL


def test_who_owes_me(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-ORC-11 proves ORC-05: WHO_OWES_ME lists the ageing rows."""
    _route(monkeypatch, app, Intent.WHO_OWES_ME)
    reply = app.chat("who owes me money?")
    assert reply.intent == Intent.WHO_OWES_ME
    assert "Tiny Catering" in reply.text and fmt(60000) in reply.text
    assert reply.ai_label is None


def test_draft_reminders_creates_proposals(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-ORC-11 proves ORC-05: DRAFT_REMINDERS drafts, then proposes each send for approval."""
    _route(monkeypatch, app, Intent.DRAFT_REMINDERS)
    reply = app.chat("please remind my customers")
    assert reply.intent == Intent.DRAFT_REMINDERS
    assert len(reply.proposals) == 1
    assert {p.proposal_id for p in app.pending()} == set(reply.proposals)


def test_financing_amount_reparsed_by_code(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-ORC-11 proves ORC-06: an amount the owner typed is parsed by code; the reply's
    numbers come from the comparison DTO."""
    _route(monkeypatch, app, Intent.COMPARE_FINANCING, amount="2,500", term_days="60")
    reply = app.chat("compare borrowing 2,500 for 60 days")
    assert reply.intent == Intent.COMPARE_FINANCING
    assert f"For {fmt(250000)} over 60 days" in reply.text


def test_schedule_payment_uses_stored_bill(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-ORC-11 proves ORC-04, ORC-05: the payee name is matched fuzzily against stored
    payees; the amount is the stored bill's outstanding, not a model number."""
    _route(monkeypatch, app, Intent.SCHEDULE_PAYMENT, payee="tiny mill", amount_cents=1)
    reply = app.chat("pay tiny mill")
    assert reply.intent == Intent.SCHEDULE_PAYMENT
    assert len(reply.proposals) == 1
    prop = app.gate.proposal(reply.proposals[0])
    # oldest open bill for the payee is BILL-1 (the fraud bill is newer and blocked anyway)
    assert prop.action.params["bill_id"] == "bil_tiny_1"
    assert prop.action.params["amount_cents"] == 80000


def test_schedule_payment_unknown_payee_asks(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-ORC-11 proves ORC-05: no confident match -> ask, create nothing."""
    _route(monkeypatch, app, Intent.SCHEDULE_PAYMENT, payee="Golden Mills")
    reply = app.chat("pay golden mills")
    assert reply.proposals == [] and app.pending() == []
    assert "could not find" in reply.text


def test_fuzzy_threshold() -> None:
    """T-ORC-11 proves ORC-05: exact match, fuzzy >= 0.85, else None."""
    from contracts.testing.builders import make_payee

    payees = [make_payee(name="Tiny Mills")]
    assert match_payee("TINY MILLS", payees) is payees[0]
    assert match_payee("Tiny Mils", payees) is payees[0]
    assert match_payee("Grain Depot", payees) is None


def test_unknown_and_help(app: Any) -> None:
    """T-ORC-11 proves ORC-05: UNKNOWN -> help text, no action."""
    reply = app.chat("sing me a song")
    assert reply.intent == Intent.UNKNOWN and "cash position" in reply.text
    assert app.pending() == []


def test_loan_pack_and_log(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-ORC-11 proves ORC-05: BUILD_LOAN_PACK builds and proposes submit; SHOW_LOG reports
    the chain."""
    _route(monkeypatch, app, Intent.BUILD_LOAN_PACK)
    reply = app.chat("build my loan pack")
    assert reply.intent == Intent.BUILD_LOAN_PACK and reply.attachments
    assert app.gate.proposal(reply.proposals[0]).status.value == Decision.PENDING_APPROVAL.value
    _route(monkeypatch, app, Intent.SHOW_LOG)
    log = app.chat("show the log")
    assert "Hash chain intact" in log.text


@pytest.mark.skipif(not ROUTER_BUILT, reason="agent/router.py (WP7 SHOULD) not built yet")
def test_natural_language_routing(app: Any) -> None:
    """T-ORC-11 proves ORC-05 end to end: the agent's router maps owner phrases to intents.
    The tests above inject the intent so they check the orchestrator's handlers on their own."""
    assert app.chat("what's my cash position?").intent == Intent.CASH_POSITION
    assert app.chat("who owes me money?").intent == Intent.WHO_OWES_ME
