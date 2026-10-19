# LoanReady

FinTechathon 2026, International Track, Topic E (SME Finance Copilot).

LoanReady turns a young small business's messy records into clean double-entry books and a
13-week cash forecast. When a cash gap appears it closes it in order of cost: **collect**
overdue invoices, then **delay** a penalty-free bill within its grace period, then
**borrow** the remainder, comparing financing options by their true annualised cost. It
then prepares a lender-ready application pack. The AI only classifies, words text from
facts computed by code, or picks from a fixed menu. Every action passes a permission gate
written in plain code, and every decision lands in a hash-chained audit log.

Everything runs on one machine with **synthetic data** (Rina's Bakery) and a self-built
banking/POS/messaging/lender **sandbox**; no real money, bank or personal data is involved.

## What the demo shows (PRD/00 §4)

`python tasks.py demo` plays the whole story headlessly and checks scenarios S1-S10:

| Step | What happens | Checked |
|---|---|---|
| 1. Capture | POS and bank feed imported, receipts read; 3 personal expenses found and moved to drawings after the owner confirms; a duplicate charge flagged; a blurry receipt waits for confirmation | S1, S5, S8 |
| 2. Books | P&L, cash-flow statement, balance sheet; ledger cash equals bank + till after the owner's confirmations | |
| 3. Forecast | Base case goes below zero in **week 3** (festival flour order due before two catering invoices are paid) | S2 |
| 4. Collect | Reminders drafted for the two overdue invoices, approved, sent through the messaging sandbox | S3 |
| 5-6. Delay, borrow | A penalty-free bill moved within its grace period; for the rest, 30-day supplier credit is cheapest and the merchant cash advance most expensive | S4 |
| 7. Pay a supplier | Regular flour supplier paid after approval; MockBank returns a reference | S10 |
| 8. Security | A bill asking for payment to a new bank account is blocked (`ACCOUNT_CHANGED`); planted instructions in a receipt, bank memo, POS note and bill are ignored and logged; a 75-day-old invoice gets no reminder | S6, S7, S9 |
| 9. Loan pack | Readiness score, PDF pack, submitted to the pretend lender after approval; accepting a loan stays blocked | |
| 10. Evidence | Action log exported to CSV, chain verified | |

Results: [`evidence/demo_summary.json`](evidence/demo_summary.json) and
[`evidence/README.md`](evidence/README.md).

## Quick start

Python **3.12**, a folder path without spaces (on Windows not under OneDrive).

```
# Windows: py -3.12 tasks.py setup          Linux/macOS: python3.12 tasks.py setup
python tasks.py setup      # creates .venv and installs requirements.lock exactly
python tasks.py doctor     # every MUST row must be OK
cp .env.example .env       # Windows: copy .env.example .env
python tasks.py seed       # generate Rina's Bakery, load the sandbox and the books
python tasks.py ui         # http://127.0.0.1:8501
```

After `setup`, `tasks.py` re-launches itself inside `.venv`, so plain `python` (or `py -3.12`
on Windows) works for every target. No API key, model, OCR binary or running server is
needed: the defaults are `LLM_BACKEND=mock`, `OCR_BACKEND=sidecar` and the in-process sandbox.

| Command | Does |
|---|---|
| `python tasks.py test` | default test suite (`--all` adds slow tests, `test books` one package) |
| `python tasks.py lint` | ruff check, ruff format check, import-layer contracts |
| `python tasks.py integrate` | layering, static checks (no float money, no wall clock, write paths only in adapters) and every package's tests |
| `python tasks.py demo` | headless Rina story; exits non-zero if any S1-S10 check fails |
| `python tasks.py evidence` | action log, sandbox ops log, chain checks, test report into `evidence/` |
| `python tasks.py docs` | generated tier table, compliance matrix and `THIRD_PARTY.md` |
| `python tasks.py sandbox` | the sandbox as a separate server on port 8100 (optional) |

## Architecture

```
+---------------------------- one machine, localhost only ----------------------------+
|  Streamlit UI (ui/, port 8501)                                                      |
|     | in-process Python calls (AppFacade)                                           |
|     v                                                                               |
|  Orchestrator (orchestrator/) -- composition root, the only place packages meet     |
|     |            |             |            |             |            |            |
|  agent/      governance/    books/     planning/    financing/    outputs/          |
|  (LLM layer, (gate, tiers,  (ledger,   (forecast,   (catalogue,   (reminders,       |
|   masking)    approvals,     stmts,     cash plan)   IRR, score)   loan pack)       |
|               audit log)     recon)                                                 |
|                 |  ExecutionTicket                                                  |
|                 v                                                                   |
|              adapters/  (bank, POS, messaging, lender ports over HTTP)              |
|                 |  bearer token (read | write) + Idempotency-Key + ticket            |
|                 v                                                                   |
|  Sandbox (sandbox/): /bank /pos /messaging /lender, own SQLite files and op logs    |
|  App database: var/app.db (SQLite)                                                  |
+-------------------------------------------------------------------------------------+
```

The rules that make this safe are enforced by code and tests, not by convention:

- **AI proposes, code decides.** The model never computes money, checks permissions or
  chooses a payee, amount or account. Money is `int` cents across modules and `Decimal`
  inside calculations, rounded half-even in one place (`contracts.money`).
- **Permission tiers** ([generated table](docs/generated/tier_table.md)): observe (T0),
  draft (T1), execute only after a human approval bound to the exact parameters (T2), and a
  hard-coded blocked set (paying a new payee, changing a payee's account, accepting a loan,
  disabling the gate, editing the log, ...). Only `adapters/` may call sandbox write
  endpoints, and only with a single-use execution ticket minted by the gate.
- **Untrusted text** (receipts, invoices, bills, memos, POS notes) is marked as such, enters
  prompts only inside a data block and can never start or shape an action.
- **Audit**: every decision, including blocked attempts, is a hash-chained row with no
  update or delete path; the sandbox keeps its own independent chain.
- **Layering**: business packages never import each other; `lint-imports` checks it.

Step-by-step use of every screen and command: [`docs/usage_guide.md`](docs/usage_guide.md).
Details: [`docs/technical_doc.md`](docs/technical_doc.md),
[`docs/security_self_assessment.md`](docs/security_self_assessment.md),
[`docs/compliance_matrix.md`](docs/compliance_matrix.md).

## The sandbox

`sandbox/` is a self-built simulator (FastAPI) of a bank, a POS system, a messaging
provider and a lender, with scoped tokens (read, write, admin), idempotency keys, bank-side
limits independent of the app, execution-ticket verification on every write and a
hash-chained operation log per sub-app. A simulator lets the demo exercise real failure
modes (changed bank details, replays, limits) without any real account. Tests and the demo
run it in-process; `python tasks.py sandbox` runs it as a server.

## LLM backends

| `LLM_BACKEND` | What answers |
|---|---|
| `mock` (default; always used by tests, `seed`, `demo`, `evidence` and `docs`) | Deterministic keyword classification and templates (labelled as templates, not AI); no model needed |
| `ollama` | A local model through Ollama (default `phi3:mini`), then mock |
| `openai` | OpenAI for the tasks the data-class policy allows, then mock |
| `hybrid` (recommended for the live UI) | Per task: wording and explanations try OpenAI, then the local model; categorisation tries the local model, then OpenAI with the merchant name only; receipt text and intents stay local; mock is always last |

Whatever the mode, bank identifiers and documents never leave the machine, names are masked
before a remote call, every answer is validated, numbers in explanations and messages are
post-checked against the facts, and any failure falls back to the next backend. A local
model's category label is only a suggestion the owner confirms (its confidence is capped
below the 0.80 threshold). Answers are cached in `var/llm_cache.db`.

