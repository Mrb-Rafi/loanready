"""Remote-model call and cost guard (PRD/09 §6, AGT-13, CTL-AI-07).

Counts calls, tokens and estimated USD per day in ``llm_usage`` (in ``var/llm_cache.db``, so
the count survives restarts). Before each remote call ``reserve()`` refuses when the daily call
cap (``OPENAI_MAX_CALLS``), the daily dollar cap (``OPENAI_MAX_USD``) or the all-time cap
(``OPENAI_TOTAL_MAX_USD``, for prepaid credit; 0 = off) would be exceeded by the call's
**worst case** (estimated prompt tokens plus the full output allowance), so the guard can
never overshoot. ``record()`` then books the real token usage the API reported.

Money: USD is stored as integer micro-dollars; prices are ``Decimal`` USD per million tokens
(``OPENAI_PRICE_IN_PER_MTOK`` / ``OPENAI_PRICE_OUT_PER_MTOK``). With both prices 0 only calls
are counted. Formula: ``usd = in_tokens × price_in / 1e6 + out_tokens × price_out / 1e6``,
rounded up to the next micro-dollar (never under-counts). No floats.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from agent.cache import connect
from contracts.clock import get_clock

MICRO = Decimal(1_000_000)
CHARS_PER_TOKEN = 3  # conservative prompt estimate (real tokenisers average about 4)


class BudgetExhausted(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Usage:
    calls: int
    in_tokens: int
    out_tokens: int
    usd_micro: int

    @property
    def usd(self) -> Decimal:
        return Decimal(self.usd_micro) / MICRO


def to_micro(usd: Decimal) -> int:
    return int((usd * MICRO).to_integral_value(rounding=ROUND_CEILING))


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    chars = sum(len(m.get("content", "")) + 16 for m in messages)
    return -(-chars // CHARS_PER_TOKEN)


class Budget:
    def __init__(
        self,
        *,
        max_calls_per_day: int,
        max_usd_per_day: Decimal,
        total_max_usd: Decimal = Decimal(0),
        price_in_per_mtok: Decimal = Decimal(0),
        price_out_per_mtok: Decimal = Decimal(0),
        path: Path | None = None,
    ) -> None:
        self.max_calls = int(max_calls_per_day)
        self.max_day_micro = to_micro(Decimal(max_usd_per_day))
        self.max_total_micro = to_micro(Decimal(total_max_usd))
        self.price_in = Decimal(price_in_per_mtok)
        self.price_out = Decimal(price_out_per_mtok)
        self.path = path
        self._mem: dict[str, list[int]] = {}
        if path is not None:
            with closing(connect(path)) as conn, conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS llm_usage (day TEXT PRIMARY KEY, calls INTEGER, "
                    "in_tokens INTEGER, out_tokens INTEGER, usd_micro INTEGER)"
                )

    @classmethod
    def from_settings(cls, settings: Any, path: Path | None) -> Budget:
        return cls(
            max_calls_per_day=settings.openai_max_calls,
            max_usd_per_day=settings.openai_max_usd,
            total_max_usd=getattr(settings, "openai_total_max_usd", Decimal(0)),
            price_in_per_mtok=settings.openai_price_in_per_mtok,
            price_out_per_mtok=settings.openai_price_out_per_mtok,
            path=path,
        )

    # cost ----------------------------------------------------------------------------------

    def cost_micro(self, in_tokens: int, out_tokens: int) -> int:
        usd = (Decimal(in_tokens) * self.price_in + Decimal(out_tokens) * self.price_out) / MICRO
        return to_micro(usd)

    # storage -------------------------------------------------------------------------------

    def _rows(self) -> dict[str, list[int]]:
        if self.path is None:
            return self._mem
        with closing(connect(self.path)) as conn:
            rows = conn.execute(
                "SELECT day, calls, in_tokens, out_tokens, usd_micro FROM llm_usage"
            )
            return {r[0]: [r[1], r[2], r[3], r[4]] for r in rows}

    def usage(self, day: date | None = None) -> Usage:
        """Usage on ``day`` (default today), or all time with ``day=date.min``."""
        rows = self._rows()
        if day == date.min:
            vals = [sum(v[i] for v in rows.values()) for i in range(4)]
        else:
            vals = rows.get((day or get_clock().today()).isoformat(), [0, 0, 0, 0])
        return Usage(*vals)

    def total(self) -> Usage:
        return self.usage(date.min)

    # guard ---------------------------------------------------------------------------------

    def reserve(self, est_in_tokens: int, max_out_tokens: int) -> None:
        """Raise ``BudgetExhausted`` unless one more call, at its worst case, fits every cap."""
        today, total = self.usage(), self.total()
        worst = self.cost_micro(est_in_tokens, max_out_tokens)
        if today.calls + 1 > self.max_calls:
            raise BudgetExhausted("daily call limit reached")
        if self.max_day_micro and today.usd_micro + worst > self.max_day_micro:
            raise BudgetExhausted("daily USD limit reached")
        if self.max_total_micro and total.usd_micro + worst > self.max_total_micro:
            raise BudgetExhausted("all-time USD limit reached")

    def record(self, in_tokens: int, out_tokens: int) -> int:
        """Book one call's real usage; returns its cost in micro-dollars."""
        cost = self.cost_micro(in_tokens, out_tokens)
        day = get_clock().today().isoformat()
        if self.path is None:
            row = self._mem.setdefault(day, [0, 0, 0, 0])
            for i, v in enumerate((1, in_tokens, out_tokens, cost)):
                row[i] += v
            return cost
        with closing(connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT INTO llm_usage VALUES (?, 1, ?, ?, ?) ON CONFLICT(day) DO UPDATE SET "
                "calls = calls + 1, in_tokens = in_tokens + excluded.in_tokens, "
                "out_tokens = out_tokens + excluded.out_tokens, "
                "usd_micro = usd_micro + excluded.usd_micro",
                (day, in_tokens, out_tokens, cost),
            )
        return cost

    def remaining(self) -> dict[str, Any]:
        today, total = self.usage(), self.total()
        return {
            "calls_today": today.calls,
            "max_calls_per_day": self.max_calls,
            "usd_today": str(today.usd),
            "max_usd_per_day": str(Decimal(self.max_day_micro) / MICRO),
            "usd_total": str(total.usd),
            "max_usd_total": str(Decimal(self.max_total_micro) / MICRO)
            if self.max_total_micro
            else "off",
            "prices_set": bool(self.price_in or self.price_out),
        }
