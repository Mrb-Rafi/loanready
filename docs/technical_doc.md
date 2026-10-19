# LoanReady technical document

LoanReady is a prototype SME finance copilot (FinTechathon 2026, Topic E). This document
describes how it is built. Tables that describe the implementation are generated from the
code: [tier table](generated/tier_table.md), [compliance matrix](compliance_matrix.md),
[evidence](../evidence/README.md). Figures quoted below are golden values asserted by the
named tests.

## 1. Architecture

### 1.1 Layers

| Layer | Packages | May import |
|---|---|---|
| 0 | `contracts/` (DTOs, enums, protocols, money, clock, ids, hashing, fakes) | nothing of ours |
| 1 | `infra/` (settings, paths, database, sandbox runner, doctor, assembly) | contracts |
| 2 | `governance`, `books`, `planning`, `financing`, `outputs`, `agent`, `adapters`, `datagen` | contracts, infra; **never each other** |
| 3 | `orchestrator/` (composition root and `AppFacade`) | layers 0-2 |
| 4 | `ui/` (Streamlit), `scripts/` (demo, evidence, docs) | `ui`: orchestrator and contracts only |
| apart | `sandbox/` (bank, POS, messaging, lender simulator) | contracts only; the app reaches it only over HTTP |

The layering is enforced by `lint-imports` (`.importlinter`) and run by `tasks.py lint` and
`tasks.py integrate`. Business packages meet only in `orchestrator/`, which builds every
service (`build_app()`), wires the gate's executor to the adapters, and exposes one
`AppFacade` to the UI, the headless demo and the tests.

### 1.2 Processes

One machine, `127.0.0.1` only. The UI process holds the app; the sandbox runs either
in-process (default: tests, demo, `SANDBOX_MODE=inprocess`) through an ASGI transport, or
as a separate server (`python tasks.py sandbox`, port 8100). Data: `var/app.db` (app),
`var/sbx_*.db` (one SQLite file per sandbox sub-app).

### 1.3 The gate between the model and actions

Every side-effecting request becomes an `Action` evaluated by `GateService.evaluate`, then
`approve` (Tier 2, a human), then `execute`. Only `execute` mints an `ExecutionTicket`; only
`adapters/` call sandbox write endpoints, and the sandbox verifies the ticket on each write.
The model's output can never become an `Action` parameter: payees, accounts and amounts are
read from stored records (bills, payees) by code.

### 1.4 Shared invariants (CLAUDE.md, checked by `integrate`)

- Money: `int` cents across module boundaries and in the database; `Decimal` inside
  calculations; ROUND_HALF_EVEN only through `contracts.money`. A static check rejects
  `float` on money.
- Time only from `contracts.clock` (FixedClock at AS_OF 2026-10-14 in tests and the demo);
  ids from `contracts.ids` (seeded in tests and the demo); hashes from `contracts.canonical`
  (sha256 of canonical JSON).
- Everything works with `LLM_BACKEND=mock`, `OCR_BACKEND=sidecar` and the in-process sandbox.

## 2. Core algorithms

### 2.1 Double-entry posting (`books/posting.py`)

Every entry is validated before it is stored: each line has exactly one positive side, every
account exists in the chart of accounts, and sum(debits) = sum(credits) > 0 (CTL-FR-01,
`books.posting.validate`). Entries are never edited; corrections are reversals (CTL-FR-02).

| # | Event | Debit | Credit |
|---|---|---|---|
| 1 | POS cash sale | 1010 Till | 4000 Sales |
| 2 | POS card sale | 1020 Card settlements receivable | 4000 Sales |
| 3 | Card settlement (net of fees) | 1000 Bank (net), 6400 Fees (fee) | 1020 (gross) |
| 4 | Cash deposited | 1000 Bank | 1010 Till |
| 5 | Invoice issued | 1100 AR | 4000 Sales |
| 6 | Customer pays | 1000 Bank | 1100 AR |
| 7 | Bill received | expense per bill category | 2000 AP |
| 8 | Bill paid | 2000 AP | 1000 Bank |
| 9 | Direct business spend | expense per category | 1000 Bank |
| 10 | Personal spend (owner confirmed) | 3100 Owner's drawings | 1000 Bank |
| 11 | Duplicate charge, refund claimed | 1110 Refund claims receivable | 1000 Bank |
| 12 | Cash receipt paid from the till | expense per category | 1010 Till |
| 14 | Opening balances | 1000, 1010, 1500 | 3000 Owner's capital |
| 16 | Reversal of entry X | mirror of X's credits | mirror of X's debits |

