"""``python -m orchestrator.seed`` (``python tasks.py seed``), PRD/11 §7.

1. Bundle: ``var/seed/bundle.json``; if missing, run ``datagen.generate`` (WP8) when present,
   else use the tiny fixture (``contracts/testing/fixtures/tiny_business.json``).
2. Reset ``var/app.db``; tables, settings (``.env`` defaults) and governance state are
   created by the container.
3. ``ports.admin.reset(bundle)`` (in-process sandbox, or HTTP with ``--external-sandbox``).
4. ``books.load_master_data(...)`` plus opening balances and recurring obligations.
5. ``sync_all()`` (POS + bank into the books), then ingest the bundle's receipts.
6. EVENT ``SEED_LOADED`` with the counts and the bundle hash.
7. Print a summary.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from contracts.actions import SYSTEM
from contracts.canonical import params_hash
from contracts.enums import EventType
from contracts.money import fmt
from contracts.seed import SeedBundle
from contracts.testing.builders import load_tiny_bundle
from infra.db import db_path, dispose_all
from infra.paths import DATA_DIR, REPO_ROOT, ensure_dirs
from infra.settings import get_settings
from orchestrator.container import build_app, bundle_path, load_bundle_file, package_present


def load_or_generate_bundle(generate: bool = True) -> tuple[SeedBundle, str]:
    """(bundle, source) where source is "bundle", "datagen" or "tiny"."""
    existing = load_bundle_file()
    if existing is not None:
        return existing, "bundle"
    if generate and package_present("datagen"):
        s = get_settings()
        cmd = [
            sys.executable,
            "-m",
            "datagen.generate",
            "--seed",
            str(s.seed),
            "--as-of",
            s.as_of.isoformat(),
        ]
        subprocess.run(cmd, cwd=REPO_ROOT, check=True)
        generated = load_bundle_file()
        if generated is not None:
            return generated, "datagen"
    return load_tiny_bundle(), "tiny"


def reset_app_db() -> None:
    """Delete var/app.db (and its WAL files) after closing pooled connections."""
    dispose_all()
    base = db_path("app")
    for suffix in ("", "-wal", "-shm"):
        p = base.with_name(base.name + suffix)
        if p.exists():
            p.unlink()


def receipt_path(image: str) -> Path | None:
    p = Path(image)
    for candidate in (p, REPO_ROOT / p, DATA_DIR / "receipts" / p.name):
        if candidate.is_absolute() and candidate.exists():
            return candidate
    return None


def _call_optional(obj: Any, name: str, *args: Any) -> bool:
    fn = getattr(obj, name, None)
    if callable(fn):
        fn(*args)
        return True
    return False


def seed_app(app: Any, bundle: SeedBundle) -> dict[str, Any]:
    """Steps 3-6 on a built app; returns the summary dict."""
    sandbox_counts = app.ports.admin.reset(bundle)
    books = app.books
    books.load_master_data(
        list(bundle.counterparties), list(bundle.payees), list(bundle.invoices), list(bundle.bills)
    )
    # BKS-01 loads these too; the BooksService protocol has no method for them yet, so the
    # orchestrator calls WP3's loaders when they exist (README: contract change request).
    _call_optional(
        books, "load_opening_balances", list(bundle.opening_balances), bundle.history_start
    )
    _call_optional(books, "load_recurring", list(bundle.recurring))
    sync = app.sync_all()
    receipts, missing = [], []
    for r in bundle.receipts:
        path = receipt_path(r.get("image", ""))
        if path is None:
            missing.append(r.get("image", ""))
            continue
        rec = app.capture_receipt(path)
        receipts.append({"id": rec.id, "file": path.name, "status": rec.status.value})
    counts = {
        "counterparties": len(bundle.counterparties),
        "payees": len(bundle.payees),
        "invoices": len(bundle.invoices),
        "bills": len(bundle.bills),
        "bank_transactions": len(bundle.bank_transactions),
        "pos_sales": len(bundle.pos_sales),
        "receipts": len(receipts),
    }
    bundle_hash = params_hash(bundle)
    app.sink.log(
        EventType.SEED_LOADED,
        {"counts": counts, "bundle_sha256": bundle_hash, "seed": bundle.seed},
        SYSTEM,
    )
    return {
        "counts": counts,
        "sandbox": sandbox_counts,
        "sync": sync,
        "receipts": receipts,
        "missing_receipts": missing,
        "bundle_sha256": bundle_hash,
        "cash_cents": books.cash_balance(app.as_of),
        "confirm_queue": len(books.confirm_queue()),
        "scenario_ids": dict(bundle.scenario_ids),
        "packages": dict(app.c.modes),
    }


def print_summary(summary: dict[str, Any], source: str) -> None:
    print(f"seeded from {source} ({bundle_path() if source != 'tiny' else 'tiny fixture'})")
    fakes = [p for p, m in summary["packages"].items() if m == "fake"]
    if fakes:
        print(f"  using fakes for: {', '.join(fakes)}")
    for k, v in summary["counts"].items():
        print(f"  {k:<18} {v}")
    print(f"  cash (1000+1010)   {fmt(summary['cash_cents'])}")
    print(f"  confirm queue      {summary['confirm_queue']}")
    print(f"  untrusted flags    {summary['sync']['flags']}")
    for receipt in summary["receipts"]:
        print(f"  receipt {receipt['file']}: {receipt['status']}")
    for name in summary["missing_receipts"]:
        print(f"  receipt {name}: FILE MISSING")
    for sid, refs in sorted(summary["scenario_ids"].items(), key=lambda kv: int(kv[0][1:])):
        print(f"  {sid:<4} {', '.join(refs)}")
    print(f"  bundle sha256      {summary['bundle_sha256']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m orchestrator.seed")
    ap.add_argument("--external-sandbox", action="store_true", help="seed SANDBOX_URL over HTTP")
    ap.add_argument("--no-generate", action="store_true", help="do not run datagen")
    args = ap.parse_args(argv)
    ensure_dirs()
    bundle, source = load_or_generate_bundle(generate=not args.no_generate)
    reset_app_db()
    # fakes="auto": a package not assembled yet is replaced by its fake (ORC-01)
    app = build_app(inprocess_sandbox=not args.external_sandbox, fakes="auto")
    try:
        summary = seed_app(app, bundle)
        print_summary(summary, source)
        missing = len(summary["missing_receipts"])
        return 1 if missing else 0
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
