# datagen (WP8): synthetic Rina's Bakery

Generates six months of fictional records for "Rina's Bakery" (Riverton, USD), AS_OF
2026-10-14, history 2026-04-14 to 2026-10-13, plus the scenario data S1 to S10
(PRD/10). Depends only on `contracts` (+ Pillow); `infra.paths` is used by the CLI only.

## Run

```
python tasks.py gen        # = python -m datagen.generate --seed 20261014 --as-of 2026-10-14
python -m datagen.generate --check   # exit 1 if tuning cannot reach S2 and S4
```

Outputs: `var/seed/bundle.json` (`contracts.seed.SeedBundle`), `data/events.json`,
`data/expected.json`, `data/receipts/receipt_{clean,blurry,injected,rotated}.png` + `.ocr.json`.
A summary table is printed (counts, opening/closing balances, BASE weeks 1-5, gaps,
tuning result, bundle hash, scenario ids).

## Public API

`datagen.generate`: `build(params) -> SeedBundle`, `tune(params) -> Generated`
(`.bundle`, `.params`, `.tuning`, `.expected()`, `.bundle_sha256`),
`write_outputs(gen, var_dir, data_dir)`, `bundle_hash(bundle)`, `main()`.
`datagen.expected.forecast_check(bundle)` is the minimal BASE forecast used by the tuning
loop. Ids are readable and stable: `txn_rina_00001`, `pos_rina_000001`, `inv_rina_1043`,
`bil_rina_2207`, `rcp_rina_blurry`.

## Test alone

```
python tasks.py test datagen       # T-DGN-01..06, 44 tests, about 10 s
```

## Modules

| Module | What it does |
|---|---|
| `params.py` | `ScenarioParams` (every tunable number), `DEFAULT_PARAMS` |
| `business.py` | profile, 9 counterparties, 3 payees (app master = MockBank registry) |
| `pos.py` | daily POS sales from a fixed menu; weekday seasonality, noise, 40% cash |
| `bank.py` | card settlements (net of 1.5% fee), Monday cash deposits, Tue/Fri cash COGS, rent, payroll, utilities, bank fee |
| `documents.py` | invoices (from the lateness histories) and supplier bills, bank payments |
| `scenarios.py` | S1-S10 rows and documents; `EVENTS` for `data/events.json` |
| `balance.py` | closing-balance targeting, equipment purchases, running balances |
| `receipts.py` | 600x900 PNGs (built-in bitmap font x2) and OCR sidecars |
| `expected.py` | `data/expected.json` and the minimal BASE forecast (PRD/06 rules) |
| `generate.py` | CLI, tuning loop, writers, summary |

SHOULD items, built after WP3/WP4/WP6 on the team lead's go: S9 (INV-0998 Cafe Lumen
600.00 due 2026-07-31, open, 75 days overdue; `expected.json` `S9` = bucket 61-90, ECL
150.00) and the rotated receipt (7 degrees, white fill, mean_conf 80 -> AUTO about 0.80).

## PRD gaps and the choices made (for the team lead)

1. **Balance targeting cannot work as PRD/10 §5 states.** The closing balance is pinned
   (1,900.00), so balance(t) = closing - sum(flows after t). An "OWNER CAPITAL
   INTRODUCED" inflow can only *lower* earlier balances. With the §3 default numbers the
   bakery is cash-generative, so the derived opening is negative. Choice: one-off
   equipment purchases `RIVERTON RESTAURANT SUPPLY - <ITEM>` (counterparty "Riverton
   Restaurant Supply") are inserted the day after each low point, sized to floor + 1,500
   rounded up to 500. With the defaults this gives 3 purchases, 6,500.00 in total; the
   opening bank balance is 3,547.70 and the lowest running balance 374.82. No capital
   injection is ever needed. **WP3 needs a rule mapping this text to 1500 Equipment**
   (investing cash flow, BKS-17); without it the three rows land in the CATEGORY confirm
   queue (they never create PERSONAL items, so S1 stays at exactly 3).
2. **Cash COGS ratio.** §3 says "about 15% of the previous 3-4 days' sales each
   (overall about 0.30)"; 15% twice a week gives 0.15 overall. Choice: 30% of the covered
   days, so the overall ratio is about 0.30 (0.3080 over the last 12 weeks).
3. **Payroll dates.** Every 14 days from 2026-04-23 gives 2026-10-22 and 2026-11-05, not
   10-23 and 11-06 as §3 says; both still fall in W2 and W4, so S2 is unaffected.
4. **Invoice history.** The counterparty lateness histories define the paid invoices: one
   paid invoice per history value, paid due + that value (Bright 5, Harbor 4, Lumen 4,
   Daily Grind 4). So cafe invoices are not weekly as §3 says. Open, not-yet-due invoices:
   Cafe Lumen INV-1052, Daily Grind INV-1053, Corner Deli INV-1054. Exactly two open
   invoices are overdue by at most 60 days (S3); INV-0998 (S9) is 75 days overdue.
5. **Pre-history Golden Mills bill.** `pye_golden.first_paid_on` is 2026-05-02, which
   under net 30 means a bill issued 2026-04-02, before history_start. Only its payment
   (`GOLDEN MILLS BILL-2012`) is in the bank feed; the bill itself is not in `bills`
   (WP3 will categorise the payment as 5000 by rule R-FLOUR).
6. **Till target.** Monday deposits sweep the till down to the 200.00 float; the last
   deposit (2026-10-12) is set so the till closes at exactly 300.00.
   `closing_till_cents` is the generated till before any receipt postings. Every receipt
   is also a GRAIN DEPOT card purchase in the bank feed (same date and amount), so WP3
   links it as evidence (BKS-14) and the till is not touched.
7. **Tuning.** The loop needs `pos_base_scale` 0.90 (6 iterations) to leave a gap after
   DELAY with a 200.00 margin; base weekly sales are therefore about 10% below the §3
   weekday bases. BASE closings W1-W5: 2,677.62 / 2,310.24 / -4,156.49 / -777.59 /
   860.03; gaps 4,200 / after collect 1,100 / after delay 400.
8. Median lateness is floored to whole days when converted to a date (as
   `FakeForecast` does). With the defaults no result depends on it.
9. Injected receipt expected status is AUTO (confidence 0.90 by BKS-25); the injection is
   flagged, not blocked.

## Proposed contract changes

None.

**PC A: read `HANDOFF_NOTES.md` in this folder first** (integration steps, decisions, changes).