Categorisation is rules first (`data/rules.json`), then the classifier; anything below the
confidence threshold (0.80) and every PERSONAL label goes to the owner's confirm queue.
Payment state comes from the bank feed: an invoice or bill becomes paid when its bank line
is matched. Duplicates (same source, amount and counterparty, at most 2 days apart) are flagged and
held for the owner. Receipts are matched to a bank outflow (same amount, ±3 days, vendor
similarity ≥ 0.6) and then only linked as evidence, never posted twice; low OCR confidence
always asks the owner.

### 2.2 Statements and identities (`books/statements.py`)

P&L by month (revenue, COGS, gross profit, operating expenses, operating profit, interest,
net profit); cash-flow statement by the direct method (lines on cash accounts 1000 + 1010,
classified by the other side of each entry; opening entries are the starting position, not
cash flows); balance sheet with profit to date computed rather than closed. Presentation
follows `LOANREADY_FRAMEWORK` (IFRS for SMEs default, US GAAP titles as an option).

Identities asserted on the tiny fixture and on the seeded bakery
(`tests/books/test_identities.py`, CTL-FR-07): total debits = total credits; assets =
liabilities + equity at every month end; net profit = change in equity from profit;
cash-flow closing cash = ledger cash. Tiny-fixture golden balance sheet
(`tests/books/test_statements_tiny.py`): bank 2,710.00, till 1,100.00, cash 3,810.00,
AR 600.00, AP 800.00.

### 2.3 Reconciliation (`books/reconcile.py`)

Each bank line is matched one-to-one to a posted entry on account 1000 with the same signed
amount within ±3 days (same `source_ref` first, then closest date, then counterparty
similarity). `is_reconciled` means the ledger-to-bank difference equals the sum of the
itemised reconciling items (CTL-FR-04). On the seeded bakery before the owner's
confirmations the ledger is 200.40 above the bank, which is exactly the five pending bank
lines; after confirmation the ledger equals MockBank (1,900.00), checked by the demo and
`tests/integration/test_identities_seeded.py`.

### 2.4 13-week forecast (`planning/forecast.py`, `scenarios.py`)

Week k covers AS_OF + 7(k−1) + 1 .. AS_OF + 7k. For each scenario:

| Input | LOW | BASE | HIGH |
|---|---|---|---|
| Weekly sales | P25 of last 12 weeks | median of last 8 weeks | P75 of last 12 weeks |
| Invoice not yet due | due + P75 lateness | due + median lateness | due date |
| Invoice overdue | max(AS_OF + 28, due + P75) | max(AS_OF + 14, due + median) | AS_OF + 3 |
| Overdue > 60 days | excluded | excluded | AS_OF + 3 |
| No lateness history | 30 days late | 14 days late | on time |

Event uplifts multiply sales in overlapping weeks; COGS = sales × cash COGS ratio (clamped to
[0, 0.95]); bills fall in their due week (overdue bills in week 1); recurring rent, wages
and utilities on their dates; suspected-fraud bills are excluded. Each component is rounded
to cents before summing, so every row adds up as displayed:
`closing_k = opening_k + sales_k + collections_k − cogs_k − bills_k − fixed_k`.
LOW ≤ BASE ≤ HIGH holds every week (hypothesis property test, `test_ordering_property.py`).

Gap and term: `recommended_gap = roundup_100(max(0, −min LOW closing) + safety_buffer)` with
the buffer one week of fixed costs; `needed_term_days` = days to the end of the first week
after the BASE crossing in which BASE is back above zero (14 to 91); `going_concern_flag`
when LOW is negative in week 13 or BASE never recovers. Golden tables: every LOW/BASE/HIGH
closing of the tiny fixture (`test_forecast_tiny.py`); on the bakery WP4 reproduces WP8's
independent forecast week by week (`test_rina.py`).

### 2.5 Cash plan: collect, then delay, then borrow (`planning/cashplan.py`)

1. **COLLECT** (no cost): every invoice overdue by at most 60 days is assumed paid at
   AS_OF + 5; one reminder draft each. 2. **DELAY** (no cost): bills with no late penalty,
   not essential, not rent/wages/loan, with a grace period and due in weeks 1-4 move to due
   + grace; one delay-request draft each. Essential obligations are never delayed. 3.
   **BORROW**: the remaining gap becomes a `FinancingRequest`. On the seeded bakery (bank
   basis) the gap goes 4,800.00 → 1,600.00 → 1,000.00; in the app before the owner's
   confirmations 4,600.00 → 1,400.00 → 800.00. Drafts are Tier 1; sending them is Tier 2.

