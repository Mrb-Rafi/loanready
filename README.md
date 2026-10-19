# LoanReady

**An SME finance copilot that turns a small business's messy records into clean books, a
13-week cash forecast, a cost-ordered plan to close a cash gap and a lender-ready application,
with every action behind a permission gate written in plain code.**

2026 Shenzhen International FinTech Competition (FinTechathon) · International Track ·
**Topic E: SME Finance Copilot** · Team **Korikath Nexus**

---

## Documentation

| Document | PDF | Source |
|---|---|---|
| **Technical documentation**: architecture, every subsystem with its formulas, AI layer, governance, sandbox, testing, configuration, performance, limitations | [LoanReady_Technical_Documentation.pdf](docs/pdf/LoanReady_Technical_Documentation.pdf) | [docs/technical_doc.md](docs/technical_doc.md) |
| **Usage guide**: install, start, every screen and button, the approval dialog, a click-by-click demo script, turning on AI, commands, troubleshooting, FAQ | [LoanReady_Usage_Guide.pdf](docs/pdf/LoanReady_Usage_Guide.pdf) | [docs/usage_guide.md](docs/usage_guide.md) |
| Security self-assessment: permission tiers, known-risk list, residual risks | | [docs/security_self_assessment.md](docs/security_self_assessment.md) |
| Compliance matrix: regulation → control → code → test → evidence (generated) | | [docs/compliance_matrix.md](docs/compliance_matrix.md) |
| Permission tier table (generated from code) | | [docs/generated/tier_table.md](docs/generated/tier_table.md) |
| Execution evidence: demo summary, hash-chained logs, chain checks, test report | | [evidence/](evidence/README.md) |

---

## Team

**Korikath Nexus**

| Name | Role | University | Programme |
|---|---|---|---|
| **M. Rafiul Bahar Rafi** | Team leader | Bangladesh Agricultural University | Bioinformatics Engineering (final year) |
| **M. Wasiul Bahar Wasi** | Member | Bangladesh University of Professionals | Finance and Banking (second year) |
| **Fawzia Tahsin** | Member | Bangladesh Agricultural University | Bioinformatics Engineering (final year) |
| **Taposhi Rabeya** | Member | Bangladesh Agricultural University | Bioinformatics Engineering (final year) |

