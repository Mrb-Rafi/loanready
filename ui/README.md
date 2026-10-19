# ui (WP10): LoanReady Streamlit UI

Spec: `PRD/12_wp10_ui.md`. Built on PC C against `FakeAppFacade`. Imports only `orchestrator`
and `contracts` (ARC-06, checked by `lint-imports`).

## Purpose

The screens a human uses: dashboard, capture, books, forecast, action plan with the
**approval dialog** (Tier 2 human approval), financing comparison and readiness, loan pack,
and the log and security page. The UI never computes money, never decides permissions and
never ranks options: every number is DTO int cents shown through `contracts.money.fmt`, and
every decision comes from the gate via the `AppFacade`.

## Layout

```
ui/
  app.py                  entry: page config, sidebar (all pages), Home dashboard, st.navigation
  state.py                facade choice, st.cache_resource app, cached reads, session principal,
                          approval tokens, flash messages, local file locations
  components/
    approval_dialog.py    st.dialog approval screen (UI-07, UI-08) + pure build_view()
    money.py              fmt/pct wrappers, md() escaping, parse_amount for forms
    tables.py             DataFrame builders (strings only), sort_by_ear, display_party masking
    charts.py             altair forecast band, cap gauge, readiness gauge
    badges.py             tier / decision / band chips, AI label, synthetic-data banner
    feedback.py           friendly messages per ReasonCode (UI-14), attempt() wrappers
  views/
    1_Capture.py 2_Books.py 3_Forecast.py 4_Action_Plan.py
    5_Financing.py 6_Loan_Pack.py 7_Log_and_Security.py
```

## Public API

- Entry point: `streamlit run ui/app.py` (via `python tasks.py ui`).
- `ui.state.get_app() -> AppFacade`, `ui.state.cached(app, method, *args)`.
- `ui.components.approval_dialog.build_view(summary, role, kill_switch, ttl, status)`,
  `render_approval(app, proposal_id)`, `approval_dialog(app, proposal_id)`.

Facade methods used: everything in PRD/12 §2 except `chat` (SHOULD, not built),
`cancel_payment` and `close_period` (P1, no screen).

## How to run and test alone

```
# PowerShell
$env:LOANREADY_UI_FAKE="1"; py -3.12 tasks.py ui      # http://127.0.0.1:8501
# bash
LOANREADY_UI_FAKE=1 py -3.12 tasks.py ui
py -3.12 tasks.py test ui
py -3.12 tasks.py lint
```

First start of Streamlit asks for an e-mail on the console; set
`STREAMLIT_SERVER_HEADLESS=true` to skip it when starting from a script.

## Agreed exception: `os.environ` in `ui/state.py`

`ui` may not import `infra`, so `ui/state.py` reads exactly one variable itself:
`LOANREADY_UI_FAKE`. If it is `"1"`, or `orchestrator.container.build_app` cannot be
imported, the UI uses `contracts.testing.fakes.FakeAppFacade()`; otherwise `build_app()`.
Nothing else in `ui/` reads the environment (PC_GUIDE §4, WP10 note).

## Tests (`tests/ui/`, 31 tests)

PRD/15 lists only smoke tests (T-UI-01..03, a SHOULD item). To keep `tasks.py test ui`
meaningful for the MUST modules, I added minimal tests now: AppTest renders Home and all
7 pages with the sidebar (T-UI-01), approval dialog fields from the gate summary and no edit
fields (T-UI-02), bookkeeper approve disabled for payments and kill-switch toggle hidden for
the bookkeeper (T-UI-03), approve/execute, reject, sandbox-error message, blocked-action
panel, verify chain, draft-to-queue flow, and unit tests of the display helpers and cached
reads.

## Proposed (contract change requests; not applied, local workarounds in place)

1. **Applied in contracts 1.1.0 (PC A).** **`AppFacade.open_bills() -> list[BillDTO]`** (who: WP10 → WP9/WP0; additive, backward
   compatible). Why: the supplier-payment form needs a list of open bills; the facade has
   none. Workaround: `views/4_Action_Plan.py` calls `open_bills()` only if the facade has it
   (`getattr`), else offers bill ids from forecast drivers and cash-plan drafts plus a
   "type a bill id" field.
2. **`forecast_explanation()` / `financing_explanation()` return `(text, ai_label,
   generated_by)`** or a small DTO (additive as new methods). Why: the UI cannot tell
   template text from LLM text, so it labels every explanation "AI-generated explanation of
   figures calculated by LoanReady" (REG-AI-06), which over-labels template text in mock mode.
3. **Applied in contracts 1.1.0 (PC A).** **`AppFacade.drafts() -> list[MessageDraftDTO]`** (additive). Why: drafts exist only as
   return values of `draft_reminders()` / `draft_delay_requests()`, so the UI keeps them in
   `st.session_state`; their `status` does not update after sending. Workaround: the UI marks
   a draft "sending proposed" locally after `propose_send`.
4. **Paths from the facade** (`export_path()`, `uploads_dir()`) or `capture_receipt(bytes,
   name)`. Why: `ui` cannot use `infra.paths`. Workaround: the real app writes
   `<repo>/var/exports/action_log.csv` (PC A moved it out of WP11's `evidence/`) and `<repo>/var/uploads/` (computed from `__file__`);
   fake mode writes under the system temp folder so tests never touch the repo.
5. **`health()` keys for UI-16/UI-17**: `sandbox_problem` (str | None) for the
   SANDBOX_MODE/port mismatch, and in `ai`: `remote_budget`, `routes` (task → backend). The
   UI shows these if present; with the fake they are absent.

## Assumptions and choices (for PC A)

- **`ui/views/` instead of `ui/pages/`** (PRD/12 §1 says `pages/`). A folder named `pages/`
  next to `app.py` switches Streamlit 1.66 into legacy multipage mode on reruns (after a
  dialog `st.rerun()` the `app.py` frame and sidebar disappeared). Routing uses
  `st.navigation` with the same file names. URLs: `/Capture`, `/Action_Plan`, ...
- **Markdown `$` escaping**: Streamlit renders `$...$` as LaTeX, so text with two amounts
  broke. All markdown text with money goes through `money_md()` / `md()`.
- **Charts never use floats**: the data frames hold int cents; the division by 100 happens
  in the Vega-Lite spec (`transform_calculate`) in the browser.
- **Approve button disabling** follows the PRD/03 §2 role table (bookkeeper cannot approve
  SCHEDULE_SUPPLIER_PAYMENT / CANCEL_PAYMENT) and the kill switch. This is display only; the
  real gate re-checks. `FakeGate` does not check roles, so in fake mode the UI is the only
  check.
- **Approval flow**: "Approve and execute" calls `approve()` (token kept in
  `st.session_state` only, never shown) then `execute()`.
- **Capture page**: one "Sync POS and bank" button (`sync_all()` is the only facade call;
  PRD shows two buttons). Untrusted excerpts (OCR, flags) are shown in code blocks, never
  rendered as markdown.
- **"Excluded pending verification"** (forecast page): `ForecastDTO` has no field for it; the
  UI lists assumptions that mention "excluded" or "verification".
- **UI-16 sandbox mismatch banner**: shown only from `health()` data (see Proposed 5); the UI
  does not probe port 8100 itself.
- **Cache**: `st.cache_data(ttl=None)` keyed by `id(app)` and `data_version()`. Time-based
  changes (approval expiry) show after the next write or page reload.
- **Not built (SHOULD)**: sidebar chat, `demo.py` stepper (Home shows a disabled "Run Rina
  story" button saying so).
