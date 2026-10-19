# scripts (WP11)

Headless demo, evidence export and generated documents (PRD/13). Run in this order:

| Command | Module | Writes |
|---|---|---|
| `python tasks.py demo` | `scripts.run_demo` | `evidence/demo_summary.json`, `evidence/action_log.csv`; exits 1 if any S1-S10 check fails |
| `python tasks.py evidence` | `scripts.export_evidence` | `evidence/` action log, sandbox ops log, chain checks, `test_report.txt` (`--no-tests` skips it), `README.md` |
| `python tasks.py docs` | `scripts.dump_tiers`, `scripts.dump_controls`, `scripts.licenses` | `docs/generated/tier_table.md`, `docs/generated/compliance_matrix.md`, `docs/compliance_matrix.md`, `THIRD_PARTY.md` |

Tests: `tests/e2e/` (T-E2E-01..03): `python tasks.py test e2e` (`--all` adds the determinism
run). Templates for generated prose are in `scripts/templates/`.

Not built (SHOULD/OPTIONAL): `trace.py` (traceability table), `docs/video_script.md`,
`docs/ai_use_statement.md`, `anchor_hash.py`.
