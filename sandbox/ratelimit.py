"""Per-token write rate limit per sub-app over a rolling 60 seconds (SBX-08; CTL-LIM-06).

Idempotent replays count too. The time source is injectable so tests are deterministic."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from sandbox.errors import SandboxHTTPError
from sandbox.settings import get_settings

WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self, time_source: Callable[[], float] = time.monotonic):
        self.time_source = time_source
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, token: str, subapp: str, limit: int | None = None) -> None:
        """Record one write; raise 429 RATE_LIMITED with Retry-After when over the limit."""
        limit = limit if limit is not None else get_settings().write_rate_per_min
        now = self.time_source()
        with self._lock:
            q = self._events.setdefault((token, subapp), deque())
            while q and now - q[0] >= WINDOW_SECONDS:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, math.ceil(WINDOW_SECONDS - (now - q[0])))
                raise SandboxHTTPError(
                    "RATE_LIMITED",
                    f"more than {limit} writes per minute",
                    details={"limit": limit},
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


LIMITER = RateLimiter()
