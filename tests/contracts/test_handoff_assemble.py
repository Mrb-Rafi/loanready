"""T-CON-06 / T-CON-07 / CTL-CHG-03: hand-off packaging and assembly, in temp dirs."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from infra.assemble import assemble
from infra.freeze import ENVIRONMENT_NAME, MANIFEST_NAME, build_manifest, environment
from infra.handoff import handoff, owned_files, parse_pytest_summary, starter_files


def make_repo(root: Path) -> Path:
    files = {
        "contracts/__init__.py": 'CONTRACTS_VERSION = "1.0.0"\n',
        "requirements.lock": "a==1\n",
        "pyproject.toml": "\n",
        "governance/__init__.py": 'REQUIRES_CONTRACTS = "1"\n',
        "governance/gate.py": "GATE = 1\n",
        "governance/stale.py": "OLD = 1\n",
        "governance/__pycache__/gate.cpython-312.pyc": "junk",
        "tests/governance/test_gate.py": "def test_x():\n    assert True\n",
        "data/events.json": "{}\n",
        "data/rules.json": "{}\n",
        "data/products.json": "{}\n",
        "books/service.py": "B = 1\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "contracts" / MANIFEST_NAME).write_text(
        build_manifest(root), encoding="utf-8", newline="\n"
    )
    env = environment(root, device="pc-a", frozen_at="2026-10-18T00:00:00Z")
    (root / "contracts" / ENVIRONMENT_NAME).write_text(json.dumps(env), encoding="utf-8")
    return root


def test_owned_files_respect_most_specific_owner(tmp_path: Path) -> None:
    """T-CON-06 proves CON-26: only the WP's owned paths, no caches; data/ split by owner."""
    root = make_repo(tmp_path)
    assert owned_files("WP1", root) == [
        "governance/__init__.py",
        "governance/gate.py",
        "governance/stale.py",
        "tests/governance/test_gate.py",
    ]
    assert owned_files("WP8", root) == ["data/events.json"]
    assert owned_files("WP3", root) == ["books/service.py", "data/rules.json"]


def test_handoff_zip_contents(tmp_path: Path) -> None:
    """T-CON-06 proves CON-26: zip holds owned files plus handoff.json with hashes and counts."""
    root = make_repo(tmp_path / "dev")
    [z] = handoff(
        ["WP1"], "laptop-b", root=root, out_dir=tmp_path / "out", skip_doctor=True, skip_tests=True
    )
    assert z.name.startswith("WP1_laptop-b_") and z.suffix == ".zip"
    with zipfile.ZipFile(z) as zf:
        meta = json.loads(zf.read("handoff.json"))
        names = set(zf.namelist())
    assert meta["wp"] == "WP1" and meta["device"] == "laptop-b"
    assert (
        {f["path"] for f in meta["files"]}
        == set(owned_files("WP1", root))
        == names - {"handoff.json"}
    )
    assert set(meta) >= {
        "contracts_version",
        "manifest_sha256",
        "requirements_lock_sha256",
        "python",
        "tests",
        "created_at",
        "git_commit",
        "files",
    }
    assert parse_pytest_summary("....\n41 passed, 2 skipped in 1.2s\n") == {
        "passed": 41,
        "failed": 0,
        "skipped": 2,
    }
    assert parse_pytest_summary("1 failed, 3 passed, 1 error in 2s") == {
        "passed": 3,
        "failed": 2,
        "skipped": 0,
    }
    with pytest.raises(SystemExit):
        handoff(
            ["WP0"], "x", root=root, out_dir=tmp_path / "out", skip_doctor=True, skip_tests=True
        )


def _handoff(root: Path, out: Path, wp: str = "WP1") -> Path:
    return handoff([wp], "laptop-b", root=root, out_dir=out, skip_doctor=True, skip_tests=True)[0]


def test_valid_handoff_assembles_with_whole_folder_replacement(tmp_path: Path) -> None:
    """T-CON-07 proves CON-27: accepted zip replaces whole ownership, removes stale files,
    writes backups and the assembly log."""
    dev = make_repo(tmp_path / "dev")
    final = make_repo(tmp_path / "final")
    (dev / "governance" / "stale.py").unlink()
    (dev / "governance" / "gate.py").write_text("GATE = 2\n", encoding="utf-8")
    (dev / "governance" / "caps.py").write_text("CAPS = 1\n", encoding="utf-8")
    z = _handoff(dev, tmp_path / "out")
    candidates, applied = assemble([z], root=final, handoff_dir=tmp_path / "handoff")
    assert [c.accepted for c in candidates] == [True] and applied[0]["wp"] == "WP1"
    assert (final / "governance" / "gate.py").read_text(encoding="utf-8") == "GATE = 2\n"
    assert (final / "governance" / "caps.py").exists()
    assert not (final / "governance" / "stale.py").exists()
    backups = list((tmp_path / "handoff" / "backup").rglob("stale.py"))
    assert backups and backups[0].read_text(encoding="utf-8") == "OLD = 1\n"
    log = json.loads((tmp_path / "handoff" / "assembly_log.json").read_text(encoding="utf-8"))
    assert log[-1]["applied"][0]["wp"] == "WP1"
    assert (final / "books" / "service.py").exists()  # other WPs untouched


