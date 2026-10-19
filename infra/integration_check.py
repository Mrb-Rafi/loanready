"""Integration check (PRD/01 §7.4). ``python -m infra.integration_check``. Inspects, never edits.

0. doctor checks; ownership of the working tree; sandbox openapi hash
1. expected packages present and exporting their service
2. REQUIRES_CONTRACTS matches the contracts major
3. lint-imports (layering)
4. static checks: float on money, wall-clock calls, sandbox write URLs, write-token reads
5. pytest per package, then tests/integration, then tests/e2e
6. summary table
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from contracts.canonical import file_sha256
from contracts.registry import (
    EXPECTED_PACKAGES,
    FROZEN_ROOT_FILES,
    is_ignored,
    is_unowned,
    owner_of,
)
from infra.handoff import parse_pytest_summary
from infra.paths import REPO_ROOT

CODE_DIRS = (
    "contracts",
    "infra",
    "governance",
    "sandbox",
    "adapters",
    "books",
    "planning",
    "financing",
    "outputs",
    "agent",
    "datagen",
    "orchestrator",
    "ui",
    "scripts",
)
# Patterns built from pieces so this file does not match itself. They match URL string
# literals that *start* with a sandbox write path, not display text such as
# "MockBank POST /bank/payments/schedule" (the approval dialog's sandbox_target, PRD/03 §7).
_Q = "[\"']"
_WRITE_URL_RE = re.compile(
    _Q
    + r"/(?:bank/payments/"
    + "schedule|messaging/"
    + "send|lender/"
    + "applications)"
    + r"|"
    + _Q
    + r"/[^\"'\s]*/"
    + "cancel"
    + _Q
)
_WRITE_TOKEN = "SANDBOX_" + "WRITE_TOKEN"
_NOW_RE = re.compile(r"\b(?:datetime\.(?:now|utcnow|today)|date\.today)\(")
_FLOAT_RE = re.compile(r"\bfloat\(")
_MONEY_HINT = re.compile(r"_cents|Decimal|amount|money")


@dataclass
class Row:
    package: str
    wp: str
    present: bool
    version_ok: bool | None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    note: str = ""


def py_files(root: Path, dirs: tuple[str, ...] = CODE_DIRS) -> list[Path]:
    out = []
    for d in dirs:
        base = root / d
        if base.exists():
            out.extend(
                p for p in base.rglob("*.py") if not is_ignored(p.relative_to(root).as_posix())
            )
    return sorted(out)


def static_checks(root: Path = REPO_ROOT) -> list[str]:
    """Rule breaks as ``path:line: message``."""
    problems = []
    for path in py_files(root, CODE_DIRS + ("tests",)):
        rel = path.relative_to(root).as_posix()
        if rel == "infra/integration_check.py":
            continue
        is_test = rel.startswith("tests/")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            problems.append(f"{rel}: not UTF-8")
            continue
        for n, line in enumerate(lines, start=1):
            code = line.split("#", 1)[0]
            if not is_test and _FLOAT_RE.search(code) and _MONEY_HINT.search(code):
                problems.append(f"{rel}:{n}: float on money")
            if not is_test and rel != "contracts/clock.py" and _NOW_RE.search(code):
                problems.append(f"{rel}:{n}: wall-clock call outside contracts/clock.py")
            allowed_write = rel.startswith(
                ("adapters/", "sandbox/", "tests/sandbox/", "tests/adapters/")
            )
            if not allowed_write and _WRITE_URL_RE.search(code):
                problems.append(f"{rel}:{n}: sandbox write path outside adapters/")
            token_ok = rel in (
                "adapters/_secrets.py",
                "infra/settings.py",
                "contracts/testing/settings.py",
            ) or rel.startswith(("sandbox/", "tests/"))
            if not token_ok and _WRITE_TOKEN in code:
                problems.append(
                    f"{rel}:{n}: {_WRITE_TOKEN} referenced outside adapters/_secrets.py"
                )
    return problems


def ownership_problems(root: Path = REPO_ROOT) -> list[str]:
    """Files in the working tree that no WP owns (outside PRD/, var/, .venv/ ...)."""
    problems = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if is_ignored(rel) or is_unowned(rel) or rel in FROZEN_ROOT_FILES:
            continue
        if owner_of(rel) is None:
            problems.append(f"unowned file: {rel}")
    return problems


def openapi_check(root: Path = REPO_ROOT) -> str:
    committed = root / "sandbox" / "openapi.json"
    if importlib.util.find_spec("sandbox") is None or not committed.exists():
        return "skipped (sandbox or sandbox/openapi.json absent)"
    try:
        from infra.sandbox_runner import load_sandbox_app

        app = load_sandbox_app()
    except Exception as exc:  # report, do not crash the whole check
        return f"FAIL: sandbox app does not import: {exc}"
    try:
        import httpx

        from infra.sandbox_runner import asgi_transport

        transport = asgi_transport(app)
        with httpx.Client(transport=transport, base_url="http://sandbox.local") as client:
            got = client.get("/health").json().get("openapi_sha256")
        transport.close()
    except Exception as exc:
        return f"FAIL: /health: {exc}"
    want = file_sha256(committed)
    return (
        "OK"
        if got == want
        else f"FAIL: /health openapi_sha256 {str(got)[:12]}… != committed {want[:12]}…"
    )


def entry_present(module: str, names: tuple[str, ...]) -> tuple[bool, str]:
    try:
        if importlib.util.find_spec(module) is None:
            return False, "not built"
    except ModuleNotFoundError:
        return False, "absent"
    if not names:
        # no exports to check (ui, scripts, sandbox): compile instead of importing, since
        # importing ui.app runs the Streamlit page; their own tests cover import errors
        spec = importlib.util.find_spec(module)
        try:
            source = Path(str(spec.origin)).read_text(encoding="utf-8") if spec else ""
            compile(source, str(spec.origin if spec else module), "exec")
        except (OSError, SyntaxError, ValueError) as exc:
            return False, f"does not compile: {exc}"
        return True, ""
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return False, f"import error: {exc}"
    missing = [n for n in names if not hasattr(mod, n)]
    return (not missing, f"missing export {', '.join(missing)}" if missing else "")


def run_pytest(paths: list[str], root: Path = REPO_ROOT) -> tuple[int, int, int, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-m", "not slow and not optional", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    c = parse_pytest_summary(proc.stdout)
    return c["passed"], c["failed"], c["skipped"], proc.returncode


def lint_imports(root: Path = REPO_ROOT) -> tuple[bool, str]:
    exe = Path(sys.executable).parent / (
        "lint-imports.exe" if sys.platform == "win32" else "lint-imports"
    )
    cmd = [str(exe)] if exe.exists() else [sys.executable, "-m", "importlinter.cli"]
    proc = subprocess.run(
        cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    tail = proc.stdout.strip().splitlines()[-3:] if proc.stdout else []
    return proc.returncode == 0, " | ".join(tail)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Integration check (inspects, never edits)")
    ap.add_argument("--no-tests", action="store_true")
    args = ap.parse_args(argv)
    root = REPO_ROOT
    failures: list[str] = []

    print("[0] doctor")
    from infra.doctor import must_failures, print_table, run_checks

    checks = run_checks(root)
    bad = must_failures(checks)
    print_table([c for c in checks if c.level == "MUST"])
    failures += [f"doctor: {c.name}" for c in bad]
    own = ownership_problems(root)
    print(f"[0] ownership: {'OK' if not own else f'{len(own)} problem(s)'}")
    for p in own[:20]:
        print(f"      {p}")
    failures += own
    oa = openapi_check(root)
    print(f"[0] sandbox openapi: {oa}")
    if oa.startswith("FAIL"):
        failures.append(f"openapi: {oa}")

    print("[1-2] packages")
    from infra.versions import package_versions

    versions = {v.package: v for v in package_versions()}
    rows: list[Row] = []
    for pkg, spec in EXPECTED_PACKAGES.items():
        present, note = entry_present(spec.entry_module, spec.service_names)
        v = versions[pkg]
        rows.append(Row(pkg, spec.wp, present, v.ok if v.present else None, note=note))
        if v.present and not v.ok:
            failures.append(f"{pkg}: REQUIRES_CONTRACTS {v.requires!r} does not match")
        if present and note:
            failures.append(f"{pkg}: {note}")

    ok, detail = lint_imports(root)
    print(f"[3] lint-imports: {'OK' if ok else 'FAIL'} {detail}")
    if not ok:
        failures.append("layering (lint-imports)")

    problems = static_checks(root)
    print(f"[4] static checks: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    for p in problems[:30]:
        print(f"      {p}")
    failures += problems

    if not args.no_tests:
        print("[5] tests")
        test_targets = [("contracts", "WP0", "tests/contracts")]
        test_targets += [
            (pkg, EXPECTED_PACKAGES[pkg].wp, f"tests/{pkg}") for pkg in EXPECTED_PACKAGES
        ]
        test_targets += [("integration", "WP9", "tests/integration"), ("e2e", "WP11", "tests/e2e")]
        by_name = {r.package: r for r in rows}
        for name, wp, path in test_targets:
            if not (root / path).is_dir():
                continue
            passed, failed, skipped, rc = run_pytest([path], root)
            if rc not in (0, 5) and failed == 0:
                failed = 1
            row = by_name.get(name)
            if row is None:
                row = Row(name, wp, True, None)
                rows.append(row)
                by_name[name] = row
            row.passed, row.failed, row.skipped = passed, failed, skipped
            print(f"      {path}: {passed} passed, {failed} failed, {skipped} skipped")
            if failed:
                failures.append(f"tests failing: {path}")

    print("[6] summary")
    print(
        f"  {'package':<16}{'WP':<6}{'present':<9}{'version':<9}"
        f"{'passed':>7}{'failed':>7}{'skipped':>8}  note"
    )
    for r in rows:
        ver = "-" if r.version_ok is None else ("ok" if r.version_ok else "BAD")
        print(
            f"  {r.package:<16}{r.wp:<6}{('yes' if r.present else 'no'):<9}{ver:<9}"
            f"{r.passed:>7}{r.failed:>7}{r.skipped:>8}  {r.note}"
        )
    missing = [r.package for r in rows if not r.present and r.package in EXPECTED_PACKAGES]
    if missing:
        print(f"  not yet assembled (fakes used): {', '.join(missing)}")
    print("integrate: OK" if not failures else f"integrate: {len(failures)} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
