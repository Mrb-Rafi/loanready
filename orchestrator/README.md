# orchestrator (WP9)

Composition root and the one `AppFacade` the UI and scripts call (PRD/11). The only package
where work packages meet; Layer 2 packages are imported **by name** here, so a partly
assembled tree still starts.

## Public API

- `build_app(settings=None, *, inprocess_sandbox=True, fakes="auto", engine=None) -> App`
  - `fakes="auto"`: a package that is missing (or whose `service` module lacks the registry's
    service names) is replaced by its fake from `contracts.testing.fakes`, with one warning
    per package (ORC-01). `fakes=True`: fakes everywhere (UI development). `fakes=False`:
    every package required; the error names the missing ones.
  - `infra.versions.check()` runs first; a contracts major mismatch stops start-up (ORC-02).
  - `engine` overrides `var/app.db` (tests).
- `App` implements every method of `contracts.services.AppFacade` (checked by
  `test_wiring.py`), plus `close()` (closes the in-process sandbox transport) and
  `excluded_bills()` (bills left out of the forecast as suspected fraud, for the forecast
  page's "excluded pending verification" list).
- `python -m orchestrator.seed [--external-sandbox] [--no-generate]` = `python tasks.py seed`.

## Modules

| Module | Does |
|---|---|
| `container.py` | `build_app`, package detection, fakes fallback, PRD/11 §2 wiring order |
| `facade.py` | `App`: every facade method; Tier 0 reads pass the gate first (ALLOWED rows) |
| `requests.py` | `build_action` (ORC-03), params from stored records with trusted provenance (ORC-04) |
| `executors.py` | `ExecutorImpl`: approved action -> adapter call (PRD/11 §4); never edits the books |
| `assemble.py` | forecast inputs (fraud bills excluded), financing request, loan-pack content |
| `sync.py` | pull bank + POS, ingest, scan untrusted text, categorise, post, log duplicates |
| `events.py` | `EventSink`: agent/books/orchestrator events -> audit log, with CTL ids |
| `session.py` | acting principal, approval tokens (in memory only), data version |
| `chat.py` | chat handlers per intent (ORC-05/06, P1) |
| `seed.py` | PRD/11 §7 |

## Decisions where the PRD left room

- **Constructors of WP3–WP8** follow PRD/11 §2 literally: `Books(engine, clock, ids, settings)`,
  `Outputs(engine, clock, ids, settings)`, `agent.llm.LLM(settings, events=sink)`,
  `agent.service.Agent(llm, events=sink)`, `Planner()`, `Financing(data/products.json)`,
  `Readiness()`. If a hand-off differs, only the matching factory in `container.py` changes.
- **Flag logging.** Both the agent's `scan` (AGT-11) and the sync path may report the same
  text. `EventSink` logs `UNTRUSTED_INSTRUCTION_IGNORED` and `DUPLICATE_FLAGGED` once per
  `(source, source_ref)` for the life of the app, so a re-sync does not log again.
  Scanned: bank memos, POS notes, bill descriptions and claimed accounts, receipt OCR flags.
- **Drafts are reused.** `draft_reminders()` / `draft_delay_requests()` return the existing
  unsent draft for an item instead of drafting it again (no duplicate T1 rows).
- **Payment and the books.** After an EXECUTED `SCHEDULE_SUPPLIER_PAYMENT`, `execute()` runs
  `sync_all()`; a same-day (settled) payment then reaches the ledger through the bank feed.
- **Cancel.** `cancel_payment(sbx_ref)` finds the original action id in the audit log
  (EXECUTED row with that `sbx_ref`); the gate checks the undo window.
- **Loan-pack application terms** (amount, term, purpose) are kept in memory per pack id for
  the executor; after a restart they fall back to the pack's metadata.
- **Financing default.** When the cash plan needs no borrowing, the comparison uses the
  recommended gap (or 1,000.00) over the needed term (or 30 days) so the page has content.
- **Accounting policies** come from `books.policies` (`policies(framework)` or
  `ACCOUNTING_POLICIES`) when WP3 provides them, else a neutral default list.
- **Events** for the forecast: `data/events.json`, else the seed bundle's events.
- **Sync window**: bank and POS are pulled from `AS_OF - 400 days` (covers the 6-month history).

## Contract change request (not applied; contracts are frozen)

- CCR-WP9-01: `BooksService` has no method to load opening balances or recurring
  obligations, though BKS-01 says books loads them from the bundle. `seed.py` calls
  `books.load_opening_balances(lines, history_start)` and `books.load_recurring(items)`
  **if they exist**. Proposal: add both to the protocol (additive, 1.1.0), or have WP3
  accept them through `load_master_data` keyword arguments. Needs agreement with PC B.

## Not built / limits

- With fakes, figures come from the canned fakes (tiny fixture), not from the seeded bundle.
  The S1–S10 and identity tests (`test_scenarios.py`, `test_identities_seeded.py`, marked
  `real` and `slow`) skip until WP3–WP8 are assembled; they have not run yet.
- Seeding the chart of accounts is left to WP3 (`Books` creates it); there is no protocol
  method for it.

## Test

```
python tasks.py test integration          # fakes + real WP1/WP2, in-process sandbox
python tasks.py test integration --all    # adds the `slow`/`real` tests when everything is assembled
```