### 2.6 Financing cost: IRR, APR, EAR (`financing/irr.py`, `metrics.py`)

Flows are from the borrower's side: `+A` received at day 0, repayments negative.
`NPV(r) = Σ amount_t / (1 + r)^day_t` in Decimal (precision 34). Sign convention: for a
borrower `NPV(0) = −total_cost ≤ 0` and NPV rises towards `+A` as r grows, so the daily
IRR is found by bisection on [0, 1] (tolerance 1e−12). Then `nominal_apr = r × 365`,
`ear = (1 + r)^365 − 1` (the headline "annualised cost"), `total_cost = −Σ flows`, and
`simple_annualised = total_cost / amount_received × 365 / term_days`.

Golden values (`tests/financing/test_irr.py`, tolerance ±0.0005 on rates, exact on cents):

| Case | Result |
|---|---|
| Bank loan 10,000.00 at 12% APR, 12 monthly instalments | payment 888.49; APR 12.11%; EAR 12.87% |
| Overdraft 1,000.00 at 18% for 30 days | interest 14.79; APR 17.87%; EAR 19.56% |
| Supplier credit net 30 | EAR 0.00% |
| Supplier 2/10 net 30, discount forgone | APR 36.89%; EAR 44.59% |
| Micro-loan 5% flat, 30 days | APR 59.41%; EAR 81.05% |
| Merchant cash advance, factor 1.20 on 1,000 over 90 days | simple 81.11%; APR 151.19%; EAR 352.12% |

MCA payments are rounded cumulatively (`pay_k = round(k·F·A/T) − round((k−1)·F·A/T)`) so
every flow is whole cents and the total is exactly F·A.

### 2.7 Disclosures and comparison (`financing/disclosure.py`, `compare.py`)

