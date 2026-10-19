"""CLI entry: generate Rina's Bakery and write every WP8 output (PRD/10).

    python -m datagen.generate --seed 20261014 --as-of 2026-10-14 [--check]

Writes ``var/seed/bundle.json`` (``SeedBundle``), ``data/events.json``,
``data/expected.json`` and ``data/receipts/*.png`` + ``*.ocr.json``, then prints a summary.
The tuning loop always runs: it moves ``pos_base_scale`` in 2% steps (at most 20 times)
until the minimal BASE forecast shows S2 and S4; ``--check`` exits 1 if it cannot.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from contracts.canonical import canonical_json, sha256_hex
from contracts.dto_books import JournalLineDTO, RecurringObligation
from contracts.dto_planning import EventUplift
from contracts.seed import SeedBundle
from datagen import balance, bank, business, documents, pos, receipts, scenarios
from datagen.expected import expected_values, forecast_check
from datagen.params import DEFAULT_PARAMS, ScenarioParams


@dataclass(frozen=True)
class Generated:
    bundle: SeedBundle
    params: ScenarioParams
    tuning: dict[str, Any]

    @property
    def bundle_sha256(self) -> str:
        return bundle_hash(self.bundle)

    def expected(self) -> dict[str, Any]:
        return expected_values(self.bundle, self.params.to_json(), self.tuning)

    def events_json(self) -> dict[str, Any]:
        return {
            "as_of": self.bundle.as_of.isoformat(),
            "events": [
                {
                    "name": e.name,
                    "start": e.start.isoformat(),
                    "end": e.end.isoformat(),
                    "factor": str(e.factor),
                }
                for e in self.bundle.events
            ],
        }


def bundle_hash(bundle: SeedBundle) -> str:
    """SHA-256 of the canonical JSON of the bundle (line endings never matter)."""
    return sha256_hex(canonical_json(bundle.model_dump(mode="json")))


def _recurring(params: ScenarioParams, start: date) -> list[RecurringObligation]:
    def first_monthly(day: int) -> date:
        d = start
        while d.day != day:
            d += timedelta(days=1)
        return d

    def monthly(name: str, category: str, cents: int, day: int) -> RecurringObligation:
        return RecurringObligation(
            name=name,
            category=category,
            amount_cents=cents,
            first_date=first_monthly(day),
            schedule="monthly",
            day_of_month=day,
            every_days=None,
            end_date=None,
            essential=True,
        )

    return [
        monthly("Rent - Maple Properties", "6000", params.rent_cents, 1),
        RecurringObligation(
            name="Payroll",
            category="6100",
            amount_cents=params.wages_cents,
            first_date=params.wages_first,
            schedule="every_n_days",
            day_of_month=None,
            every_days=params.wages_every_days,
            end_date=None,
            essential=True,
        ),
        monthly("City Power Co", "6200", params.power_cents, params.utilities_day),
        monthly("Netlink Internet", "6200", params.internet_cents, params.utilities_day),
    ]


def build(params: ScenarioParams = DEFAULT_PARAMS) -> SeedBundle:
    """Generate the whole bundle from ``params`` (one seeded RNG, no wall clock)."""
    rng = random.Random(params.seed)
    start, end = balance.history_bounds(params)

    sales = pos.generate_pos(rng, params, start, end)
    sales, pos_note_id = scenarios.add_pos_note(sales)
    totals = pos.daily_totals(sales)
    sc = scenarios.build()

    inv_specs = documents.number_invoices(documents.history_invoices(rng, params) + sc.invoices)
    hist_bills = documents.history_bills(rng, params)
    flows = (
        bank.card_settlements(totals, params, start, end)
        + bank.cash_deposits(totals, params, start, end)
        + bank.cash_cogs(rng, totals, params, start, end)
        + bank.recurring(params, start, end)
        + documents.invoice_payment_flows(inv_specs)
        + documents.bill_payment_flows(hist_bills)
        + [documents.prehistory_golden_payment(rng, params)]
        + sc.flows
        + receipts.receipt_flows()
    )
    flows += balance.equipment_purchases(flows, params, start, end)
    opening = balance.opening_balance(flows, params)
    txns, tags = balance.finalize(flows, opening)

    by_id = {t.id: t for t in txns}

    def first_paid(tag: str) -> date | None:
        dated = [by_id[i].date for i in tags.get(tag, [])]
        return min(dated) if dated else None

    paid_on = {
        business.PAYEE_GOLDEN: first_paid(f"payee:{business.PAYEE_GOLDEN}"),
        business.PAYEE_BOXCO: first_paid(f"payee:{business.PAYEE_BOXCO}"),
        business.PAYEE_MAPLE: first_paid("rent"),
    }
    if paid_on[business.PAYEE_GOLDEN] != business.GOLDEN_FIRST_PAID_ON:
        raise ValueError("Golden Mills first payment must be on 2026-05-02 (PRD/10 §2)")

    invoices = documents.invoice_dtos(inv_specs)
    bills = sorted(hist_bills + sc.bills, key=lambda b: (b.issue_date, b.number))
    receipt_ids = {r.name: r.id for r in receipts.RECEIPTS}
    invoice_ids = {
        k: [documents.invoice_id(n) for n in nums] for k, nums in sc.invoice_numbers.items()
    }
    scenario_ids = {
        "S1": tags["S1"],
        "S2": sc.bill_ids["S2"],
        "S3": invoice_ids["S3"],
        "S4": sc.bill_ids["S4"],
        "S5": tags["S5"],
        "S6": sc.bill_ids["S6"],
        "S7": [
            tags["S7"][0],
            pos_note_id,
            receipt_ids["receipt_injected"],
            sc.bill_ids["S6"][0],
        ],
        "S8": [receipt_ids["receipt_blurry"]],
        "S9": invoice_ids["S9"],
        "S10": sc.bill_ids["S10"],
    }
    opening_lines = [
        JournalLineDTO(account_code="1000", debit_cents=opening, credit_cents=0),
        JournalLineDTO(account_code="1010", debit_cents=params.opening_till_cents, credit_cents=0),
        JournalLineDTO(
            account_code="1500", debit_cents=params.opening_equipment_cents, credit_cents=0
        ),
        JournalLineDTO(
            account_code="3000",
            debit_cents=0,
            credit_cents=opening + params.opening_till_cents + params.opening_equipment_cents,
        ),
    ]
    return SeedBundle(
        version="1",
        seed=params.seed,
        as_of=params.as_of,
        business=dict(business.BUSINESS),
        history_start=start,
        opening_balances=opening_lines,
        bank_accounts=business.bank_accounts(params.closing_bank_cents),
        bank_transactions=txns,
        bank_payees=business.bank_payees(paid_on),
        pos_sales=sales,
        counterparties=business.counterparties(),
        payees=business.payees(paid_on),
        invoices=invoices,
        bills=bills,
        receipts=receipts.bundle_rows(),
        recurring=_recurring(params, start),
        events=[
            EventUplift(name=n, start=s, end=e, factor=Decimal(f))
            for n, s, e, f in scenarios.EVENTS
        ],
        scenario_ids=scenario_ids,
    )


def tune(params: ScenarioParams = DEFAULT_PARAMS) -> Generated:
    """Tuning loop (PRD/10 §5): regenerate with pos_base_scale +/- tuning_step until the
    minimal BASE forecast shows S2 and S4 with ``tuning_margin_cents`` to spare."""
    scale = params.pos_base_scale
    steps: list[dict[str, Any]] = []
    for _ in range(params.tuning_max_iter + 1):
        current = params.with_scale(scale)
        bundle = build(current)
        chk = forecast_check(bundle, params.tuning_margin_cents, params.s4_min_borrow_cents)
        steps.append(
            {
                "pos_base_scale": str(scale),
                "s2_ok": chk["s2_ok"],
                "s4_ok": chk["s4_ok"],
                "direction": chk["direction"],
            }
        )
        if chk["s2_ok"] and chk["s4_ok"] and chk["direction"] == 0:
            return Generated(bundle, current, {"converged": True, "steps": steps})
        if chk["direction"] == 0:
            break
        scale = (scale + params.tuning_step * chk["direction"]).quantize(Decimal("0.01"))
    return Generated(bundle, current, {"converged": False, "steps": steps})


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")  # LF on Windows too (ARC-10)


def write_outputs(gen: Generated, var_dir: Path, data_dir: Path) -> dict[str, Path]:
    """Write bundle, events, expected values and receipts. Returns the paths."""
    paths = {
        "bundle": var_dir / "seed" / "bundle.json",
        "events": data_dir / "events.json",
        "expected": data_dir / "expected.json",
        "receipts": data_dir / "receipts",
    }
    _write_json(paths["bundle"], gen.bundle.model_dump(mode="json"))
    _write_json(paths["events"], gen.events_json())
    _write_json(paths["expected"], gen.expected())
    receipts.write_receipts(paths["receipts"])
    return paths


def summary(gen: Generated) -> str:
    b = gen.bundle
    exp = gen.expected()
    chk = exp["forecast_check"]
    equipment = [t for t in b.bank_transactions if t.description.startswith("RIVERTON REST")]
    equipment_cents = -sum(t.amount_cents for t in equipment)
    open_invoices = sum(i.paid_cents == 0 for i in b.invoices)
    open_bills = sum(x.paid_cents == 0 for x in b.bills)
    gaps = (chk["gap_cents"], chk["gap_after_collect_cents"], chk["gap_after_delay_cents"])
    tuning = (
        f"converged={gen.tuning['converged']} scale={gen.params.pos_base_scale} "
        f"iterations={len(gen.tuning['steps'])}"
    )
    rows = [
        ("history", f"{b.history_start} .. {b.as_of - timedelta(days=1)}"),
        ("pos_sales", str(len(b.pos_sales))),
        ("bank_transactions", str(len(b.bank_transactions))),
        ("equipment purchases", f"{len(equipment)} ({equipment_cents} cents)"),
        ("invoices / open", f"{len(b.invoices)} / {open_invoices}"),
        ("bills / open", f"{len(b.bills)} / {open_bills}"),
        ("receipts", str(len(b.receipts))),
        ("opening bank (cents)", str(b.opening_balances[0].debit_cents)),
        ("closing bank / till", f"{exp['closing_bank_cents']} / {exp['closing_till_cents']}"),
        ("BASE closings W1..W5", str(chk["base_closings_cents"][:5])),
        ("first negative week (BASE)", str(chk["first_negative_week_base"])),
        ("gap / after collect / delay", " / ".join(str(g) for g in gaps)),
        ("tuning", tuning),
        ("bundle sha256", gen.bundle_sha256),
    ]
    rows += [(k, ", ".join(v)) for k, v in b.scenario_ids.items()]
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k.ljust(width)}  {v}" for k, v in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datagen.generate", description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_PARAMS.seed)
    parser.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_PARAMS.as_of)
    parser.add_argument("--check", action="store_true", help="exit 1 unless S2 and S4 hold")
    parser.add_argument("--var-dir", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.as_of != DEFAULT_PARAMS.as_of:
        print("warning: scenario dates are fixed for AS_OF 2026-10-14", file=sys.stderr)

    from infra.paths import DATA_DIR, var_dir

    params = ScenarioParams(seed=args.seed, as_of=args.as_of)
    gen = tune(params)
    write_outputs(gen, args.var_dir or var_dir(), args.data_dir or DATA_DIR)
    print(summary(gen))
    if args.check and not gen.tuning["converged"]:
        print("tuning did not reach S2 and S4", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
