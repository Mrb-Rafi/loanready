"""Hand-off packaging (CON-26).

``python -m infra.handoff WP3 WP4 [--device NAME] [--allow-failing]`` runs doctor and each
WP's tests, then zips exactly the WP's owned files with a ``handoff.json`` manifest into
``handoff/out/``. ``python -m infra.handoff WP0 --starter`` zips the whole repository
(without .venv, var, handoff, .env, caches, .git) as the starter copy for new PCs.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from contracts import CONTRACTS_VERSION
from contracts.canonical import file_sha256
from contracts.clock import wall_clock_now
from contracts.registry import OWNERSHIP, is_ignored, owner_of
from infra.freeze import load_environment
from infra.paths import HANDOFF_DIR, REPO_ROOT

STARTER_EXCLUDE_TOP = {".venv", "var", "handoff", ".git", ".env", "evidence"}
_SUMMARY = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)")


def normalise_wp(wp: str) -> str:
    w = wp.upper()
    w = w if w.startswith("WP") else f"WP{w}"
    if w not in OWNERSHIP:
        raise SystemExit(f"unknown work package {wp!r}; expected one of {', '.join(OWNERSHIP)}")
    return w


def owned_files(wp: str, root: Path = REPO_ROOT) -> list[str]:
    """Every existing file whose owner is ``wp`` (most-specific ownership rule)."""
    out = set()
    for owned in OWNERSHIP[wp]:
        p = root / owned
        candidates = [p] if p.is_file() else (list(p.rglob("*")) if p.is_dir() else [])
        for f in candidates:
            if f.is_file():
                rel = f.relative_to(root).as_posix()
                if not is_ignored(rel) and owner_of(rel) == wp:
                    out.add(rel)
    return sorted(out)


def test_dirs(wp: str, root: Path = REPO_ROOT) -> list[str]:
    return [
        o.rstrip("/")
        for o in OWNERSHIP[wp]
        if o.startswith("tests/") and o.endswith("/") and (root / o).is_dir()
    ]


def parse_pytest_summary(text: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    tail = "\n".join(text.strip().splitlines()[-5:])
    for n, kind in _SUMMARY.findall(tail):
        key = "failed" if kind.startswith("error") else kind
        if key in counts:
            counts[key] += int(n)
    return counts


def run_tests(paths: list[str], root: Path = REPO_ROOT) -> tuple[dict[str, int], str]:
    if not paths:
        return {"passed": 0, "failed": 0, "skipped": 0}, "no tests"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-m", "not slow and not optional", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    counts = parse_pytest_summary(proc.stdout)
    if proc.returncode not in (0, 5) and counts["failed"] == 0:
        counts["failed"] = 1  # collection or usage error
    return counts, proc.stdout[-2000:]


def git_commit(root: Path = REPO_ROOT) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def build_zip(wp: str, files: list[str], meta: dict, out_dir: Path, root: Path = REPO_ROOT) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = wall_clock_now().strftime("%Y%m%d-%H%M")
    path = out_dir / f"{wp}_{meta['device']}_{stamp}.zip"
    meta = {**meta, "files": [{"path": f, "sha256": file_sha256(root / f)} for f in files]}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(root / f, f)
        zf.writestr("handoff.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return path


def handoff(
    wps: list[str],
    device: str,
    allow_failing: bool = False,
    root: Path = REPO_ROOT,
    out_dir: Path | None = None,
    skip_doctor: bool = False,
    skip_tests: bool = False,
) -> list[Path]:
    env = load_environment(root)
    if env is None:
        raise SystemExit("contracts/ENVIRONMENT.json missing: this copy was never frozen")
    if not skip_doctor:
        from infra.doctor import must_failures, print_table, run_checks

        checks = run_checks(root)
        if must_failures(checks):
            print_table(checks)
            raise SystemExit("doctor failed: fix the environment before handing off")
    zips = []
    for wp in wps:
        if wp == "WP0":
            raise SystemExit("WP0 is distributed with --starter, never assembled")
        files = owned_files(wp, root)
        if not files:
            raise SystemExit(f"{wp}: no owned files found")
        counts, log = (
            ({"passed": 0, "failed": 0, "skipped": 0}, "skipped")
            if skip_tests
            else run_tests(test_dirs(wp, root), root)
        )
        print(
            f"{wp}: tests passed={counts['passed']} failed={counts['failed']} "
            f"skipped={counts['skipped']}"
        )
        if counts["failed"] and not allow_failing:
            print(log)
            raise SystemExit(
                f"{wp}: tests failing; fix them or use --allow-failing for a partial hand-off"
            )
        meta = {
            "wp": wp,
            "device": device,
            "created_at": wall_clock_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "contracts_version": CONTRACTS_VERSION,
            "manifest_sha256": env["manifest_sha256"],
            "requirements_lock_sha256": env["requirements_lock_sha256"],
            "python": platform.python_version(),
            "git_commit": git_commit(root),
            "tests": counts,
        }
        path = build_zip(wp, files, meta, out_dir or HANDOFF_DIR / "out", root)
        print(f"{wp}: {len(files)} files -> {path}")
        zips.append(path)
    return zips


def starter_files(root: Path = REPO_ROOT) -> list[str]:
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and rel.split("/")[0] not in STARTER_EXCLUDE_TOP and not is_ignored(rel):
            out.append(rel)
    return sorted(out)


def starter(device: str, root: Path = REPO_ROOT, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or HANDOFF_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"starter_{wall_clock_now().strftime('%Y%m%d-%H%M')}.zip"
    files = starter_files(root)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(root / f, f)
    print(
        f"starter: {len(files)} files, {path.stat().st_size / 1e6:.1f} MB -> {path} (from {device})"
    )
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pack work packages for the final PC")
    ap.add_argument("wps", nargs="+")
    ap.add_argument("--device", default=platform.node() or "pc")
    ap.add_argument("--allow-failing", action="store_true")
    ap.add_argument(
        "--starter", action="store_true", help="WP0 only: zip the whole repo for new PCs"
    )
    args = ap.parse_args(argv)
    device = re.sub(r"[^A-Za-z0-9_-]", "-", args.device)
    wps = [normalise_wp(w) for w in args.wps]
    if args.starter:
        if wps != ["WP0"]:
            raise SystemExit("--starter is only for WP0")
        starter(device)
        return 0
    handoff(wps, device, args.allow_failing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