def test_wp8_handoff_keeps_other_owners_data_files(tmp_path: Path) -> None:
    """T-CON-07 proves CON-27: WP8 owns data/ except rules.json (WP3) and products.json (WP5)."""
    dev = make_repo(tmp_path / "dev")
    final = make_repo(tmp_path / "final")
    (dev / "data" / "expected.json").write_text("{}\n", encoding="utf-8")
    z = _handoff(dev, tmp_path / "out", "WP8")
    assemble([z], root=final, handoff_dir=tmp_path / "handoff")
    assert (final / "data" / "expected.json").exists()
    assert (final / "data" / "rules.json").exists() and (final / "data" / "products.json").exists()


def _rewrite(
    z: Path,
    out: Path,
    *,
    meta_update: dict | None = None,
    extra: dict[str, str] | None = None,
    tamper: str | None = None,
) -> Path:
    with zipfile.ZipFile(z) as src:
        meta = json.loads(src.read("handoff.json"))
        contents = {n: src.read(n) for n in src.namelist() if n != "handoff.json"}
    meta.update(meta_update or {})
    for rel, text in (extra or {}).items():
        contents[rel] = text.encode()
        meta["files"].append(
            {"path": rel, "sha256": __import__("hashlib").sha256(text.encode()).hexdigest()}
        )
    if tamper:
        contents[tamper] = b"tampered\n"
    with zipfile.ZipFile(out, "w") as dst:
        for n, data in contents.items():
            dst.writestr(n, data)
        dst.writestr("handoff.json", json.dumps(meta))
    return out


@pytest.mark.parametrize(
    "kind,needle",
    [
        ("foreign", "path outside WP1"),
        ("frozen", "frozen WP0 file"),
        ("hash", "file hash differs"),
        ("manifest", "manifest mismatch"),
        ("lock", "lock mismatch"),
    ],
)
def test_refusals(tmp_path: Path, kind: str, needle: str) -> None:
    """T-CON-07 proves CON-27: foreign paths, frozen files, wrong hashes, other contracts or
    lock are refused and nothing is written."""
    dev = make_repo(tmp_path / "dev")
    final = make_repo(tmp_path / "final")
    z = _handoff(dev, tmp_path / "out")
    bad = tmp_path / f"bad_{kind}.zip"
    if kind == "foreign":
        _rewrite(z, bad, extra={"books/evil.py": "x\n"})
    elif kind == "frozen":
        _rewrite(z, bad, extra={"contracts/money.py": "x\n"})
    elif kind == "hash":
        _rewrite(z, bad, tamper="governance/gate.py")
    elif kind == "manifest":
        _rewrite(z, bad, meta_update={"manifest_sha256": "f" * 64})
    else:
        _rewrite(z, bad, meta_update={"requirements_lock_sha256": "e" * 64})
    before = (final / "governance" / "stale.py").exists()
    candidates, applied = assemble([bad], root=final, handoff_dir=tmp_path / "handoff")
    assert not candidates[0].accepted and applied == []
    assert any(needle in r for r in candidates[0].reasons), candidates[0].reasons
    assert (final / "governance" / "stale.py").exists() == before
    assert not (tmp_path / "handoff" / "assembly_log.json").exists()


def test_newest_handoff_wins_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    """T-CON-07 proves CON-27: duplicate WPs -> newest created_at wins; dry run changes nothing."""
    dev = make_repo(tmp_path / "dev")
    final = make_repo(tmp_path / "final")
    z = _handoff(dev, tmp_path / "out")
    old = _rewrite(z, tmp_path / "old.zip", meta_update={"created_at": "2026-10-18T01:00:00Z"})
    new = _rewrite(z, tmp_path / "new.zip", meta_update={"created_at": "2026-10-18T02:00:00Z"})
    candidates, applied = assemble(
        [old, new], dry_run=True, root=final, handoff_dir=tmp_path / "handoff"
    )
    assert applied == [] and not (tmp_path / "handoff").exists()
    by_name = {c.zip_path.name: c for c in candidates}
    assert by_name["old.zip"].superseded_by == new and by_name["new.zip"].superseded_by is None


def test_starter_excludes_runtime_dirs(tmp_path: Path) -> None:
    """T-CON-06 proves CON-26: the starter copy excludes .venv, var, handoff, .env, caches."""
    root = make_repo(tmp_path)
    for rel in (".venv/x", "var/app.db", "handoff/out/a.zip", ".env", ".git/HEAD"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    files = starter_files(root)
    assert "governance/gate.py" in files and "contracts/__init__.py" in files
    assert not [f for f in files if f.split("/")[0] in {".venv", "var", "handoff", ".env", ".git"}]
    assert not [f for f in files if "__pycache__" in f]
