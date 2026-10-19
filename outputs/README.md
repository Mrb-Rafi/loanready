# outputs (WP6): reminders, delay requests and the loan pack

Customer payment reminders and supplier delay requests whose numbers are computed and
checked in code (the LLM may only word them), and the deterministic loan-pack PDF with a
content hash. PRD/08.

## Public API

`outputs.service.Outputs(engine, clock, ids, settings)` implements
`contracts.services.OutputsService`. Optional keyword arguments: `business_name` (else
`settings.business_name` if present, else "Rina's Bakery") and `packs_dir` (else
`infra.paths.var_dir() / "packs"`). `outputs.templates.TemplateWording` is the
`WordingProvider` fallback.

| Module | What it does |
|---|---|
| `facts.py` | `reminder_facts` (tone FRIENDLY 1-7 / FIRM 8-21 / FINAL 22+; NOT_OVERDUE raises), `delay_facts` (no penalty, not essential, within grace) |
| `templates.py` | the exact PRD/08 §3 templates and the two AI labels |
| `postcheck.py` | invoice/bill number, amount, dates present; no other amount, URL, account, phone, threat words; <= 1,200 chars |
| `drafts.py` | wording -> post-check -> template fallback -> AI label as last body line -> `body_sha256` |
| `loanpack/` | `builder.build_pdf`, one function per section, styles (charts are SHOULD: sales trend is a table) |
| `repo.py`, `service.py` | outbox_drafts, loan_packs, lender_submissions |

## For the orchestrator (PC A)

- `drafts()` returns every draft, oldest first, with status `draft` or `sent`.
- `mark_sent(draft_id, sbx_ref)` changes only status and `sbx_ref`; marking twice raises.
  Drafts are never edited: a new wording is a new draft id and hash.
- After `draft_reminder` / `draft_delay_request`, `outputs.last_fallback` is True when the
  post-check replaced an LLM wording with the template (log `LLM_FALLBACK_USED`);
  `outputs.last_problems` lists why.
- Loan packs: `var/packs/loan_pack_{as_of}_{pack_id}.pdf` plus a `.sections.json` sidecar.
  `LoanPackDTO.path` is the **absolute** POSIX path, so the gate can re-hash the file.
  `metadata` holds business, as_of, amount, term, framework, sections, `content_hash`,
  `sha256`, `pack_id`. `verify_pack` re-hashes the file.
- Set the business name once after seeding (it is not in settings):
  `Outputs(..., business_name=bundle.business["name"])` or
  `outputs.business_name = bundle.business["name"]`.

## Test alone

```
python tasks.py test outputs     # 34 tests, coverage 95%
```

## Loan pack details

- A4, platypus, built-in Helvetica; one outline entry per section key (`/Title (pnl)` in
  the bytes); 13 sections in `LOAN_PACK_SECTIONS` order, including the lender's 9.
- Footer: `LoanReady pack {pack_id} · page n/N · content hash {params_hash(content)[:16]}`.
- Deterministic: `invariant=1`, creation date = the content's AS_OF; same content and
  pack id give byte-identical files (tested).
- Account-like numbers are masked to the last 4 digits everywhere in the pack
  (`SBX-GM-****2277`, `****4433`); the audit-log hash is shown in full.
- Tables adapt to the columns present in each statement (monthly + total from WP3; one
  period column from the fakes).
- The going-concern note is printed when `going_concern_note` is set or the forecast
  flag is on.

## Not built (SHOULD)

Sending delay requests (the drafts exist; sending is the orchestrator's Tier 2 path) and
`loanpack/charts.py` (the sales trend and forecast band are tables).

## Proposed

None. (A `business_name` setting would remove the default above; optional.)

**PC A: read `HANDOFF_NOTES.md` in this folder first** (integration steps, decisions, changes).
