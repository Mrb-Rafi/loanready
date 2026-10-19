"""Evidence export (PRD/13 §1): ``python tasks.py evidence``, run after ``tasks.py demo``.

Writes into ``evidence/``:

- ``action_log.csv``: the app's hash-chained action log (every gate decision and event).
- ``sandbox_ops.csv``: the sandbox's own operation log (an independent chain per sub-app).
- ``chain_verification.txt`` / ``sandbox_chain_verification.txt``: verification result and
  the final ``row_hash`` of each chain.
- ``test_report.txt``: pytest summary of the default suite (skip with ``--no-tests``).
- ``README.md``: what each file proves.

It reads the state the demo left in ``var/`` and never changes business data; exporting
logs one ``EXPORT_CREATED`` event after the CSV is written (so it is not in this CSV).
"""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
from pathlib import Path
from typing import Any

from contracts.enums import Decision
from contracts.ids import IdFactory, set_id_factory
from infra.paths import REPO_ROOT, ensure_dirs
from orchestrator.container import build_app

EVIDENCE_DIR = REPO_ROOT / "evidence"
PYTEST_ARGS = ["-m", "pytest", "-q", "-m", "not slow and not optional", "-p", "no:randomly"]

README = (Path(__file__).parent / "templates" / "evidence_readme.md").read_text(encoding="utf-8")


def _final_hashes(ops_csv: str) -> dict[str, str]:
    """Last ``row_hash`` per sandbox sub-app (each sub-app keeps its own chain)."""
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(ops_csv)):
        out[row["subapp"]] = row["row_hash"]
    return out


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def export(app: Any, out_dir: Path) -> dict[str, Any]:
    """Write the log files; returns what was found (used by the e2e tests)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = app.audit_rows()
    app.export_csv(out_dir / "action_log.csv")
    broken = app.verify_chain()
    counts = {d.value: app.audit_counts().get(d.value, 0) for d in Decision}
    final = rows[-1].row_hash if rows else ""
    _write(
        out_dir / "chain_verification.txt",
        f"action log rows exported: {len(rows)}\n"
        f"chain: {'OK' if broken is None else f'BROKEN at seq {broken}'}\n"
        f"final row_hash: {final}\n" + "".join(f"{k}: {v}\n" for k, v in counts.items()),
    )
    ops = app.ports.admin.opslog_csv()
    _write(out_dir / "sandbox_ops.csv", ops if ops.endswith("\n") else ops + "\n")
    verify = app.ports.admin.opslog_verify()
    finals = _final_hashes(ops)
    lines = [f"sandbox operation log rows: {max(0, len(ops.splitlines()) - 1)}"]
    for sub, bad in sorted(verify.items()):
        state = "OK" if bad is None else f"BROKEN at seq {bad}"
        lines.append(f"{sub}: {state}; final row_hash: {finals.get(sub, '(no rows)')}")
    _write(out_dir / "sandbox_chain_verification.txt", "\n".join(lines) + "\n")
    return {
        "chain_ok": broken is None,
        "sandbox_ok": all(v is None for v in verify.values()),
        "counts": counts,
        "rows": len(rows),
    }


def test_report(out_dir: Path) -> int:
    """Run the default suite (as ``tasks.py test``) and keep its summary lines."""
    proc = subprocess.run(
        [sys.executable, *PYTEST_ARGS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    keep = [
        ln
        for ln in proc.stdout.splitlines()
        if ln.startswith(("FAILED", "ERROR", "SKIPPED", "=")) or " passed" in ln or " failed" in ln
    ]
    _write(
        out_dir / "test_report.txt",
        f"$ python {' '.join(PYTEST_ARGS)}\nexit code: {proc.returncode}\n\n"
        + "\n".join(keep)
        + "\n",
    )
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.export_evidence", description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=EVIDENCE_DIR)
    ap.add_argument("--no-tests", action="store_true", help="skip test_report.txt")
    args = ap.parse_args(argv)
    ensure_dirs()
    # A separate process: random ids for the one EXPORT_CREATED row, so a seeded factory
    # cannot repeat an id the demo already used.
    set_id_factory(IdFactory(None))
    app = build_app(fakes=False)
    try:
        if not app.audit_rows():
            print("the action log is empty: run `python tasks.py demo` first")
            return 1
        found = export(app, args.out_dir)
        seed = app.c.extras.get("seed", {}).get("seed") or app.c.settings.seed
    finally:
        app.close()
    counts = ", ".join(f"{k} {v}" for k, v in found["counts"].items())
    _write(args.out_dir / "README.md", README.format(seed=seed, counts=counts))
    rc = 0 if args.no_tests else test_report(args.out_dir)
    print(
        f"evidence in {args.out_dir}: {found['rows']} log rows, chain "
        f"{'OK' if found['chain_ok'] else 'BROKEN'}, sandbox chains "
        f"{'OK' if found['sandbox_ok'] else 'BROKEN'}, tests exit {rc}"
    )
    return 0 if found["chain_ok"] and found["sandbox_ok"] and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
