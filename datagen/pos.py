"""Daily POS sales with weekday seasonality and noise (PRD/10 §3).

Day target = weekday base x ``pos_base_scale`` x U(0.9, 1.1). The day is filled with
25-60 sales of 4.00-25.00 each, built from a fixed menu, so every sale's amount equals
the sum of ``qty x unit_cents`` and the day total is the sum of its sales (it lands within
one sale of the target). Tender is cash for about 40% of sales by count.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from contracts.money import to_cents
from contracts.sandbox_api import PosItem, PosSaleOut
from datagen.business import ID_TAG
from datagen.params import ScenarioParams

# name, unit price in cents (fictional menu)
MENU: tuple[tuple[str, int], ...] = (
    ("Croissant", 350),
    ("Pain au chocolat", 400),
    ("Baguette", 450),
    ("Sourdough loaf", 750),
    ("Rye loaf", 650),
    ("Cinnamon roll", 425),
    ("Blueberry muffin", 375),
    ("Cookie", 250),
    ("Bagel", 300),
    ("Cake slice", 550),
    ("Fruit tart", 600),
    ("Coffee", 300),
    ("Latte", 450),
    ("Celebration cake", 2200),
)
OPEN_MINUTE = 7 * 60  # 07:00 UTC
CLOSE_MINUTE = 18 * 60  # 18:00 UTC


def day_target_cents(rng: random.Random, params: ScenarioParams, day: date) -> int:
    """target = base(weekday) x scale x (1 + u), u uniform in [-noise, +noise] (0.1% steps)."""
    span = params.pos_noise_pct * 10
    factor = Decimal(1000 + rng.randint(-span, span)) / Decimal(1000)
    base = Decimal(params.pos_base_cents[day.weekday()]) / Decimal(100)
    return to_cents(base * params.pos_base_scale * factor)


def _sale_items(rng: random.Random, target_cents: int, params: ScenarioParams) -> list[PosItem]:
    picked: dict[str, int] = {}
    prices = dict(MENU)
    total = 0
    while total < max(target_cents, params.sale_min_cents):
        room = params.sale_max_cents - total
        options = [m for m in MENU if m[1] <= room]
        if not options:
            break
        name, price = options[rng.randrange(len(options))]
        picked[name] = picked.get(name, 0) + 1
        total += price
    return [PosItem(name=n, qty=q, unit_cents=prices[n]) for n, q in sorted(picked.items())]


def generate_day(
    rng: random.Random, params: ScenarioParams, day: date, start_index: int
) -> list[PosSaleOut]:
    """All sales of one day, in time order, ids ``pos_rina_<n>`` from ``start_index``."""
    target = day_target_cents(rng, params, day)
    lo = max(params.pos_txn_min, -(-target // 1800))
    hi = min(params.pos_txn_max, target // 600)
    n_target = rng.randint(lo, max(lo, hi))
    mean = target // n_target
    amounts: list[list[PosItem]] = []
    total = 0
    while len(amounts) < params.pos_txn_max and (
        total < target or len(amounts) < params.pos_txn_min
    ):
        sale_target = mean * rng.randint(50, 150) // 100
        sale_target = min(max(sale_target, params.sale_min_cents), params.sale_max_cents)
        items = _sale_items(rng, sale_target, params)
        amounts.append(items)
        total += sum(i.qty * i.unit_cents for i in items)
    minutes = sorted(rng.randint(OPEN_MINUTE, CLOSE_MINUTE - 1) for _ in amounts)
    sales: list[PosSaleOut] = []
    for n, (items, minute) in enumerate(zip(amounts, minutes, strict=True)):
        ts = datetime.combine(day, time(0, 0), tzinfo=UTC) + timedelta(
            minutes=minute, seconds=rng.randint(0, 59)
        )
        tender = "cash" if rng.randint(1, 100) <= params.cash_share_pct else "card"
        sales.append(
            PosSaleOut(
                id=f"pos_{ID_TAG}_{start_index + n:06d}",
                ts=ts,
                amount_cents=sum(i.qty * i.unit_cents for i in items),
                tender=tender,
                items=items,
                note=None,
            )
        )
    return sales


def generate_pos(
    rng: random.Random, params: ScenarioParams, start: date, end: date
) -> list[PosSaleOut]:
    """POS sales for every day in [start, end] inclusive, oldest first."""
    sales: list[PosSaleOut] = []
    day = start
    while day <= end:
        sales.extend(generate_day(rng, params, day, len(sales) + 1))
        day += timedelta(days=1)
    return sales


def daily_totals(sales: list[PosSaleOut]) -> dict[date, dict[str, int]]:
    """{day: {"cash": cents, "card": cents}} from the sales list."""
    out: dict[date, dict[str, int]] = {}
    for s in sales:
        bucket = out.setdefault(s.ts.date(), {"cash": 0, "card": 0})
        bucket[s.tender] += s.amount_cents
    return out
