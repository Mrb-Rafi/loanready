"""Balance targeting (PRD/10 §5): closing bank 1,900.00 at AS_OF, running balance never
below 300.00, opening balance derived.

The closing balance is pinned, so the balance at the end of day t is
``b(t) = closing - sum(net flows after day t)``. Adding an inflow (the PRD's
"OWNER CAPITAL INTRODUCED") can only *lower* earlier balances; the only way to lift a
low point is an extra outflow **after** it. With the PRD's default numbers the bakery is
cash-generative, so without that the derived opening balance is negative. The generator
therefore inserts one-off equipment purchases (posted to 1500 Equipment, investing cash
flow) the day after each low point: walking backwards from AS_OF, whenever b(t-1) would
fall below the floor, buy equipment on day t for ``roundup(floor + headroom - b(t-1),
step)``. One backward pass satisfies every day, and the opening balance is then
``b(history_start - 1) >= floor``. See README "PRD gaps".
"""

from __future__ import annotations

from datetime import date, timedelta

from contracts.sandbox_api import BankTxnOut
from datagen.bank import RANK_EQUIPMENT, Flow, days
from datagen.business import ACCOUNT_ID, ID_TAG
from datagen.params import ScenarioParams

EQUIPMENT_ITEMS = (
    "DECK OVEN",
    "SPIRAL MIXER",
    "DISPLAY FRIDGE",
    "PROOFING CABINET",
    "ESPRESSO MACHINE",
    "STAINLESS WORK TABLES",
    "DOUGH SHEETER",
    "BLAST CHILLER",
)
EQUIPMENT_VENDOR = "Riverton Restaurant Supply"


def order_key(f: Flow) -> tuple[date, int, int, str, int, str]:
    """Inside a day: inflows, then outflows; then stream rank, description, amount."""
    direction = 0 if f.amount_cents > 0 else 1
    return (f.day, direction, f.rank, f.description, f.amount_cents, f.tag or "")


def _roundup(cents: int, step: int) -> int:
    return -(-cents // step) * step


def equipment_purchases(
    flows: list[Flow], params: ScenarioParams, start: date, end: date
) -> list[Flow]:
    """Outflows that keep every end-of-day balance (and the opening) >= min_balance_cents.

    b_after(end) = closing; b_after(t - 1) = b_after(t) - net(t). Inflows are ordered first
    inside a day and the purchase last, so end-of-day balances and the opening bound every
    intermediate ``balance_after_cents`` from below.
    """
    net: dict[date, int] = {}
    for f in flows:
        net[f.day] = net.get(f.day, 0) + f.amount_cents
    purchases: list[Flow] = []
    b_after = params.closing_bank_cents
    if b_after < params.min_balance_cents:
        raise ValueError("closing balance below the floor")
    for t in reversed(days(start, end)):
        b_before = b_after - net.get(t, 0)
        if b_before < params.min_balance_cents:
            amount = _roundup(
                params.min_balance_cents + params.equipment_headroom_cents - b_before,
                params.equipment_step_cents,
            )
            item = EQUIPMENT_ITEMS[len(purchases) % len(EQUIPMENT_ITEMS)]
            purchases.append(
                Flow(
                    t,
                    -amount,
                    f"RIVERTON RESTAURANT SUPPLY - {item}",
                    EQUIPMENT_VENDOR,
                    RANK_EQUIPMENT,
                    "equipment",
                )
            )
            b_before += amount
        b_after = b_before
    return sorted(purchases, key=order_key)


def opening_balance(flows: list[Flow], params: ScenarioParams) -> int:
    """opening = closing target - sum(history flows)."""
    return params.closing_bank_cents - sum(f.amount_cents for f in flows)


def finalize(
    flows: list[Flow], opening_cents: int
) -> tuple[list[BankTxnOut], dict[str, list[str]]]:
    """Order, number (``txn_rina_<n>``) and attach running balances. Returns the txns and
    a map tag -> txn ids (in order)."""
    txns: list[BankTxnOut] = []
    tags: dict[str, list[str]] = {}
    balance = opening_cents
    for n, f in enumerate(sorted(flows, key=order_key), start=1):
        balance += f.amount_cents
        txn_id = f"txn_{ID_TAG}_{n:05d}"
        txns.append(
            BankTxnOut(
                id=txn_id,
                account_id=ACCOUNT_ID,
                date=f.day,
                amount_cents=f.amount_cents,
                description=f.description,
                counterparty=f.counterparty,
                kind="seed",
                payment_ref=None,
                balance_after_cents=balance,
            )
        )
        if f.tag:
            tags.setdefault(f.tag, []).append(txn_id)
    return txns, tags


def history_bounds(params: ScenarioParams) -> tuple[date, date]:
    """(history_start, last history day) = (as_of - history_days, as_of - 1)."""
    return params.as_of - timedelta(days=params.history_days), params.as_of - timedelta(days=1)
