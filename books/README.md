# books (WP3): ledger, statements, reconciliation, receipts

Builds double-entry books from the bank feed, POS sales, invoices, bills and receipts;
produces the P&L (monthly), direct-method cash flow and balance sheet (IFRS for SMEs
labels by default, US GAAP switch for titles and interest classification); reconciles
books to the bank; ages receivables; feeds the forecast and readiness score (PRD/05).

## Public API

`books.service.Books(engine, clock, ids, settings)` implements
`contracts.services.BooksService`. `books.rules.RuleClassifier` is the rules-only
`Classifier` (confidence 0.60, so every guess goes to the owner).

Extra methods for the orchestrator (not in the protocol, see "Proposed" below):

| Method | Use |
|---|---|
| `load_opening_balances(lines, history_start)` | row 14 OPENING entry (seed step 4) |
| `load_recurring(obligations)` | recurring obligations table (seed step 4) |
| `load_bundle(bundle)` | master data + opening balances + recurring from a `SeedBundle` |
| `last_scan_flags()` | `ScanFlag`s of the last `ingest_bank` / `ingest_pos` / `ingest_receipt` call; the orchestrator logs one `UNTRUSTED_INSTRUCTION_IGNORED` per item (BKS-02) |
| `accounting_policies()` | policy note (list of paragraphs) for the loan pack (REG-ACC-12); also `books.policies.policies(framework)` |
| `reverse_entry(entry_id, principal, memo)` | row 16 reversal; original marked REVERSED |
| `receipts()` | stored receipts |

Sync order the orchestrator should use (what `tests/books/conftest.py::sync` does):
`load_bundle` -> `ingest_pos` -> `ingest_bank` -> `categorise_pending(agent.classifier())`
-> `post_ready()` -> `ingest_receipt(path)` for each bundle receipt.

## Test alone

```
python tasks.py test books     # 79 tests, coverage 89%; Rina tests skip if datagen is absent
```

## How it works (choices PC A should know)

1. **Payments come from the bank feed.** `load_master_data` stores every invoice and bill
   with `paid_cents = 0` (status OPEN unless VOID), and posts revenue / expense on the
   issue date (rows 5 and 7). A document becomes PAID when its bank line is matched
   (BKS-04) or `record_customer_payment` / `record_bill_payment` is called. This is what
   makes WP8's historical payments match their invoices instead of landing in the confirm
   queue. Re-loading master data keeps the books' payment state.
2. **Matching before categorisation** (BKS-04): invoice payments (amount = outstanding,
   name similarity >= 0.8 on the bank counterparty field or the memo), bill payments,
   `CARD SETTLEMENT` (row 3, gross and fee parsed from "GROSS x FEE y"), `CASH DEPOSIT`
   (row 4). Matched lines are posted immediately; `post_ready` posts rule-categorised
   lines.
3. **Rules added to `data/rules.json`:** `R-EQUIP` (`RESTAURANT SUPPLY|KITCHEN EQUIPMENT`
   -> 1500 Equipment, investing cash flow; needed for WP8's equipment purchases) and
   `R-CAPITAL` (`OWNER CAPITAL` -> 3000). The other rules are PRD/05 §3 verbatim.
4. **Receipts:** a receipt matching a bank outflow (same amount, date +/- 3 days, vendor
   similarity >= 0.6; undated receipts within the last 30 days) is linked as evidence and
   never posted again (BKS-14). Unmatched receipts wait for confirmation, then post
   from the till (row 12). Low confidence always creates a RECEIPT confirm item. The
   TOTAL line is excluded from the line items.
5. **Confirm queue:** CATEGORY accepts any non-cash account or PERSONAL; PERSONAL accepts
   PERSONAL (-> 3100) or any expense account; DUPLICATE: `duplicate_claim_refund` (-> 1110)
   or `genuine_second_purchase` (rule label, else a new CATEGORY item); RECEIPT:
   `confirm` / `reject`. Only HUMAN owner or bookkeeper may resolve.
6. **Statements:** cash = 1000 + 1010. OPENING entries are the starting position (always
   in opening cash, never a cash-flow line). Cash-flow lines carry monthly columns plus
   `total`. A cash movement against several non-cash lines is split pro rata (integer
   cents, remainder to the largest line).
7. **Reconciliation:** OPENING entries and bank lines of kind `opening` are excluded on
   both sides (the agreed start). On the seeded bakery: ledger 2,100.40 vs bank 1,900.00
   before the owner confirms the 5 pending lines; the difference equals the 5 itemised
   reconciling items, so `is_reconciled` is True.
8. **Performance:** POS sales are bulk-inserted (5,206 sales in about 2 s); the journal is
   cached in memory and cleared on every journal write.

## Seeded bakery check (WP8 bundle, before owner confirmations)

Confirm queue: 3 PERSONAL (S1), 1 DUPLICATE (S5), 1 CATEGORY (the S7 memo), plus 1
RECEIPT (S8 blurry) after receipts; clean and injected receipts link to their bank lines
(the injected one is flagged), and so does the rotated one (confidence 0.80, AUTO). Cash
COGS ratio (12 weeks) 0.3080; open bills BILL-2190, 2207, 2211, 2215; open invoices
INV-0998 (S9, 61-90 bucket), INV-1043, 1051 (overdue, S3), 1052, 1053, 1054.
Identities 1-5 hold.

## Not built yet (SHOULD, waiting for the team lead)

ECL provision matrix and row 15 (`ecl_rates` is `{}`, allowance 0), period close
(`close_period` raises `NotImplementedError`; `period_is_closed` reads the table and
posting into a closed month raises `PeriodLocked`), indirect cash-flow reconciliation,
Tesseract OCR (`OCR_BACKEND=auto|tesseract` read the sidecar for now).

## Proposed

Contract change request (WP3, PC B): add to `contracts.services.BooksService`
`load_opening_balances(lines: list[JournalLineDTO], d: date) -> None`,
`load_recurring(obligations: list[RecurringObligation]) -> None`,
`last_scan_flags() -> list[ScanFlag]` and `accounting_policies() -> list[str]`, with
no-op/canned implementations in `FakeBooks`. Why: PRD/11 §7 step 4 and ORC-09 need them,
and BKS-02 says ingest returns flags, but the protocol's `ingest_bank` returns only raw
rows. Backward compatible: yes (additive). Until then the orchestrator can call these
methods on the concrete `Books` class.

