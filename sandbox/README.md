# sandbox (WP2)

Self-built simulator of the outside world (PRD/04): MockBank, POS, Messaging and a pretend
lender, with scoped tokens, idempotency, a payment lifecycle, server-side fraud checks and
its own hash-chained operation log. It behaves like a small, strict bank: it re-checks
payees and accounts even though the app's gate already did (defence in depth).

Separate service: imports only `contracts.sandbox_api/seed/money/canonical/enums/clock`,
reads its own settings (`sandbox/settings.py`), never imports app packages.

## Run

- In-process (tests, demo, normal UI): `infra.sandbox_runner.asgi_transport()`; no port.
- External: `python tasks.py sandbox` -> http://127.0.0.1:8100/docs.
  Never run both against the same `var/`.

## Modules

`app` (routes, /health, error handlers, ops-log middleware) · `auth` (read/write/admin
tokens) · `idempotency` · `ratelimit` · `opslog` · `clock` (business date in `meta`) ·
`errors` (catalogue) · `settings` · `db` (one SQLite file per sub-app) · `bank/`
(`service`, `settlement`, `limits`, `router`) · `pos/` · `messaging/` · `lender/` ·
`admin` · `dump_openapi` (+ committed `openapi.json`).

## Decisions where the PRD left room

- `/admin/*` writes need the admin token but not an Idempotency-Key (operator/test actions,
  never app actions); `/admin/*` and `/health` requests are logged in the bank's ops log.
- `openapi.json` is `app.openapi()` as sorted, indented plain JSON (the schema has JSON
  numbers that canonical JSON rejects by design).
- Message `sbx_ref` = `msg_` + sha256(key)[:12]; returned-payment errors and closing an
  unknown payee reuse `NOT_CANCELLABLE` / `PAYEE_UNKNOWN` (the catalogue is fixed).
- Pagination cursors encode the last (date, seq) or (ts, id), so pages stay correct when
  settlement adds rows later.

## Built beyond MUST (cheap, tested)

Cancel, statement, admin clock advance, payee close, payment return, admin limits.
Not built: POS daily summary (OPTIONAL), Plaid/Square/Stripe adapters (OPTIONAL).

## Test alone

`python tasks.py test sandbox` · after any API change: `python -m sandbox.dump_openapi`.
