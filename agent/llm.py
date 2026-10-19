"""The single entry point for model calls (PRD/09 §2-§5, CTL-AI-02/03/05, REG-AI-03).

``LLM.complete(req)``: policy check → prompt build (untrusted only in data blocks) → resolve
backends → per backend: remote guard → mask → call → validate (one retry) → unmask; on any
failure move to the next backend, ending in the deterministic mock.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from agent import policy
from agent.backends.mock import MockBackend
from agent.masking import Masker, assert_no_identifiers
from agent.prompts import RETRY_INSTRUCTION, assert_untrusted_enclosed, build_messages
from contracts.clock import get_clock
from contracts.dto_agent import LLMRequest, LLMResponse
from contracts.enums import CounterpartyKind, DataClass, EventType, LLMBackend, LLMTask
from contracts.errors import LLMOutputInvalid, RemoteNotAllowed

log = logging.getLogger(__name__)

EventSink = Callable[[EventType, dict[str, Any]], None]
Mode = Literal["mock", "ollama", "openai", "hybrid"]

# Mode → backends tried before the implicit final mock. ``hybrid`` keeps the local model
# first until the SHOULD module ``agent.routing`` provides DEFAULT_ROUTES (PRD/09 §2.2).
_MODE_ORDER: dict[str, tuple[LLMBackend, ...]] = {
    "mock": (),
    "ollama": (LLMBackend.OLLAMA,),
    "openai": (LLMBackend.OPENAI,),
    "hybrid": (LLMBackend.OLLAMA, LLMBackend.OPENAI),
}

# Payload keys whose values are names to mask before a remote call (AGT-04).
_NAME_KEYS: dict[str, CounterpartyKind] = {
    "customer_name": CounterpartyKind.CUSTOMER,
    "supplier_name": CounterpartyKind.SUPPLIER,
    "counterparty_name": CounterpartyKind.OTHER,
}
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key\S*)", re.IGNORECASE)


class Backend(Protocol):
    name: LLMBackend
    model: str
    remote: bool

    def generate(self, req: LLMRequest, messages: list[dict[str, str]]) -> str: ...


class BackendError(Exception):
    """A backend could not answer. ``reason``: timeout, connection, budget, unavailable."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _no_events(_event: EventType, _payload: dict[str, Any]) -> None:
    return None


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", text)


# --- validation (AGT-08, AGT-09) -------------------------------------------------------------


def _field(spec: dict[str, Any]) -> tuple[Any, Any]:
    if "enum" in spec:
        values = tuple(spec["enum"])
        if not values:
            raise LLMOutputInvalid("schema enum is empty")
        return Literal[values], Field(...)  # type: ignore[valid-type]
    kind = spec.get("type")
    if kind == "string":
        return str, Field(..., max_length=spec.get("maxLength"))
    if kind == "number":
        return float, Field(..., ge=spec.get("minimum"), le=spec.get("maximum"))
    if kind == "integer":
        return int, Field(..., ge=spec.get("minimum"), le=spec.get("maximum"))
    raise LLMOutputInvalid(f"unsupported schema type {kind!r}")


def _model_for(schema: dict[str, Any]) -> type[BaseModel]:
    props: dict[str, Any] = schema.get("properties", {})
    fields = {name: _field(spec) for name, spec in props.items()}
    return create_model(  # type: ignore[call-overload,no-any-return]
        "LLMOutput",
        __config__=ConfigDict(extra="forbid", strict=False),
        **fields,
    )


