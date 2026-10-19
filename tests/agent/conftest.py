"""Shared helpers for WP7 agent tests: an event recorder and scriptable stub backends."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from contracts.dto_agent import LLMRequest
from contracts.enums import EventType, LLMBackend


class EventRecorder:
    """Collects ``(EventType, payload)`` pairs the agent emits."""

    def __init__(self) -> None:
        self.events: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: dict[str, Any]) -> None:
        self.events.append((event, dict(payload)))

    def of(self, event: EventType) -> list[dict[str, Any]]:
        return [p for e, p in self.events if e == event]


class StubBackend:
    """A backend that answers from a script (strings, or exceptions to raise).

    ``fail_if_called`` makes any call fail the test (proves "before any network call")."""

    def __init__(
        self,
        name: LLMBackend,
        script: list[str | Exception] | None = None,
        *,
        remote: bool = False,
        model: str = "stub-model",
        fail_if_called: bool = False,
        responder: Callable[[LLMRequest, list[dict[str, str]]], str] | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.remote = remote
        self.script = list(script or [])
        self.fail_if_called = fail_if_called
        self.responder = responder
        self.calls: list[tuple[LLMRequest, list[dict[str, str]]]] = []

    def generate(self, req: LLMRequest, messages: list[dict[str, str]]) -> str:
        if self.fail_if_called:
            pytest.fail(f"{self.name} backend must not be called")
        self.calls.append((req, messages))
        if self.responder is not None:
            return self.responder(req, messages)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def events() -> EventRecorder:
    return EventRecorder()


@pytest.fixture
def stub() -> type[StubBackend]:
    """The StubBackend class (test modules cannot import this conftest by name)."""
    return StubBackend
