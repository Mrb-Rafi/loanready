"""T-CON-04 / T-CON-05 / CTL-CHG-03: freeze manifest and doctor checks."""

from __future__ import annotations

import json
from pathlib import Path

from contracts import CONTRACTS_VERSION
from infra.doctor import check_contracts_version, check_lock, check_python, parse_lock
from infra.freeze import (
    MANIFEST_NAME,
    build_manifest,
    environment,
    file_hash,
    manifest_files,
    verify_manifest,
)


def make_repo(root: Path) -> Path:
    for rel, text in {
        "contracts/__init__.py": 'CONTRACTS_VERSION = "1.0.0"\n',
        "contracts/money.py": "X = 1\n",
        "infra/db.py": "Y = 2\n",
        "pyproject.toml": "[tool]\n",
        "requirements.lock": "a==1\n",
        "tasks.py": "pass\n",
        "tests/conftest.py": "\n",
        "governance/gate.py": "not frozen\n",
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    return root


def freeze_repo(root: Path) -> None:
    (root / "contracts" / MANIFEST_NAME).write_text(
        build_manifest(root), encoding="utf-8", newline="\n"
    )


def test_crlf_and_lf_hash_identically(tmp_path: Path) -> None:
    """T-CON-04 proves CON-23: CRLF and LF copies of a text file hash identically."""
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_bytes(b"x = 1\ny = 2\n")
    b.write_bytes(b"x = 1\r\ny = 2\r\n")
    assert file_hash(a) == file_hash(b)
    c, d = tmp_path / "c.png", tmp_path / "d.png"
    c.write_bytes(b"\x89PNG\n")
    d.write_bytes(b"\x89PNG\r\n")
    assert file_hash(c) != file_hash(d)  # binaries hashed raw
    dot = tmp_path / ".importlinter"
    dot.write_bytes(b"[x]\r\n")
    dot2 = tmp_path / ".gitattributes"
    dot2.write_bytes(b"[x]\n")
    assert file_hash(dot) == file_hash(dot2)


def test_manifest_scope_and_determinism(tmp_path: Path) -> None:
    """T-CON-04 proves CON-23: covers contracts/, infra/ and frozen root files only; freeze
    output is deterministic."""
    root = make_repo(tmp_path)
    files = manifest_files(root)
    assert "governance/gate.py" not in files
    assert {"contracts/money.py", "infra/db.py", "pyproject.toml", "tests/conftest.py"} <= set(
        files
    )
    assert build_manifest(root) == build_manifest(root)
    line = build_manifest(root).splitlines()[0]
    assert len(line.split("  ")[0]) == 64


def test_one_byte_change_reported(tmp_path: Path) -> None:
    """T-CON-04 proves CON-23: a one-byte change, a missing file and a new file are reported."""
    root = make_repo(tmp_path)
    freeze_repo(root)
    assert verify_manifest(root) == []
    (root / "contracts" / "money.py").write_text("X = 2\n", encoding="utf-8")
    (root / "infra" / "new.py").write_text("", encoding="utf-8")
    (root / "infra" / "db.py").unlink()
    assert sorted(verify_manifest(root)) == [
        "changed: contracts/money.py",
        "missing: infra/db.py",
        "not in manifest: infra/new.py",
    ]
    (root / "contracts" / MANIFEST_NAME).unlink()
    assert "missing contracts/MANIFEST.sha256" in verify_manifest(root)[0]


def test_environment_json(tmp_path: Path) -> None:
    """T-CON-04 proves CON-24: ENVIRONMENT.json fields."""
    root = make_repo(tmp_path)
    freeze_repo(root)
    env = environment(root, device="pc-a", frozen_at="2026-10-18T00:00:00Z")
    assert set(env) == {
        "contracts_version",
        "python",
        "requirements_lock_sha256",
        "manifest_sha256",
        "frozen_at",
        "frozen_by",
    }
    assert env["frozen_by"] == "pc-a" and env["python"].count(".") == 1
    json.dumps(env)


def test_doctor_detects_python_lock_and_version_drift() -> None:
    """T-CON-05 proves CON-25: wrong Python minor, lock drift and contract version mismatch fail."""
    assert check_python("3.12", (3, 12)).ok
    assert not check_python("3.12", (3, 11)).ok
    assert not check_python(None, (3, 12)).ok
    lock = parse_lock(
        'A_B==1.0\nuvloop==0.1 ; sys_platform == "nonexistent-os"\n# comment\n\nc==2\n'
    )
    assert lock == {"a-b": "1.0", "c": "2"}
    assert check_lock(lock, {"a-b": "1.0", "c": "2"}).ok
    drift = check_lock(lock, {"a-b": "1.1", "d": "3"})
    assert (
        not drift.ok
        and "a-b 1.1!=1.0" in drift.detail
        and "missing: c" in drift.detail
        and "d" in drift.detail
    )
    assert check_contracts_version({"contracts_version": CONTRACTS_VERSION}).ok
    assert not check_contracts_version({"contracts_version": "0.9.0"}).ok
    assert not check_contracts_version(None).ok
