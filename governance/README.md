# governance (WP1)

Permission gate between the agent and every side effect (PRD/03). Plain code, outside the
model, decides what may run; every decision lands in a hash-chained, append-only log.

## Public API

- `build_governance(engine, clock, ids, lookup, executor, settings) -> (Gate, AuditLog, Controls)`:
  creates tables if needed and seeds missing settings from `.env` defaults.
- `Gate` (`GateService`): `evaluate`, `approve`, `reject`, `execute`, `pending`, `proposal`,
  `cap_usage`, `set_kill_switch`, `update_setting`, `settings`, `tier_table`, `ticket_verifier`.
- `AuditLog` (`AuditLogService`): `append_decision`, `log_event`, `rows`, `verify_chain`,
  `export_csv`, `counts`, `last_hash`. No update or delete path exists.
- `Controls` (`ControlsRegistry`): `controls()`, every CTL id of PRD/14 §3.
- `governance.tiers`: `TIER_TABLE`, `BLOCKED_TYPES`, `ROLE_RULES`, `describe()`.

## Modules

`tiers` (tier table, blocked set, role rules) · `gate` (pipeline, approve/reject/execute,
Tier 3 path) · `checks` (entity checks) · `caps` · `approvals` (parameter-bound,
single-use, 15-minute tokens; maker-checker) · `tickets` (HMAC tickets; `_mint` used only
by `gate`) · `audit` · `controls` · `settings_store` · `repo` · `service`.

## Decisions where the PRD left room

- Day cap window = money actions with the same **value date** (today if none); per-payee
  week = value dates within 6 days either side. Pending, approved and executed proposals
  count; rejected, failed, expired and blocked do not.
- Messages-per-recipient and the 7-day reminder rate count sends created in the last 7
  days (pending, approved or executed).
- The "first payment to this payee in 30 days" warning uses `first_paid_on` (no
  last-payment date exists in the contracts).
- A check that fails at execution (entities or caps changed after approval) marks the
  proposal `BLOCKED`; a changed params hash marks it `FAILED`; expiry marks it `EXPIRED`.
- A Tier 3 action over a cap is downgraded: an EVENT row with reason `TIER3_DOWNGRADED`,
  then a Tier 2 proposal (caps are re-checked at execution).
- Deleting the **last** log rows cannot be detected by the chain alone (any earlier
  deletion, edit or reorder is); the exported CSV row count and final hash cover that.

## Not built

`checks.py` watchlist screening (OPTIONAL, CTL-PAY-06) and `selfcheck.py` (OPTIONAL).

## Test alone

```
python tasks.py test governance
```
