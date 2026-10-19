# planning (WP4): 13-week cash forecast and cash plan

Pure, deterministic functions (no DB, no clock, no randomness; AS_OF comes from the
inputs; all maths in `Decimal`, results in int cents via `contracts.money`). PRD/06.

## Public API

`planning.service.Planner()`: no constructor arguments; implements both
`ForecastService.forecast(inputs) -> ForecastDTO` and
`CashPlanService.plan(inputs, forecast, avg_daily_card_sales_cents) -> CashPlanDTO`.

| Module | What it does |
|---|---|
| `stats.py` | percentile (numpy "linear"), median, mean, coefficient of variation |
| `scenarios.py` | LOW / BASE / HIGH sales and invoice collection dates (PRD/06 §2) |
| `forecast.py` | weekly engine, event uplift, recurring buckets, assumptions (§3) |
| `drivers.py` | crossing week, top 3 outflows, delayed inflows, facts (§4) |
| `gap.py` | base / recommended gap, safety buffer, needed term, going concern (§5) |
| `cashplan.py` | COLLECT -> DELAY -> BORROW what-ifs and Tier 1 drafts (§6) |

## Test alone

```
python tasks.py test planning    # 28 tests, coverage 97%; Rina tests skip if datagen is absent
```

## Verified

- Tiny fixture: all 13 LOW / BASE / HIGH closings of PRD/15 §3.1 exactly; crunch variant
  BASE W2 -450.00 / W3 420.00, LOW -520.00 / -285.00 / 1,050.00, base gap 500.00,
  recommended gap 700.00 (buffer 115.38), term 21 days, COLLECT closes the gap with one
  reminder.
- Seeded bakery (WP8 bundle, inputs built as in ORC-07): BASE closings equal WP8's
  independent `expected.forecast_check` week by week; first negative week 3 (S2); the
  festival bill is the top outflow; plan = COLLECT (INV-1043, INV-1051) -> DELAY
  (BILL-2215 to 2026-11-14) -> BORROW with a positive remainder (S3, S4): gap 4,200.00
  -> 1,100.00 after COLLECT -> 400.00 after DELAY, which goes to BORROW. INV-0998 (S9,
  75 days overdue) is excluded from BASE and LOW collections and from COLLECT.
- LOW <= BASE <= HIGH every week over 100 hypothesis cases (COGS ratio clamped to
  0..0.95, PLN-01); same inputs give an identical DTO and `inputs_hash`.

## Choices PC A should know

1. **Labels use ids.** `ForecastInputs` has bill/invoice ids but no numbers or
   descriptions, so driver labels read "bill bil_rina_2207 from cpt_golden" and
   "invoice inv_rina_1043". The UI/agent may map ids to names; facts carry the amounts.
2. **Delayed inflows follow PRD/06 §4 literally:** every invoice due by the end of the
   crossing week but expected later. On the bakery that is the two S3 invoices plus two
   cafe invoices (INV-1052, INV-1054), so `delayed_count` is 4 (total $3,785.00, not the
   $3,150.00 of the S3 pair). Tell me if the story wants only overdue invoices.
3. **Lateness** percentiles are floored to whole days.
4. **Supplier credit flag:** `ForecastInputs` has no supplier payment terms, so
   `FinancingRequest.supplier_terms_available` is True when any open bill exists
   (the fake uses True too). See "Proposed".
5. COLLECT and DELAY steps appear only when they have items; BORROW step has
   `gap_after = gap_before` and `closed = 0` (the loan is a request, not money yet), as
   in `FakeCashPlan`. Drafts are Tier 1 only (PLN-09).
6. Needed term is 14 days (the minimum) when BASE never goes negative.

## Not built

Supplier price-rise alert (OPTIONAL, PRD-34).

## Proposed

Contract change request (WP4, PC B), optional: add `payment_terms_days: int | None = None`
to `BillForecastItem` so `supplier_terms_available` can follow PRD/06 §6 exactly ("a
supplier whose counterparty has payment_terms_days >= 30"). Backward compatible: yes.

**PC A: read `HANDOFF_NOTES.md` in this folder first** (integration steps, decisions, changes).
