"""Freeze manifest and environment pin (CON-23, CON-24). Team lead, WP0 device only.

``python -m infra.freeze [--device NAME]`` writes ``contracts/MANIFEST.sha256`` and
``contracts/ENVIRONMENT.json``. Run after every contract change request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

from contracts import CONTRACTS_VERSION
from contracts.clock import wall_clock_now
from contracts.registry import FROZEN_ROOT_FILES, is_ignored
from infra.paths import REPO_ROOT

MANIFEST_NAME = "MANIFEST.sha256"
ENVIRONMENT_NAME = "ENVIRONMENT.json"
MANIFEST_DIRS = ("contracts", "infra")
EXCLUDED = {f"contracts/{MANIFEST_NAME}", f"contracts/{ENVIRONMENT_NAME}"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".txt", ".lock", ".cfg", ".ini", ".yml", ".yaml"}
TEXT_NAMES = {".env.example"}


def is_text(path: Path) -> bool:
    """Text files are hashed with CRLF normalised to LF (same hash on Windows and Linux)."""
    name = path.name
    return (
        path.suffix in TEXT_SUFFIXES
        or name in TEXT_NAMES
        or (name.startswith(".") and "." not in name[1:])
    )


def file_hash(path: Path) -> str:
    data = path.read_bytes()
    if is_text(path):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def manifest_files(root: Path = REPO_ROOT) -> list[str]:
    """Sorted POSIX paths covered by the manifest."""
    files: set[str] = set()
    for d in MANIFEST_DIRS:
        base = root / d
        if base.exists():
            for p in base.rglob("*"):
                rel = p.relative_to(root).as_posix()
                if p.is_file() and not is_ignored(rel) and rel not in EXCLUDED:
                    files.add(rel)
    for rel in FROZEN_ROOT_FILES:
        if (root / rel).is_file():
            files.add(rel)
    return sorted(files)


def build_manifest(root: Path = REPO_ROOT) -> str:
    """One line per file: ``<sha256>  <posix path>\\n``, sorted by path."""
    return "".join(f"{file_hash(root / rel)}  {rel}\n" for rel in manifest_files(root))


def parse_manifest(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if line.strip():
            digest, rel = line.split("  ", 1)
            out[rel] = digest
    return out


def verify_manifest(root: Path = REPO_ROOT) -> list[str]:
    """Problems: files that differ, are missing, or are new under contracts/ and infra/."""
    path = root / "contracts" / MANIFEST_NAME
    if not path.exists():
        return [
            f"missing contracts/{MANIFEST_NAME} (run `python tasks.py freeze` on the WP0 device)"
        ]
    expected = parse_manifest(path.read_text(encoding="utf-8"))
    problems = []
    for rel, digest in expected.items():
        f = root / rel
        if not f.is_file():
            problems.append(f"missing: {rel}")
        elif file_hash(f) != digest:
            problems.append(f"changed: {rel}")
    for rel in manifest_files(root):
        if rel not in expected:
            problems.append(f"not in manifest: {rel}")
    return problems


def text_sha256(path: Path) -> str:
    return file_hash(path)


def environment(
    root: Path = REPO_ROOT, device: str | None = None, frozen_at: str | None = None
) -> dict:
    manifest = root / "contracts" / MANIFEST_NAME
    return {
        "contracts_version": CONTRACTS_VERSION,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "requirements_lock_sha256": text_sha256(root / "requirements.lock"),
        "manifest_sha256": text_sha256(manifest),
        "frozen_at": frozen_at or wall_clock_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frozen_by": device or platform.node() or "unknown",
    }


def freeze(root: Path = REPO_ROOT, device: str | None = None) -> dict:
    (root / "contracts" / MANIFEST_NAME).write_text(
        build_manifest(root), encoding="utf-8", newline="\n"
    )
    env = environment(root, device)
    (root / "contracts" / ENVIRONMENT_NAME).write_text(
        json.dumps(env, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return env


def load_environment(root: Path = REPO_ROOT) -> dict | None:
    p = root / "contracts" / ENVIRONMENT_NAME
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Freeze contracts/ and infra/ (WP0 device only)")
    ap.add_argument("--device", default=None)
    args = ap.parse_args(argv)
    env = freeze(REPO_ROOT, args.device)
    n = len(manifest_files(REPO_ROOT))
    print(
        f"froze {n} files; contracts {env['contracts_version']}; "
        f"manifest {env['manifest_sha256'][:16]}…"
    )
    print(
        f"lock {env['requirements_lock_sha256'][:16]}…; python {env['python']}; "
        f"by {env['frozen_by']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