def validate_output(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON and validate it against a pydantic model built from the schema; raise
    ``LLMOutputInvalid`` on any problem. Strings stay strings; numbers are not coerced from
    strings."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise LLMOutputInvalid("output is not JSON") from exc
    if not isinstance(data, dict):
        raise LLMOutputInvalid("output is not a JSON object")
    model = _model_for(schema)
    for name, spec in schema.get("properties", {}).items():
        value = data.get(name)
        if spec.get("type") == "string" and not isinstance(value, str):
            raise LLMOutputInvalid(f"{name} must be a string")
        if spec.get("type") == "number" and (
            isinstance(value, bool) or not isinstance(value, int | float)
        ):
            raise LLMOutputInvalid(f"{name} must be a number")
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as exc:
        raise LLMOutputInvalid("output does not match the schema") from exc


# --- client ----------------------------------------------------------------------------------


class LLM:
    """``LLMClient`` implementation. ``backends`` injects real or stub backends (ollama/openai
    are SHOULD/OPTIONAL modules); the mock is always present and always last."""

    def __init__(
        self,
        settings: Any | None = None,
        events: EventSink | None = None,
        *,
        mode: str | None = None,
        backends: Mapping[LLMBackend, Backend] | None = None,
    ) -> None:
        if mode is None:
            if settings is None:
                from infra.settings import get_settings

                settings = get_settings()
            mode = str(settings.llm_backend)
        mode = mode.lower()
        if mode not in _MODE_ORDER:
            raise ValueError(f"unknown LLM_BACKEND {mode!r}")
        self.mode: str = mode
        self.events: EventSink = events or _no_events
        self.mock = MockBackend()
        self.backends: dict[LLMBackend, Backend] = {
            k: v for k, v in (backends or {}).items() if k != LLMBackend.MOCK
        }
        self._remote_calls: dict[date, int] = {}

    # route ---------------------------------------------------------------------------------

    def resolve(self, task: LLMTask, data_class: DataClass) -> list[Backend]:
        """Backends to try, in order (AGT-19): the mode's list, minus remote backends the
        policy forbids for this task and class, minus backends not configured, then mock."""
        chain: list[Backend] = []
        for name in _MODE_ORDER[self.mode]:
            backend = self.backends.get(name)
            if backend is None:
                continue
            if backend.remote and not policy.remote_allowed(task, data_class):
                continue
            chain.append(backend)
        chain.append(self.mock)
        return chain

    # call ----------------------------------------------------------------------------------

    def complete(self, req: LLMRequest, *, masker: Masker | None = None) -> LLMResponse:
        policy.check_request(req.task, req.data_class)
        messages = build_messages(req)
        assert_untrusted_enclosed(messages, req)
        chain = self.resolve(req.task, req.data_class)
        attempts: list[LLMBackend] = []
        for i, backend in enumerate(chain):
            attempts.append(backend.name)
            nxt = chain[i + 1].name.value if i + 1 < len(chain) else None
            try:
                output, latency = self._try(backend, req, messages, masker)
            except _Fallback as fb:
                if nxt is None:
                    raise LLMOutputInvalid(f"mock backend failed: {fb.reason}") from None
                self.events(
                    EventType.LLM_FALLBACK_USED,
                    {
                        "task": req.task.value,
                        "from": backend.name.value,
                        "to": nxt,
                        "reason": fb.reason,
                    },
                )
                continue
            return LLMResponse(
                task=req.task,
                backend=backend.name,
                model=backend.model,
                output=output,
                cached=False,
                fallback_used=len(attempts) > 1,
                latency_ms=latency,
                attempts=attempts,
            )
        raise LLMOutputInvalid("no backend answered")  # pragma: no cover - mock always answers

    def _try(
        self,
        backend: Backend,
        req: LLMRequest,
        messages: list[dict[str, str]],
        masker: Masker | None,
    ) -> tuple[dict[str, Any], int]:
        send = messages
        active_masker: Masker | None = None
        if backend.remote:
            try:
                policy.assert_remote_allowed(req.task, req.data_class)
                active_masker = masker or self._masker_for(req)
                send = [{**m, "content": active_masker.mask(m["content"])} for m in messages]
                for m in send:
                    assert_no_identifiers(m["content"])
            except RemoteNotAllowed as exc:
                self.events(
                    EventType.REMOTE_NOT_ALLOWED,
                    {
                        "task": req.task.value,
                        "data_class": req.data_class.name,
                        "backend": backend.name.value,
                        "reason": exc.message,
                    },
                )
                raise _Fallback("policy") from None
            self._count_remote(req, backend)
        started = time.perf_counter()
        output = self._call_validated(backend, req, send)
        latency = 0 if backend is self.mock else int((time.perf_counter() - started) * 1000)
        if active_masker is not None:
            output = {
                k: active_masker.unmask(v) if isinstance(v, str) else v for k, v in output.items()
            }
        return output, latency

    def _call_validated(
        self, backend: Backend, req: LLMRequest, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Call once; on invalid output retry once with "Return valid JSON only" (AGT-09)."""
        for attempt in range(2):
            msgs = (
                messages
                if attempt == 0
                else [*messages, {"role": "user", "content": RETRY_INSTRUCTION}]
            )
            try:
                raw = backend.generate(req, msgs)
            except BackendError as exc:
                log.warning(
                    "LLM backend %s failed for %s: %s",
                    backend.name.value,
                    req.task.value,
                    _redact(str(exc)),
                )
                raise _Fallback(exc.reason) from None
            except (TimeoutError, ConnectionError, OSError) as exc:
                log.warning(
                    "LLM backend %s unreachable for %s: %s",
                    backend.name.value,
                    req.task.value,
                    type(exc).__name__,
                )
                raise _Fallback(
                    "timeout" if isinstance(exc, TimeoutError) else "connection"
                ) from None
            try:
                return validate_output(raw, req.output_schema)
            except LLMOutputInvalid:
                log.info(
                    "LLM backend %s gave invalid output (attempt %d)",
                    backend.name.value,
                    attempt + 1,
                )
        raise _Fallback("invalid_json")

    def _masker_for(self, req: LLMRequest) -> Masker:
        names = {
            str(v): kind
            for key, kind in _NAME_KEYS.items()
            if isinstance(v := req.payload.get(key), str) and v
        }
        business = req.payload.get("business_name")
        return Masker(names, business_name=str(business) if business else None)

    def _count_remote(self, req: LLMRequest, backend: Backend) -> None:
        today = get_clock().today()
        self._remote_calls[today] = self._remote_calls.get(today, 0) + 1
        self.events(
            EventType.LLM_REMOTE_CALL,
            {
                "task": req.task.value,
                "data_class": req.data_class.name,
                "model": backend.model,
                "cached": False,
            },
        )

    # status --------------------------------------------------------------------------------

    def remote_calls_today(self) -> int:
        return self._remote_calls.get(get_clock().today(), 0)

    def status(self) -> dict[str, Any]:
        configured = [b for n in _MODE_ORDER[self.mode] if (b := self.backends.get(n))]
        available = [b.name.value for b in configured] + [LLMBackend.MOCK.value]
        models = {b.name.value: b.model for b in configured}
        models[LLMBackend.MOCK.value] = self.mock.model
        return {
            "mode": self.mode.upper(),
            "available": available,
            "models": models,
            "remote_calls_today": self.remote_calls_today(),
            "remote_ai": any(b.remote for b in configured),
            "remote_tasks": policy.remote_tasks(),
        }


class _Fallback(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
