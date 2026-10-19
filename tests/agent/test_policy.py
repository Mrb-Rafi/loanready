"""Data-class policy (CTL-AI-02): T-AGT-01."""

from __future__ import annotations

from typing import Any

import pytest

from agent import policy
from agent.llm import LLM
from agent.prompts import EXPLAIN_SCHEMA, WORDING_SCHEMA
from contracts.dto_agent import LLMRequest
from contracts.enums import DataClass, EventType, LLMBackend, LLMTask, SourceKind
from contracts.errors import InvalidParams, RemoteNotAllowed
from contracts.untrusted import Untrusted


def _llm(mode: str, backends: dict[LLMBackend, Any], events: Any) -> LLM:
    return LLM(mode=mode, events=events, backends=backends)


def test_task_policy_covers_every_task() -> None:
    """T-AGT-01 proves AGT-01, CTL-AI-02: every LLMTask has a policy row."""
    assert set(policy.TASK_POLICY) == set(LLMTask)


@pytest.mark.parametrize("dc", [DataClass.C3_BANK_ID, DataClass.C4_DOCUMENT])
@pytest.mark.parametrize("task", list(LLMTask))
def test_c3_c4_never_remote(task: LLMTask, dc: DataClass) -> None:
    """T-AGT-01 proves AGT-01, CTL-AI-02, REG-DP-03: C3/C4 are never allowed remotely."""
    assert policy.remote_allowed(task, dc) is False
    with pytest.raises(RemoteNotAllowed):
        policy.assert_remote_allowed(task, dc)


def test_local_only_tasks_never_remote() -> None:
    """T-AGT-01 proves CTL-AI-02: TIDY_RECEIPT_TEXT and INTENT_PICK are local only."""
    for task in (LLMTask.TIDY_RECEIPT_TEXT, LLMTask.INTENT_PICK):
        for dc in DataClass:
            assert policy.remote_allowed(task, dc) is False


def test_allowed_classes_pass() -> None:
    """T-AGT-01 proves CTL-AI-02: C1 explanations and masked C2 wording may go remote."""
    assert policy.remote_allowed(LLMTask.EXPLAIN_FORECAST, DataClass.C1_AGGREGATE)
    assert policy.remote_allowed(LLMTask.DRAFT_REMINDER, DataClass.C2_COUNTERPARTY)
    assert policy.remote_allowed(LLMTask.CATEGORISE_TXN, DataClass.C2_COUNTERPARTY)
    assert not policy.remote_allowed(LLMTask.EXPLAIN_FORECAST, DataClass.C2_COUNTERPARTY)


def test_request_above_task_max_class_rejected(events: Any) -> None:
    """T-AGT-01 proves AGT-01: data_class above the task's max class is refused outright."""
    llm = _llm("mock", {}, events)
    req = LLMRequest(
        task=LLMTask.EXPLAIN_FORECAST,
        payload={"week": "3"},
        data_class=DataClass.C2_COUNTERPARTY,
        output_schema=EXPLAIN_SCHEMA,
    )
    with pytest.raises(InvalidParams):
        llm.complete(req)


@pytest.mark.parametrize("dc", [DataClass.C3_BANK_ID, DataClass.C4_DOCUMENT])
def test_c3_c4_payload_in_openai_mode_never_reaches_remote(
    dc: DataClass, events: Any, stub: Any
) -> None:
    """T-AGT-01 proves AGT-01, AGT-19, CTL-AI-02: a C3/C4 request in openai mode is answered
    locally; the remote stub fails the test if it is ever called."""
    remote = stub(LLMBackend.OPENAI, remote=True, fail_if_called=True)
    llm = _llm("openai", {LLMBackend.OPENAI: remote}, events)
    req = LLMRequest(
        task=LLMTask.TIDY_RECEIPT_TEXT,
        payload={},
        data_class=dc,
        output_schema=EXPLAIN_SCHEMA,
        untrusted_blocks=[Untrusted("FLOUR 2KG acct 99887766554433", SourceKind.RECEIPT, "rcpt_1")],
    )
    resp = llm.complete(req)
    assert resp.backend == LLMBackend.MOCK
    assert LLMBackend.OPENAI not in resp.attempts
    assert events.of(EventType.LLM_REMOTE_CALL) == []


