# Deck outline (10 slides, about 10 minutes)

Figures come from `evidence/demo_summary.json` (seed 20261014, AS_OF 2026-10-14) and the
financing golden tests; re-check them after any re-run of `python tasks.py demo`.
Wording follows PRD/14 §4: "designed around", "aligned with"; never "compliant",
"certified", "credit score", "best loan for you".

## 1. The problem (45 s)

- Young SMEs run on mixed records: personal and business spending in one account, no books,
  receipts in a drawer.
- A cash crunch arrives before the money that would cover it; the only fast credit is often
  the most expensive (merchant cash advances priced as a "factor").
- Lenders cannot assess a business without books. IFC estimates the MSME finance gap at
  USD 5.7 trillion for formal MSMEs plus USD 2.1 trillion informal (IFC report, Feb 2026, 2019
  data).

## 2. LoanReady in one line (30 s)

"From messy records to a lender-ready pack, closing cash gaps in order of cost, with every
action behind a human-approved permission gate." Topic E capabilities covered: bookkeeping
and statements, cash-flow forecasting and planning, financing comparison and loan readiness.

## 3. How it works (60 s)

Capture → books → 13-week forecast (low/base/high) → **collect**, then **delay**, then
**borrow** → loan pack. Show the five steps as one row of icons with the Tier of each
(T0 observe, T1 draft, T2 approve).

## 4. Live demo or video cut (2 min 30 s)

Rina's Bakery, the ten steps of the demo story:
- 3 personal expenses moved to drawings after confirmation; a duplicate charge flagged; a
  blurry receipt waits for the owner.
- Ledger cash $2,200.00 equals bank $1,900.00 + till $300.00.
- Base cash goes below zero in **week 3** (festival flour order before two catering invoices).
- Two reminders and one delay request approved and sent; the remaining need, $1,000.00, goes
  to the comparison.
- Supplier paid after approval (MockBank reference); a bill with changed bank details is
  **blocked** (`ACCOUNT_CHANGED`); four planted instructions are ignored and logged.
- Readiness 86/100 (self-assessment, not a credit score); pack submitted after approval;
  lender receipt shown; accepting a loan stays blocked.

## 5. Financing comparison (60 s)

- Ranked by annualised cost (EAR), APR beside it, total cost in dollars.
- For Rina's $1,000.00: Golden Mills 30-day trade credit EAR 0.00% is cheapest; the merchant
  cash advance is the most expensive (EAR 1,752.76% for this short, card-repaid advance).
- The factor-rate trap, from the golden test: factor 1.20 on 1,000 over 90 days = simple
  81.11%, APR 151.19%, **EAR 352.12%**. "1.20" is not "20%".

## 6. Architecture: AI proposes, code decides (45 s)

- Layers: contracts → infra → business packages (never importing each other) → orchestrator
  → UI; checked by `lint-imports`.
- The model only classifies, words text from computed facts, or picks from a fixed menu; it
  never computes money or chooses a payee, amount or account.
- Self-built sandbox (bank, POS, messaging, lender) with scoped tokens, idempotency and its
  own hash-chained operation log.

## 7. Permission tiers and approvals (45 s)

- Generated tier table: T0 / T1 / T2, Tier 3 disabled, hard-coded blocked set.
- Gate pipeline: kill switch → blocked set → origin/provenance → role → schema → entity checks
  → caps → tier.
- Approval tokens bound to the exact parameters (PSD2 dynamic-linking principle), single
  use, 15 minutes; execution tickets single use; maker-checker.

## 8. Security evidence (45 s)

- Injection corpus and planted instructions: logged, nothing moves.
- Fraud invoice: blocked in the app and checked again by MockBank.
- Hash-chained action log exported to CSV, chain verified; show `chain_verification.txt` and
  the decision counts (allowed, drafted, pending, approved, executed, blocked, events).

## 9. Regulatory alignment (45 s)

Designed around IFRS for SMEs statements (US GAAP option); controls modelled on SOX/COSO
principles, each with an automated test; cost disclosure in the style of US
commercial-financing disclosure laws and the PBoC annualised-rate rule; data minimisation
from GDPR and PIPL (bank identifiers and documents never leave the machine); EU AI Act
human oversight. Point to the generated compliance matrix. Alignment, not certification.

## 10. Landscape and next steps (45 s)

- Neighbours: QuickBooks AI agents, Xero, Zoho Books, Wave (bookkeeping); Nav, Lendio,
  Biz2Credit (loan marketplaces); Khatabook, OkCredit, Vyapar (emerging-market ledgers).
  LoanReady's angle: cost-ordered cash planning and a governed agent, end to end.
- Next: read-only real bank feeds through the existing ports, local models through Ollama
  under the same policy and post-checks, multi-user roles, period close and ECL.
- Fit with WeBank's SME lending: a structured, verifiable application pack with an audit
  hash, from businesses that lenders could not assess before.
