"""Per-PC environment check (CON-25). ``python -m infra.doctor [--ports]``.

Prints a table and exits non-zero if any MUST check fails.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import importlib.util
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import Marker
from packaging.utils import canonicalize_name

from contracts import CONTRACTS_VERSION
from infra.freeze import load_environment, verify_manifest
from infra.paths import REPO_ROOT


@dataclass
class Check:
    name: str
    level: str  # MUST | WARN | INFO
    ok: bool
    detail: str = ""


def parse_lock(text: str) -> dict[str, str]:
    """``{normalised name: version}`` for lock lines whose markers apply to this platform."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        req, _, marker = line.partition(";")
        if marker.strip() and not Marker(marker.strip()).evaluate():
            continue
        name, _, version = req.strip().partition("==")
        out[canonicalize_name(name)] = version.strip()
    return out


def installed_packages() -> dict[str, str]:
    out = {}
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if name and canonicalize_name(name) not in {"loanready", "pip", "setuptools", "wheel"}:
            out[canonicalize_name(name)] = dist.version
    return out


def check_python(expected: str | None, actual: tuple[int, int] = sys.version_info[:2]) -> Check:
    got = f"{actual[0]}.{actual[1]}"
    if expected is None:
        return Check("Python version", "MUST", False, f"{got}; ENVIRONMENT.json missing")
    return Check("Python version", "MUST", got == expected, f"{got} (expected {expected})")


def check_venv(root: Path = REPO_ROOT) -> Check:
    venv = (root / ".venv").resolve()
    inside = Path(sys.prefix).resolve() == venv
    return Check(
        ".venv active",
        "MUST",
        venv.exists() and inside,
        f"interpreter {sys.executable}" if inside else "run through `python tasks.py ...`",
    )


def check_lock(lock: dict[str, str], installed: dict[str, str]) -> Check:
    missing = sorted(n for n in lock if n not in installed)
    wrong = sorted(
        f"{n} {installed[n]}!={v}" for n, v in lock.items() if n in installed and installed[n] != v
    )
    extra = sorted(n for n in installed if n not in lock)
    ok = not (missing or wrong or extra)
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if wrong:
        parts.append("version: " + ", ".join(wrong))
    if extra:
        parts.append("not in lock: " + ", ".join(extra))
    return Check(
        "Packages equal requirements.lock",
        "MUST",
        ok,
        "; ".join(parts) or f"{len(lock)} packages match",
    )


def check_manifest(root: Path = REPO_ROOT) -> Check:
    problems = verify_manifest(root)
    return Check(
        "contracts/MANIFEST.sha256 matches",
        "MUST",
        not problems,
        "; ".join(problems[:8]) + (" …" if len(problems) > 8 else "")
        if problems
        else "all files match",
    )


def check_contracts_version(env: dict | None) -> Check:
    expected = env.get("contracts_version") if env else None
    return Check(
        "CONTRACTS_VERSION",
        "MUST",
        expected == CONTRACTS_VERSION,
        f"{CONTRACTS_VERSION} (ENVIRONMENT.json: {expected})",
    )


def check_var(root: Path = REPO_ROOT) -> Check:
    try:
        from infra.paths import var_dir

        var = var_dir()
    except Exception:
        var = root / "var"
    try:
        var.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=var, delete=True):
            pass
        free = shutil.disk_usage(var).free
    except OSError as exc:
        return Check("var/ writable, ≥ 1 GB free", "MUST", False, str(exc))
    return Check(
        "var/ writable, ≥ 1 GB free", "MUST", free >= 1 << 30, f"{free / (1 << 30):.1f} GB free"
    )


def info_checks(root: Path = REPO_ROOT, ports: bool = False) -> list[Check]:
    from contracts.registry import EXPECTED_PACKAGES

    out = [
        Check(
            ".env present",
            "WARN",
            (root / ".env").exists(),
            "" if (root / ".env").exists() else "copy .env.example to .env",
        )
    ]
    if ports:
        from infra.sandbox_runner import port_in_use

        for port in (8100, 8501):
            busy = port_in_use("127.0.0.1", port)
            out.append(Check(f"port {port} free", "WARN", not busy, "in use" if busy else ""))
    out.append(Check("Tesseract binary", "INFO", bool(shutil.which("tesseract")), "optional"))
    out.append(Check("OpenAI key set", "INFO", bool(os.environ.get("OPENAI_API_KEY")), "optional"))
    for pkg, spec in EXPECTED_PACKAGES.items():
        present = importlib.util.find_spec(spec.entry_module.split(".")[0]) is not None
        entry = present and _is_built(spec.entry_module, spec.service_names)
        out.append(
            Check(
                f"package {pkg} ({spec.wp})",
                "INFO",
                entry,
                "built" if entry else ("skeleton only" if present else "absent"),
            )
        )
    return out


def _is_built(module: str, names: tuple[str, ...]) -> bool:
    """Entry module exists, imports, and exports its service names (not just a skeleton)."""
    try:
        if importlib.util.find_spec(module) is None:
            return False
        mod = importlib.import_module(module)
    except Exception:
        return False
    return all(hasattr(mod, n) for n in names)


def run_checks(root: Path = REPO_ROOT, ports: bool = False) -> list[Check]:
    env = load_environment(root)
    lock_path = root / "requirements.lock"
    lock = parse_lock(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    return [
        check_python(env.get("python") if env else None),
        check_venv(root),
        check_lock(lock, installed_packages()),
        check_manifest(root),
        check_contracts_version(env),
        check_var(root),
        *info_checks(root, ports),
    ]


def print_table(checks: list[Check]) -> None:
    w = max(len(c.name) for c in checks)
    for c in checks:
        status = "OK" if c.ok else ("FAIL" if c.level == "MUST" else c.level)
        print(f"  {status:<5} {c.level:<5} {c.name:<{w}}  {c.detail}")


def must_failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.level == "MUST" and not c.ok]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LoanReady environment check")
    ap.add_argument("--ports", action="store_true", help="also check ports 8100 and 8501")
    args = ap.parse_args(argv)
    checks = run_checks(REPO_ROOT, args.ports)
    print("doctor:")
    print_table(checks)
    bad = must_failures(checks)
    print("doctor: all MUST checks OK" if not bad else f"doctor: {len(bad)} MUST check(s) failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
