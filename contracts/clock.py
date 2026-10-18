"""Clock (CON-02). The only module allowed to call ``datetime.now()`` (ARC-12).

``get_clock()`` returns the process clock. ``infra.settings`` registers a provider that
builds it from settings (FixedClock in test/demo, SystemClock in dev); tests and the
demo may install one with ``set_clock()``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

DEFAULT_AS_OF = date(2026, 10, 14)
HORIZON_WEEKS = 13


@runtime_checkable
class Clock(Protocol):
    def today(self) -> date: ...  # business date (AS_OF)
    def now(self) -> datetime: ...  # UTC, tz-aware


def _default_start(as_of: date) -> datetime:
    """AS_OF at 09:00 Asia/Dhaka, as UTC (03:00Z)."""
    local = datetime.combine(as_of, time(9, 0), tzinfo=ZoneInfo("Asia/Dhaka"))
    return local.astimezone(UTC)


class FixedClock:
    """Deterministic clock for tests and the headless demo.

    ``now()`` returns ``start + n * tick_seconds`` on the n-th call (n = 0, 1, ...), so
    timestamps are strictly increasing and identical across runs. ``advance()`` moves
    time forward (approval-expiry tests).
    """

    def __init__(self, as_of: date, start: datetime | None = None, tick_seconds: int = 1):
        if start is not None and start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        self._as_of = as_of
        self._start = (start or _default_start(as_of)).astimezone(UTC)
        self._tick = tick_seconds
        self._calls = 0
        self._offset = timedelta(0)

    def today(self) -> date:
        return self._as_of

    def now(self) -> datetime:
        value = self._start + self._offset + timedelta(seconds=self._calls * self._tick)
        self._calls += 1
        return value

    def peek(self) -> datetime:
        """The value the next ``now()`` call would return, without consuming a tick."""
        return self._start + self._offset + timedelta(seconds=self._calls * self._tick)

    def advance(self, seconds: int) -> None:
        self._offset += timedelta(seconds=seconds)

    def set_today(self, as_of: date) -> None:
        self._as_of = as_of


class SystemClock:
    """Dev mode: business date fixed at AS_OF, real UTC wall-clock time."""

    def __init__(self, as_of: date):
        self._as_of = as_of

    def today(self) -> date:
        return self._as_of

    def now(self) -> datetime:
        return datetime.now(UTC)


def wall_clock_now() -> datetime:
    """Real UTC time, for tooling only (freeze, hand-off, assembly timestamps)."""
    return datetime.now(UTC)


_clock: Clock | None = None
_provider: Callable[[], Clock] | None = None


def register_clock_provider(provider: Callable[[], Clock]) -> None:
    """Called by ``infra.settings`` so ``get_clock()`` can build the clock from settings."""
    global _provider
    _provider = provider


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock


def reset_clock() -> None:
    global _clock
    _clock = None


def get_clock() -> Clock:
    """Process clock: FixedClock when LOANREADY_ENV in {test, demo}, else SystemClock.

    Without a registered provider (contracts used alone) a FixedClock at DEFAULT_AS_OF.
    """
    global _clock
    if _clock is None:
        _clock = _provider() if _provider is not None else FixedClock(DEFAULT_AS_OF)
    return _clock


def week_range(k: int, as_of: date) -> tuple[date, date]:
    """Forecast week k (1..13) = [as_of + 1 + 7(k-1), as_of + 7k], inclusive."""
    if not 1 <= k <= HORIZON_WEEKS:
        raise ValueError("k must be 1..13")
    return as_of + timedelta(days=1 + 7 * (k - 1)), as_of + timedelta(days=7 * k)


def week_of(d: date, as_of: date) -> int | None:
    """Forecast week (1..13) containing d, or None if outside the horizon."""
    delta = (d - as_of).days
    if delta < 1 or delta > 7 * HORIZON_WEEKS:
        return None
    return (delta - 1) // 7 + 1


def month_key(d: date) -> str:
    """``"2026-10"``."""
    return f"{d.year:04d}-{d.month:02d}"
