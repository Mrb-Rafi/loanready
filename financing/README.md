# financing (WP5)

Financing comparison and lender-readiness self-assessment (PRD/07). Pure: inputs in, DTOs
out, no database. Built on PC C.

## Public API

- `financing.Financing` (`contracts.services.FinancingService`): `catalogue()`,
  `compare(request, product_ids=None)`, plus `quote(request, product_id)`, `product(id)`
  and `params()`. Unknown product ids raise `CatalogueMiss` (CTL-FIN-01).
- `financing.Readiness` (`contracts.services.ReadinessService`): `score(inputs)`.
- Display labels for the UI and loan pack: `financing.disclosure.APR_LABEL`, `EAR_LABEL`,
  `SIMPLE_LABEL` (FIN-03).

Modules: `catalogue` (+ `data/products.json`), `regparams` (+ `data/regulatory_params.json`),
`builders` (six product kinds), `irr` (daily IRR by Decimal bisection), `metrics` (APR,
EAR, simple), `disclosure`, `compare` (neutral ranking, flags, notices), `readiness`,
`service`.

## Test alone

```
py -3.12 tasks.py test financing
```

## Notes for PC A (assumptions and PRD ambiguities)

1. **MCA rounding (PRD/07 §3 vs §4).** §3 says the last MCA day absorbs rounding; with
   89 x 13.33 + 13.63 the EAR is 351.96%, outside the ±0.0005 tolerance of the §4 golden
   352.12%. The builder rounds payments cumulatively
   (`pay_k = round(k·F·A/T) − round((k−1)·F·A/T)`): every flow is whole cents, the total
   is exactly F·A (the last day still closes the residual), and the golden 151.19% /
   352.12% match.
2. **MCA explanatory line (FIN-03).** `DisclosureDTO` has no free-text field, so the
   sentence is added to `FinancingComparisonDTO.notices` (and is available from
   `disclosure.mca_explanation`). See the CCR below.
3. **China private-lending cap (SHOULD).** Not built yet. `lpr_1y` stays
   `PLACEHOLDER_UPDATE_BEFORE_SUBMISSION` (read as `None`; never invented); every
   comparison carries a notice that the cap was not checked. `RegParams.cn_cap_apr` is
   ready for the SHOULD step.
4. **Flags outside the PRD list:** `BELOW_MIN_AMOUNT` (bank loan, PRD/07 §3) and
   `ADVANCE_EXCEEDS_NEED` (invoice financing: "the advance may exceed A").
5. **Eligibility.** Amounts outside [min, max] make every kind ineligible (§6 step 2);
   the bank loan also gets `BELOW_MIN_AMOUNT`. Overdraft and MCA are also ineligible when
   the term is longer than the product's `max_term_days`. Fixed-term products (bank loan,
   micro-loan) are never ineligible for term; they get the `TERM_*` flags instead.
   Ineligibility reasons are `"CODE: sentence"` strings.
6. **Readiness.** CV uses the population stdev. Component points are quantized to 0.01
   and the score is `round_half_even` of their sum. Improvement gains are recomputed by
   re-scoring; `hint_action` is an `ActionType` value (`CATEGORISE`, `RECONCILE`,
   `SEPARATE_PERSONAL`, `DRAFT_REMINDER`, `PLAN_CASH`) or `None`.
7. **S4 (FIN-04).** Tested on a representative request (1,200.00 for 21 days, card sales,
   two invoices) because `data/expected.json` (WP8) is not on this PC. WP9 should repeat
   it on the real Rina request.

## Proposed (contract change request)

- **Who:** WP5 (PC C). **What:** add `explanation: str | None = None` to
  `contracts.dto_financing.DisclosureDTO`. **Why:** FIN-03 wants the MCA factor-rate line
  per option; today it rides in `FinancingComparisonDTO.notices`.
  **Backward compatible:** yes (optional field with default). Workaround in use: notices.