**Advisor:** **Dr. Md. Rakib Hassan**, Professor, Department of Computer Science and
Mathematics, Bangladesh Agricultural University
([profile](https://www.bau.edu.bd/profile/CSM1007)).

---

## The problem

Small businesses are rarely refused credit because they are bad risks; they are refused
because they cannot prove what they earn. Their records live in cash, paper receipts, chat
orders and one account shared between personal and business money. When a cash crunch comes,
the fastest money on offer, such as a merchant cash advance priced as a "factor rate", is
usually the most expensive.

## What LoanReady does

| Capability | How |
|---|---|
| **Books from messy records** | Imports till (POS) sales, the bank feed and receipts; categorises by rules first, then AI; separates personal spending; flags duplicate charges; posts balanced double-entry journals; builds P&L, cash-flow statement and balance sheet; reconciles to the bank |
| **13-week cash forecast** | Low, base and high scenarios; the week cash first goes negative; the drivers behind it (big bills, late customers) |
| **Cost-ordered cash plan** | Closes the gap cheapest first: **collect** overdue invoices, **delay** penalty-free bills within their grace period, **borrow** only the remainder |
| **Honest financing comparison** | Seven example products ranked by true annualised cost (EAR, with APR beside it), full disclosures and a plain explanation of factor-rate traps |
| **Loan readiness** | A 0-100 self-assessment from business data only, with concrete steps to improve it |
| **Lender-ready pack** | A 13-section PDF built from the books, forecast and score, submitted to a pretend lender after approval |
| **Safety in code, not in the AI** | The AI only classifies, words text from computed facts or picks from a fixed menu. Every payment, message and submission needs a human approval bound to the exact details; paying a new payee, changing a payee's bank account, accepting a loan or raising limits is always refused; every decision is written to a hash-chained log that cannot be edited |

Section references such as "PRD/10 §5" or ids such as "BKS-19" and "T-FIN-03" in the code
and documents point to the team's internal product specification, which is not published in
this repository.

Everything runs on one machine with **synthetic data** (Rina's Bakery) and a self-built
banking, POS, messaging and lender **sandbox**. No real money, bank or personal data is
involved.

---

## The demo story

`python tasks.py demo` plays the whole story headlessly and checks scenarios S1-S10
(business date 14 October 2026):

| Step | What happens | Scenario |
|---|---|---|
| 1. Capture | POS and bank imported, receipts read; three personal expenses moved to drawings after confirmation; a duplicate charge flagged; a blurry receipt waits for the owner | S1, S5, S8 |
| 2. Books | P&L, cash flow, balance sheet; ledger cash equals bank + till | |
| 3. Forecast | Base case goes below zero in **week 3**: a 6,500.00 festival flour order falls due before two catering invoices are paid | S2 |
| 4. Collect | Reminders drafted for the two overdue invoices, approved, sent | S3 |
| 5-6. Delay, borrow | A penalty-free bill is moved within its grace period; for the remaining 1,000.00, supplier credit is cheapest (EAR 0.00%) and the merchant cash advance most expensive | S4 |
| 7. Pay a supplier | The regular flour supplier is paid after approval | S10 |
| 8. Security | A bill asking for payment to a new bank account is blocked (`ACCOUNT_CHANGED`); planted instructions in a bank memo, POS note, receipt and bill are ignored and logged; a 75-day-old invoice gets no reminder | S6, S7, S9 |
| 9. Loan pack | Readiness score; pack generated and submitted after approval; accepting a loan stays blocked | |
| 10. Evidence | Action log exported; hash chain verified | |

Results: [`evidence/demo_summary.json`](evidence/demo_summary.json). A click-by-click version
for the screen is in the [usage guide](docs/pdf/LoanReady_Usage_Guide.pdf) (§8).

---

## Quick start

Requirements: **Python 3.12**, about 1 GB of disk, a folder path without spaces (on Windows,
not inside OneDrive). No AI model, API key, OCR software or database server is needed.

```
git clone https://github.com/Mrb-Rafi/loanready.git
cd loanready

# Windows:  py -3.12 tasks.py setup        Linux/macOS:  python3.12 tasks.py setup
python tasks.py setup      # creates .venv and installs requirements.lock exactly
python tasks.py doctor     # every MUST row must be OK
cp .env.example .env       # Windows: copy .env.example .env
python tasks.py seed       # generate Rina's Bakery and load the sandbox and the books
python tasks.py ui         # open http://127.0.0.1:8501
```

After `setup`, `tasks.py` switches into its own environment automatically, so plain `python`
(or `py -3.12` on Windows) works for every command.

| Command | What it does |
|---|---|
| `python tasks.py ui` | Start the screen (http://127.0.0.1:8501) |
| `python tasks.py demo` | Headless story; exits non-zero if any S1-S10 check fails |
| `python tasks.py evidence` | Action log, sandbox op log, chain checks and test report into `evidence/` |
| `python tasks.py docs` | Regenerate the tier table, compliance matrix and `THIRD_PARTY.md` |
| `python tasks.py test` | Default test suite (`--all` adds slow tests; `test books` runs one package) |
| `python tasks.py lint` | ruff and the import-layer contracts |
| `python tasks.py integrate` | Layering, static checks and every package's tests |
| `python tasks.py sandbox` | Run the simulated bank as a separate server on port 8100 |
| `python -m agent.health` | Show the AI mode, available models, per-task routes and OpenAI budget |
| `python -m scripts.capture_screenshots` | Re-take the usage guide's screenshots from the real app (needs Chromium) |
| `python -m scripts.build_pdfs` | Rebuild the two PDFs (needs pandoc and Chromium) |

Full instructions, every screen and troubleshooting: [usage guide](docs/pdf/LoanReady_Usage_Guide.pdf).

---

## Architecture

```
+---------------------------- one machine, localhost only ----------------------------+
|  Streamlit UI (ui/)                                                                 |
|     | AppFacade                                                                     |
|  Orchestrator (orchestrator/): composition root, the only place packages meet       |
|     |            |             |            |             |            |            |
|  agent/      governance/    books/     planning/    financing/    outputs/          |
|  (AI layer)  (gate, tiers,  (ledger,   (forecast,   (catalogue,   (reminders,       |
|               approvals,     stmts,     cash plan)   IRR, score)   loan pack)       |
|               audit log)     recon)                                                 |
|                 |  ExecutionTicket                                                  |
|              adapters/  (the only code that calls the sandbox)                      |
|                 |  scoped token + Idempotency-Key + ticket                          |
|  Sandbox (sandbox/): bank, POS, messaging, lender; own databases and op logs        |
+-------------------------------------------------------------------------------------+
```

Rules enforced by code and tests:

- **Money is exact:** integer cents between modules, `Decimal` inside calculations, half-even
  rounding in one place; a static check rejects floats on money.
- **Permission tiers:** observe (T0), draft (T1), execute only after a human approval bound to
  the exact parameters (T2), and a hard-coded blocked set
  ([tier table](docs/generated/tier_table.md)). Only `adapters/` may call sandbox write
  endpoints, and only with a single-use execution ticket from the gate.
- **Untrusted text** (receipts, invoices, bills, memos, POS notes) enters AI prompts only inside
  a data block and can never start or shape an action.
- **Audit:** every decision, including blocked attempts, is a hash-chained, append-only row;
  the sandbox keeps its own independent chain.
- **Layering:** business packages never import each other (`lint-imports`).
- **Deterministic:** fixed business date, seeded ids, canonical hashing; the demo and evidence
  are byte-identical across runs and across Windows and Linux.

---

## AI, used narrowly

| `LLM_BACKEND` | What answers |
|---|---|
| `mock` (default; always used by tests, `seed`, `demo`, `evidence` and `docs`) | Deterministic keyword classification and templates; no model needed |
| `ollama` | A local model (default `phi3:mini`), then mock |
| `openai` | OpenAI for the tasks the data-class policy allows, then mock |
| `hybrid` | Messages and explanations: OpenAI → local → mock; categorisation: local → OpenAI (merchant name only) → mock; receipt text and intents stay local |

Bank identifiers and documents never leave the machine; names are masked before any remote
call; every answer is validated and its numbers checked against the facts; a local model's
category labels are only suggestions the owner confirms. An OpenAI cost guard reserves each
call's worst case and stops at calls-per-day, dollars-per-day and all-time dollar caps. Setup:
[usage guide §10](docs/pdf/LoanReady_Usage_Guide.pdf).

---

## Quality and evidence

- **About 720 automated tests** across 13 suites (package tests against fakes, golden values,
  property tests, integration tests over the in-process sandbox, S1-S10 scenario tests, e2e
  tests of the headless demo); all pass on Linux. Latest results:
  [`evidence/test_report.txt`](evidence/test_report.txt).
- **Security tests:** prompt-injection corpus, fraudulent invoice, approval replay, ticket
  misuse, remote-policy refusal before any network call
  (`tests/integration/test_injection_integration.py`, `tests/integration/test_fraud_invoice.py`,
  `tests/governance/test_approvals.py`, `tests/agent/test_backends.py`).
- **Evidence** ([`evidence/`](evidence/README.md)): demo summary, the hash-chained action log,
  the sandbox's operation log and both chain verifications.

---

## Repository map

| Path | What |
|---|---|
| `contracts/`, `infra/`, `tasks.py` | Shared types, money/clock/id/hash helpers, task runner, doctor, hand-off and assembly |
| `governance/` | Permission gate, tiers, approvals, tickets, caps, kill switch, audit log, controls registry |
| `sandbox/`, `adapters/` | Simulated bank, POS, messaging and lender; the only code that calls them |
| `books/` | Ingestion, rules, posting, statements, reconciliation, receipts |
| `planning/` | 13-week forecast and the collect-delay-borrow plan |
| `financing/` | Product catalogue, IRR/APR/EAR, disclosures, comparison, readiness score |
| `outputs/` | Reminder and delay drafts with post-checks, loan-pack PDF |
| `agent/` | AI layer: policy, masking, prompts, validation, backends, routing, cache, budget |
| `datagen/`, `data/` | Synthetic Rina's Bakery generator and its expected values |
| `orchestrator/` | Composition root, `AppFacade`, executors, seeding |
| `ui/` | Streamlit screens and the approval dialog |
| `scripts/` | Headless demo, evidence export, generated documents, PDF builder |
| `docs/` | Technical documentation, usage guide (Markdown and PDF), security self-assessment, compliance matrix |
| `evidence/` | Execution evidence |

---

## Limitations

Stated honestly (details in the technical documentation, §23):

- Synthetic data only; one business; one currency (USD); no real bank, POS or lender.
- No multi-user authentication: the UI switches between an owner and a bookkeeper role.
- Not built: intent router and chat; Tesseract OCR (receipts are read from OCR sidecar files);
  expected-credit-loss provision matrix; period close; US GAAP indirect cash-flow
  reconciliation; China private-lending cap check; supplier price-rise alerts; loan-pack
  charts; payee watchlist screening; the guided demo stepper; Tier 3.
- The readiness score is a self-assessment, not a credit score or lending decision. LoanReady
  does not act as a broker and never accepts a loan.
- Regulatory statements describe design alignment only: LoanReady is designed around IFRS for
  SMEs, SOX/COSO-style controls, PSD2 dynamic linking, commercial-financing disclosure rules
  and GDPR/PIPL data minimisation; it is not certified or approved under any of them
  ([compliance matrix](docs/compliance_matrix.md)).

---

## Licences

All dependencies and their licences: [`THIRD_PARTY.md`](THIRD_PARTY.md), generated from
`requirements.lock`. All data in this repository is synthetic and generated by our scripts;
the financial products are fictional.
