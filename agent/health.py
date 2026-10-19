"""Backend availability with a small circuit breaker (PRD/09 §2.3).

- Ollama is available when ``GET {OLLAMA_HOST}/api/tags`` answers within 2 s and lists
  ``OLLAMA_MODEL``.
- OpenAI is available when a key and a model are set and the budget guard has calls and
  dollars left.
- Results are cached for 60 s; a backend that fails 3 times in a row is off for 5 minutes,
  so a slow laptop does not hang the UI.

The timer is ``time.monotonic`` (a duration, not a business date), injectable for tests.
``python -m agent.health`` prints what this PC has (and the OpenAI models the key can use).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

from contracts.enums import LLMBackend

CACHE_S = 60.0
FAILS_TO_TRIP = 3
OFF_S = 300.0
TAGS_TIMEOUT_S = 2.0


def ollama_check(host: str, model: str) -> Callable[[], bool]:
    def check() -> bool:
        import httpx

        try:
            r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=TAGS_TIMEOUT_S)
            names = {m.get("name", "") for m in r.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return False
        return model in names or f"{model}:latest" in names

    return check


def openai_check(backend: Any) -> Callable[[], bool]:
    def check() -> bool:
        if not getattr(backend, "model", ""):
            return False
        try:
            backend.budget.reserve(1, 1)
        except Exception:
            return False
        return True

    return check


class Health:
    def __init__(
        self,
        checks: dict[LLMBackend, Callable[[], bool]] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.checks = dict(checks or {})
        self.now = now
        self._cached: dict[LLMBackend, tuple[float, bool]] = {}
        self._fails: dict[LLMBackend, int] = {}
        self._off_until: dict[LLMBackend, float] = {}

    def available(self, name: LLMBackend) -> bool:
        if name == LLMBackend.MOCK:
            return True
        t = self.now()
        if self._off_until.get(name, 0.0) > t:
            return False
        check = self.checks.get(name)
        if check is None:
            return False
        hit = self._cached.get(name)
        if hit is not None and t - hit[0] < CACHE_S:
            return hit[1]
        ok = bool(check())
        self._cached[name] = (t, ok)
        return ok

    def record_success(self, name: LLMBackend) -> None:
        self._fails[name] = 0

    def record_failure(self, name: LLMBackend) -> None:
        self._fails[name] = self._fails.get(name, 0) + 1
        if self._fails[name] >= FAILS_TO_TRIP:
            self._off_until[name] = self.now() + OFF_S
            self._fails[name] = 0
            self._cached.pop(name, None)

    def snapshot(self) -> dict[str, Any]:
        t = self.now()
        return {
            n.value: {
                "available": self.available(n),
                "circuit_open": self._off_until.get(n, 0.0) > t,
            }
            for n in self.checks
        }


def main(argv: list[str] | None = None) -> int:
    """Print this PC's backend status; with an OpenAI key, list small chat models."""
    from agent.llm import LLM

    llm = LLM()
    st = llm.status()
    for key in ("mode", "available", "models", "routes", "remote_budget"):
        print(f"{key}: {st.get(key)}")
    from infra.settings import get_settings

    api_key = get_settings().openai_api_key.get_secret_value()
    if api_key:  # list models even before OPENAI_MODEL is chosen
        try:
            from openai import OpenAI

            ids = sorted(m.id for m in OpenAI(api_key=api_key, timeout=15).models.list())
        except Exception as exc:  # report, never show the key
            print(f"openai: cannot list models ({type(exc).__name__})")
        else:
            small = [m for m in ids if any(k in m for k in ("nano", "mini", "luna"))]
            print("openai small models:", ", ".join(small) or "(none matched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