**OpenAI cost guard.** Each remote call reserves its worst-case cost first and books the
real token usage after, against three caps: calls per day (`OPENAI_MAX_CALLS`), dollars per
day (`OPENAI_MAX_USD`) and dollars all-time (`OPENAI_TOTAL_MAX_USD`, for prepaid credit),
using the prices in `OPENAI_PRICE_IN_PER_MTOK` / `OPENAI_PRICE_OUT_PER_MTOK`. When a cap is
reached, calls fall back to the local model or mock.

To switch on: install Ollama and `ollama pull phi3:mini`; for OpenAI put `OPENAI_API_KEY` in
`.env`, run `python -m agent.health` to list the small models your key can use, set
`OPENAI_MODEL` and the prices from the OpenAI pricing page, then `LLM_BACKEND=hybrid`.
`python -m agent.health` shows the routes each task would take right now and the budget.

## Repository map

| Path | Work package | What |
|---|---|---|
| `contracts/`, `infra/`, `tasks.py` | WP0 | Frozen shared types, money/clock/ids/hash helpers, task runner, doctor, hand-off |
| `governance/` | WP1 | Gate, tiers, approvals, tickets, caps, kill switch, audit log, controls registry |
| `sandbox/`, `adapters/` | WP2 | Simulator service and the only code that calls it |
| `books/` | WP3 | Ingestion, rules, posting, statements, reconciliation, receipts |
| `planning/` | WP4 | 13-week forecast (low/base/high) and the collect-delay-borrow plan |
| `financing/` | WP5 | Product catalogue, IRR/APR/EAR, disclosures, comparison, readiness score |
| `outputs/` | WP6 | Reminder and delay drafts with post-checks, loan-pack PDF |
| `agent/` | WP7 | LLM layer: policy, masking, prompts, validation, mock backend |
| `datagen/`, `data/` | WP8 | Synthetic Rina's Bakery generator and its expected values |
| `orchestrator/` | WP9 | Composition root, `AppFacade`, executors, seeding |
| `ui/` | WP10 | Streamlit screens, approval dialog |
| `scripts/`, `docs/`, `evidence/`, `tests/e2e/` | WP11 | Demo, evidence, generated documents |
| `PRD/` | | The specification |

## Limitations

Honest list of what this prototype does not do (also in the security self-assessment):

- Synthetic data only; single currency (USD); single business; no real bank connection.
- No multi-user authentication: the UI switches between an owner and a bookkeeper role.
- Not built yet (SHOULD/OPTIONAL items of the PRD): intent router and chat; Tesseract OCR (receipts are read from OCR
  sidecar files); expected-credit-loss provision matrix; period close; US GAAP indirect
  cash-flow reconciliation; China private-lending cap check; supplier price-rise alerts;
  loan-pack charts; payee watchlist screening; the UI's guided demo stepper.
- Tier 3 (autonomous execution) is disabled.
- The readiness score is a self-assessment from business data, not a credit score or a
  lending decision. LoanReady does not act as a broker and never accepts a loan.
- Regulatory statements are about design alignment only; LoanReady is not certified or
  approved under any regime ([compliance matrix](docs/compliance_matrix.md)).

## Licences

All dependencies and their licences: [`THIRD_PARTY.md`](THIRD_PARTY.md) (generated from
`requirements.lock`). All data in this repository is synthetic and generated by our scripts.

## Team

Built by the LoanReady team for FinTechathon 2026 across three development PCs (Windows and
Linux), integrated on the Linux PC.
