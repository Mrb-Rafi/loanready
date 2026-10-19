# contracts CHANGELOG

Contract change requests (CCR) follow PRD/01 §7.3: write the request under **Proposed**
(who, what, why, backward compatible yes/no). The team lead applies it on the WP0 device,
bumps `CONTRACTS_VERSION`, runs `python tasks.py freeze` and redistributes `contracts/`.

## Proposed

(none)

## 1.2.1 (2026-10-20)

Publication clean-up, no interface change: the internal specification, the build
instructions for the development sessions and the PC-to-PC hand-off notes are no longer part
of the repository. The two instruction files left `FROZEN_ROOT_FILES` and WP0's owned paths
(they are unowned local files now), so a clean clone passes `doctor` without them. The compliance matrix reads regulation names from
`scripts/regulations.json` instead of the specification.

## 1.2.0 (2026-10-20)

Additive: `AppFacade.excluded_bills() -> list[BillDTO]`, the open bills left out of the
forecast as suspected fraud (claimed bank account differs from the stored payee's). The
Forecast page lists them under "Excluded pending verification" (it guessed from assumption
text before and showed nothing). `FakeAppFacade` implements it.

## 1.1.0 LLM settings (2026-10-19)

Contracts unchanged. `infra/settings.py` and `.env.example`: new optional setting
`OPENAI_TOTAL_MAX_USD` (all-time USD cap for prepaid OpenAI credit, default 0 = off),
read by `agent.budget`. Manifest re-frozen.

## 1.1.0 tooling fixes (2026-10-19, clean-clone check)

Contracts unchanged. `infra/` only, manifest re-frozen:

- `infra/doctor.py`, `infra/integration_check.py`: an entry module with no service names
  (ui, scripts, sandbox) is found (doctor) or compiled (integrate), not imported; importing
  `ui.app` ran the Streamlit page (about 50 warnings, and it built the app).
- `infra/handoff.py`: the starter leaves out `.coverage*` and `htmlcov/`.

## 1.1.0 (2026-10-19)

Additive (WP10 CCRs 1 and 3, applied by PC A):

- `AppFacade.drafts() -> list[MessageDraftDTO]`: every draft, oldest first, with status
  `draft`/`sent`, so the UI shows real status after a send (was session-only).
- `AppFacade.open_bills() -> list[BillDTO]`: open bills for the supplier-payment form (was
  a typed bill id). The gate still checks every payment (S6 stays blocked).
- `FakeAppFacade` implements both.

## 1.0.0 starter refresh (2026-10-19)

Contracts unchanged (`CONTRACTS_VERSION` stays 1.0.0). Two frozen root files changed during
the PC B / PC C assembly, so the manifest was re-frozen and a new starter issued:

- `.importlinter`: `ui-facade-only` sets `allow_indirect_imports = True`. ARC-06 limits the
  UI's direct imports; with the real orchestrator assembled, `ui.state ->
  orchestrator.container -> infra` was reported as a violation.
- `pyproject.toml`: `[tool.ruff.format] exclude = ["*.md"]`. ruff 0.17 formats Python
  snippets inside Markdown, which failed `lint` on the WP3/WP4/WP6 hand-off notes.

PCs B and C must copy `contracts/`, `infra/` and the root files from the new starter before
any further hand-off; zips built on the old starter are refused (manifest mismatch).

## 1.0.0 (2026-10-18)

Initial frozen contracts, built from PRD/02_contracts.md. Additions beyond the PRD text,
made up front so no WP needs a CCR for them:

- `AppFacade.data_version()` and `AppFacade.principal()`: the UI cache key and the acting
  principal needed by PRD/12 UI-12 and the approval dialog.
- `contracts.ports.Ports`: the `{bank, pos, messaging, lender, admin}` bundle returned by
  `adapters.build_ports` (PRD/04 SBX-A-01), defined here so fakes and adapters share it.
- `contracts.clock.wall_clock_now()`: real UTC time for tooling (freeze, hand-off,
  assembly), so no module outside `contracts/clock.py` calls `datetime.now()`.
- `contracts.clock.set_clock()` / `reset_clock()` and `contracts.ids.set_id_factory()`:
  installing the process clock and ID factory (tests, demo, container).
- `Untrusted` validates and serialises inside pydantic models as
  `{"value", "source", "source_ref"}` so `LLMRequest.untrusted_blocks` keeps provenance.
- Settings: `OPENAI_PRICE_IN_PER_MTOK`, `OPENAI_PRICE_OUT_PER_MTOK` (PRD/09 AGT-13),
  `BANK_PROVIDER`, `POS_PROVIDER` (PRD/04 §10.1), `LOANREADY_UI_FAKE` (PRD/12).
- `requirements.lock` carries platform markers (`uvloop` not on Windows; `colorama` and
  `tzdata` only on Windows) so one lock installs on Windows 11 and Linux.
- `LoanPackContent.audit_final_hash` / `audit_chain_ok` (optional): the controls section
  of the loan pack needs them (PRD/11 ORC-09, PRD/08 §5 `controls`).
- Constants for shared use: `LOAN_PACK_SECTIONS`, `LENDER_REQUIRED_SECTIONS`
  (`dto_outputs`), `AGEING_BUCKETS` (`dto_books`), `INSTRUCTION_FIELDS`, `binding_for()`,
  `mask_account()`, `mask_contact()`, `PRINCIPALS_BY_ROLE` (`actions`), `CONTROL_IDS`,
  `PERSONAL_LABEL`, `CASH_ACCOUNTS`, `LENDER_IDS`, `OPERATING_ACCOUNT_ID`, settings key
  maps and `owner_of()` (`registry`), `bytes_sha256()` (`canonical`), header names and
  `PAYMENT_FAILURE_CODES` (`sandbox_api`).
- Tables live in their own SQLAlchemy metadata (`contracts.schema.APP_METADATA`) so the
  sandbox's tables never mix into `var/app.db`; timestamps are stored as UTC ISO strings.
- Integration-check rule "sandbox write paths only in adapters/" matches URL string
  literals that start with a write path. Display text such as the approval dialog's
  `"MockBank POST /bank/payments/schedule"` (PRD/03 §7) is allowed in governance.
- `FakeForecast` / `FakeCashPlan` are simplified engines that reproduce the PRD/15 §3.1
  tiny-fixture golden tables (normal and crunch); WP4 builds the real ones.
- An onboarding guide for the development PCs (roles, setup, what WP0 provides,
  Windows-to-Linux rules, hand-off); internal, not published.
