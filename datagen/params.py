"""Every tunable number of the Rina's Bakery generator (PRD/10 §3, §5).

Money is int cents; rates and factors are ``Decimal``. No floats anywhere: random draws are
integer-based (``rng.randint``) and converted to ``Decimal`` where a factor is needed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any

from contracts.clock import DEFAULT_AS_OF

DEFAULT_SEED = 20261014


@dataclass(frozen=True)
class ScenarioParams:
    """Generator parameters. Defaults reproduce PRD/10 §3; ``pos_base_scale`` is what the
    tuning loop moves (PRD/10 §5)."""

    seed: int = DEFAULT_SEED
    as_of: date = DEFAULT_AS_OF
    history_days: int = 183  # history_start = as_of - 183 days; last history day = as_of - 1

    # POS (§3): weekday base in cents, Monday = 0 ... Sunday = 6
    pos_base_cents: tuple[int, ...] = (30000, 30000, 30000, 30000, 38000, 47000, 42000)
    pos_base_scale: Decimal = Decimal("1.00")
    pos_noise_pct: int = 10  # day total x U(1 - 10%, 1 + 10%)
    pos_txn_min: int = 25
    pos_txn_max: int = 60
    sale_min_cents: int = 400
    sale_max_cents: int = 2500
    cash_share_pct: int = 40  # by count

    # Bank streams (§3)
    card_fee_rate: Decimal = Decimal("0.015")
    cash_cogs_ratio: Decimal = Decimal("0.30")  # of the covered days' POS sales (see README)
    cash_cogs_noise_pct: int = 4
    rent_cents: int = 120000
    wages_cents: int = 220000
    wages_first: date = date(2026, 4, 23)
    wages_every_days: int = 14
    power_cents: int = 21000
    internet_cents: int = 5000
    utilities_day: int = 20
    bank_fee_cents: int = 1200
    bank_fee_day: int = 28

    # Documents (§3)
    catering_min_cents: int = 60000
    catering_max_cents: int = 190000
    cafe_min_cents: int = 15000
    cafe_max_cents: int = 40000
    gm_bill_min_cents: int = 70000
    gm_bill_max_cents: int = 110000
    boxco_bill_min_cents: int = 40000
    boxco_bill_max_cents: int = 70000

    # Balance targeting (§5)
    closing_bank_cents: int = 190000
    closing_till_cents: int = 30000
    opening_till_cents: int = 20000
    opening_equipment_cents: int = 800000
    min_balance_cents: int = 30000
    equipment_headroom_cents: int = 150000
    equipment_step_cents: int = 50000

    # Tuning loop (§5)
    tuning_margin_cents: int = 20000
    # S4 also needs "MCA most expensive" (PRD/07 FIN-04), so the BORROW need after DELAY must
    # reach the MCA minimum (500.00, data/products.json) even before the owner confirms the
    # S1/S5/S7 bank lines (they leave the ledger ~200.40 above the bank): 500.00 + 200.00.
    s4_min_borrow_cents: int = 70000
    tuning_step: Decimal = Decimal("0.02")
    tuning_max_iter: int = 20

    extra: dict[str, str] = field(default_factory=dict)

    def with_scale(self, scale: Decimal) -> ScenarioParams:
        return replace(self, pos_base_scale=scale)

    def to_json(self) -> dict[str, Any]:
        """JSON-safe dict (Decimal and date as strings, tuples as lists)."""
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if isinstance(value, (Decimal, date)):
                out[key] = str(value)
            elif isinstance(value, tuple):
                out[key] = list(value)
            else:
                out[key] = value
        return out


DEFAULT_PARAMS = ScenarioParams()