Every option shows amount received, total repaid, total cost, term, nominal APR and EAR side
by side; factor-rate products carry an explanatory line ("a factor of 1.20 is not 20% a
year"). Options are ranked neutrally by EAR among eligible products; ineligible ones are
listed with their reasons (for example below the product minimum). Products and rates come
only from the dated catalogue `data/products.json`; an unknown product id is refused
(CTL-FIN-01). LoanReady compares; it never recommends a lender or accepts a loan.

### 2.8 Readiness score (`financing/readiness.py`)

A 0-100 self-assessment from business data only (no personal attributes exist in the
input type): history 15, bookkeeping 20, separation of personal spend 15, sales stability
15 (coefficient of variation), cash health 15, receivables 10, debt service 10 (DSCR).
Points are linear between anchors; `score = round_half_even(Σ points)`; bands LOW < 50,
FAIR 50-74, GOOD ≥ 75. Improvement suggestions are re-scored, never estimated. It carries a
disclaimer that it is not a credit score or a lending decision (CTL-AI-08).

### 2.9 Messages and the loan pack (`outputs/`)

Reminders and delay requests are worded from facts computed by code (invoice number, amount,
due date, business name). A post-check rejects any text that adds an amount, URL, bank
account, phone number or threatening/legal wording, or exceeds 1,200 characters; on
rejection the deterministic template is used and the fallback is logged. The AI label is
part of the hashed body. Drafts are immutable: the approval is bound to the body's sha256.
The loan pack is an A4 PDF built with reportlab's built-in Helvetica and `invariant=1`, so
the same content gives byte-identical files on Windows and Linux; it carries a synthetic-data
banner, 13 sections, masked account numbers and the audit chain's final hash. Submission
sends the file hash; the sandbox lender re-hashes it.

## 3. Security design

### 3.1 Tiers

Hard-coded in `governance/tiers.py` (a read-only mapping; no setter exists):
T0 observe/compute, T1 draft, T2 execute after approval, T3 autonomous (disabled), and a
blocked set that no setting can enable (pay a new payee, add or change a payee account,
accept a loan, change limits, disable the gate or kill switch, edit or delete the log,
approve one's own proposal, export raw data to a remote model). Unknown types fail closed.
Full list: [generated tier table](generated/tier_table.md).

### 3.2 Gate pipeline (`governance/gate.py`)

`evaluate` runs in order and stops at the first failure, writing one BLOCKED row:
1 kill switch, 2 blocked set / unknown type, 3 origin and provenance (untrusted text cannot
initiate or shape an action), 4 role, 5 parameter schema, 6 entity checks against stored
records (payee exists, bill open, account on the bill equals the stored payee account, else
`ACCOUNT_CHANGED`), 7 caps, 8 tier decision. `execute` re-checks everything, consumes the
approval token, then mints the execution ticket.

### 3.3 Approvals and dynamic linking (`governance/approvals.py`)

A Tier 2 proposal shows the human the amount, payee, masked account and reason. The approval
token is bound to the hash of the exact parameters (the PSD2 "dynamic linking" principle),
single use, and expires after 15 minutes (`APPROVAL_TTL_SECONDS=900`). Maker-checker: a human
cannot approve their own proposal; a bookkeeper cannot approve payments; the agent can never
approve. A changed parameter or edited draft body after approval is refused
(`DRAFT_MISMATCH`, `PACK_HASH_MISMATCH`).

### 3.4 Execution tickets, caps, kill switch

Tickets are signed, single use, short lived and bound to the action type and binding fields
of the outgoing request; the sandbox rejects writes without a valid one. Caps (defaults):
5,000.00 per action, 10,000.00 per day, 8,000.00 per payee per week, 2 messages per
customer per week, 7 days between reminders to the same customer. The sandbox enforces its
own per-payment and daily limits and a write rate limit independently. The kill switch
blocks everything except Tier 0 and cannot be disabled by the agent.

### 3.5 Audit hash chain (`governance/audit.py`)

Every gate decision (allowed, drafted, pending, approved, rejected, blocked, executed,
failed) and security event is an append-only row with
`row_hash = sha256(prev_hash ‖ canonical row)`; there is no update or delete path.
`verify_chain()` recomputes it; the CSV export and the loan pack carry the final hash.
Parameters are masked before logging. The sandbox keeps an independent chain per sub-app.

### 3.6 Data-class policy and masking (`agent/policy.py`, `masking.py`)

Classes: C0 public, C1 aggregate, C2 counterparty names, C3 bank identifiers, C4 documents.
Each task declares its maximum class; C3 and C4 never go to a remote model, and C2 only
masked. Masking replaces names with placeholders and digit runs, e-mails and phones with
tokens before any remote call. Remote use is opt-in. API keys are read in one module and
redacted from logs.

### 3.7 Untrusted data

Text from receipts, invoices, bills, e-mails, bank memos and POS notes is wrapped as
`Untrusted` with its source. It enters prompts only through `agent.prompts.data_block()`
inside data tags with an instruction not to follow it, and it is scanned for instruction
patterns: each hit is logged as `UNTRUSTED_INSTRUCTION_IGNORED`. No code path turns such
text into an action parameter. In the demo four planted instructions (bank memo, POS note,
receipt, bill) are logged and nothing moves.

## 4. Sandbox

`sandbox/` simulates a bank (accounts, payees, scheduled payments with value dates and
cancellation, returns), a POS feed, a messaging provider and a lender inbox. It uses scoped
bearer tokens (read, write, admin), idempotency keys on every write (a replay returns the
original result, no double payment), bank-side limits, server-side payee-account checks,
execution-ticket verification and a hash-chained operation log. A simulator was chosen over
a public test bank because it lets the demo and the tests exercise the exact failure modes
that matter (changed bank details, replays, limits, tampered packs) deterministically, with
no credentials and no network.

## 5. Regulatory alignment

LoanReady is designed around IFRS for SMEs presentation (with a US GAAP option), controls
modelled on SOX/COSO principles each with an automated test, the PSD2 SCA dynamic-linking
principle for approvals, the cost-disclosure style of US commercial-financing disclosure
laws and the PBoC annualised-rate rule, data minimisation from GDPR and PIPL, and the human
oversight principle of the EU AI Act. This is alignment, not certification: see the
[compliance matrix](compliance_matrix.md).

## 6. Limitations and future work

- Not built (PRD SHOULD/OPTIONAL): Ollama and OpenAI backends, hybrid routing, LLM cache and
  remote budget guard, intent router and chat; Tesseract OCR (receipts use OCR sidecars);
  ECL provision matrix; period close; US GAAP indirect cash-flow reconciliation; China
  private-lending cap check; supplier price-rise alert; loan-pack charts; payee watchlist;
  the UI demo stepper; Tier 3.
- Synthetic data, one business, one currency; no real bank, POS or lender connection; no
  multi-user authentication.
- The forecast is a rules-based projection with ranges, not a statistical model.
- Next steps: real read-only bank feeds (the adapter ports already isolate them), local
  models through Ollama with the existing policy and post-checks, multi-user roles, and the
  P1 accounting features above.