def test_second_guard_raises_before_network_and_emits_event(
    events: Any, stub: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-AGT-01 proves AGT-01: even if route resolution were wrong, the per-call guard refuses
    C4 remotely before the backend is called, emits REMOTE_NOT_ALLOWED and moves on."""
    remote = stub(LLMBackend.OPENAI, remote=True, fail_if_called=True)
    llm = _llm("openai", {LLMBackend.OPENAI: remote}, events)
    monkeypatch.setattr(llm, "resolve", lambda task, dc: [remote, llm.mock])
    req = LLMRequest(
        task=LLMTask.TIDY_RECEIPT_TEXT,
        payload={},
        data_class=DataClass.C4_DOCUMENT,
        output_schema=EXPLAIN_SCHEMA,
        untrusted_blocks=[Untrusted("receipt text", SourceKind.RECEIPT, "rcpt_2")],
    )
    resp = llm.complete(req)
    assert resp.backend == LLMBackend.MOCK
    assert resp.attempts == [LLMBackend.OPENAI, LLMBackend.MOCK]
    refused = events.of(EventType.REMOTE_NOT_ALLOWED)
    assert refused and refused[0]["task"] == "TIDY_RECEIPT_TEXT"
    assert refused[0]["data_class"] == "C4_DOCUMENT"
    fb = events.of(EventType.LLM_FALLBACK_USED)
    assert fb == [{"task": "TIDY_RECEIPT_TEXT", "from": "OPENAI", "to": "MOCK", "reason": "policy"}]


def test_allowed_remote_call_emits_event_without_content(events: Any, stub: Any) -> None:
    """T-AGT-01 proves AGT-02: an allowed remote call emits LLM_REMOTE_CALL with
    {task, data_class, model, cached} only, never content."""
    remote = stub(
        LLMBackend.OPENAI,
        ['{"text": "Cash dips in week 3."}'],
        remote=True,
        model="gpt-test",
    )
    llm = _llm("openai", {LLMBackend.OPENAI: remote}, events)
    req = LLMRequest(
        task=LLMTask.EXPLAIN_FORECAST,
        payload={"week": "3"},
        data_class=DataClass.C1_AGGREGATE,
        output_schema=EXPLAIN_SCHEMA,
    )
    resp = llm.complete(req)
    assert resp.backend == LLMBackend.OPENAI
    assert resp.model == "gpt-test"
    calls = events.of(EventType.LLM_REMOTE_CALL)
    assert calls == [
        {
            "task": "EXPLAIN_FORECAST",
            "data_class": "C1_AGGREGATE",
            "model": "gpt-test",
            "cached": False,
        }
    ]


def test_remote_wording_is_masked_and_unmasked(events: Any, stub: Any) -> None:
    """T-AGT-01 proves AGT-01, AGT-04, CTL-AI-03: C2 wording goes out with names masked and
    comes back unmasked."""

    def echo(req: LLMRequest, messages: list[dict[str, str]]) -> str:
        text = "\n".join(m["content"] for m in messages)
        assert "Golden Mills" not in text and "Tiny Bakery" not in text
        assert "{CUSTOMER_1}" in text and "{BUSINESS}" in text
        return (
            '{"subject": "Invoice INV-1 for {CUSTOMER_1}", '
            '"body": "Hello {CUSTOMER_1}, from {BUSINESS}"}'
        )

    remote = stub(LLMBackend.OPENAI, remote=True, responder=echo)
    llm = _llm("openai", {LLMBackend.OPENAI: remote}, events)
    req = LLMRequest(
        task=LLMTask.DRAFT_REMINDER,
        payload={
            "customer_name": "Golden Mills",
            "business_name": "Tiny Bakery",
            "invoice_number": "INV-1",
        },
        data_class=DataClass.C2_COUNTERPARTY,
        output_schema=WORDING_SCHEMA,
    )
    resp = llm.complete(req)
    assert resp.output["subject"] == "Invoice INV-1 for Golden Mills"
    assert resp.output["body"] == "Hello Golden Mills, from Tiny Bakery"


def test_residual_identifier_blocks_remote(events: Any, stub: Any) -> None:
    """T-AGT-02 proves AGT-05: an IBAN-like
    identifier that survives masking refuses the remote call and falls back locally."""
    remote = stub(LLMBackend.OPENAI, remote=True, fail_if_called=True)
    llm = _llm("openai", {LLMBackend.OPENAI: remote}, events)
    req = LLMRequest(
        task=LLMTask.DRAFT_REMINDER,
        payload={
            "customer_name": "Golden Mills",
            "business_name": "Tiny Bakery",
            "invoice_number": "INV-1",
            "note": "pay to GB82WEST1234AB56987X65",
        },
        data_class=DataClass.C2_COUNTERPARTY,
        output_schema=WORDING_SCHEMA,
    )
    resp = llm.complete(req)
    assert resp.backend == LLMBackend.MOCK
    assert events.of(EventType.REMOTE_NOT_ALLOWED)
