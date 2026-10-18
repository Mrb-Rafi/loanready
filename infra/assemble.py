"""Assembly on the final PC (CON-27).

``python -m infra.assemble handoff/incoming/*.zip [--dry-run] [--no-integrate]``:
1. read each zip's handoff.json; for duplicate WPs the newest ``created_at`` wins;
2. refuse a zip built against other contracts or dependencies, with files outside its
   WP, with a file hash that differs from handoff.json, or touching frozen WP0 files;
3. for each accepted WP move every currently owned file to ``handoff/backup/<time>/``
   and write the zip's files (whole-ownership replacement: stale files disappear);
4. append to ``handoff/assembly_log.json``; 5. run the integration check.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from contracts.clock import wall_clock_now
from contracts.registry import OWNERSHIP, owner_of
from infra.freeze import load_environment
from infra.handoff import owned_files
from infra.paths import HANDOFF_DIR, REPO_ROOT


@dataclass
class Candidate:
    zip_path: Path
    meta: dict
    accepted: bool = True
    reasons: list[str] = field(default_factory=list)
    superseded_by: Path | None = None


def _safe(rel: str) -> bool:
    p = PurePosixPath(rel)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in rel


def inspect_zip(path: Path, env: dict) -> Candidate:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return Candidate(path, {}, False, [f"not a readable zip: {exc}"])
    with zf:
        try:
            meta = json.loads(zf.read("handoff.json").decode("utf-8"))
        except (KeyError, ValueError) as exc:
            return Candidate(path, {}, False, [f"handoff.json missing or invalid: {exc}"])
        c = Candidate(path, meta)
        wp = meta.get("wp")
        if wp not in OWNERSHIP or wp == "WP0":
            c.reasons.append(f"work package {wp!r} cannot be assembled")
        if meta.get("manifest_sha256") != env.get("manifest_sha256"):
            c.reasons.append(
                f"manifest mismatch: zip {str(meta.get('manifest_sha256'))[:12]}… vs local "
                f"{str(env.get('manifest_sha256'))[:12]}…; re-sync contracts/ on that PC "
                "and rebuild the hand-off"
            )
        if meta.get("requirements_lock_sha256") != env.get("requirements_lock_sha256"):
            c.reasons.append(
                f"lock mismatch: zip {str(meta.get('requirements_lock_sha256'))[:12]}… vs local "
                f"{str(env.get('requirements_lock_sha256'))[:12]}…; on that PC delete .venv, "
                "run setup, rebuild"
            )
        listed = {f["path"]: f["sha256"] for f in meta.get("files", [])}
        names = {n for n in zf.namelist() if not n.endswith("/") and n != "handoff.json"}
        if names != set(listed):
            c.reasons.append("zip contents differ from handoff.json file list")
        for rel in sorted(names):
            if not _safe(rel):
                c.reasons.append(f"unsafe path: {rel}")
                continue
            owner = owner_of(rel)
            if owner == "WP0":
                c.reasons.append(f"frozen WP0 file: {rel}")
            elif owner != wp:
                c.reasons.append(f"path outside {wp}: {rel} (owner {owner})")
            elif rel in listed and hashlib.sha256(zf.read(rel)).hexdigest() != listed[rel]:
                c.reasons.append(f"file hash differs: {rel}")
        c.accepted = not c.reasons
        return c


def choose(candidates: list[Candidate]) -> list[Candidate]:
    """Newest created_at per WP wins; older ones are marked superseded."""
    best: dict[str, Candidate] = {}
    for c in candidates:
        if not c.accepted:
            continue
        wp = c.meta["wp"]
        cur = best.get(wp)
        if cur is None or c.meta.get("created_at", "") > cur.meta.get("created_at", ""):
            if cur is not None:
                cur.superseded_by = c.zip_path
            best[wp] = c
        else:
            c.superseded_by = cur.zip_path
    return [c for c in candidates if c.accepted and c.superseded_by is None]


def apply(c: Candidate, root: Path, backup_root: Path) -> dict:
    wp = c.meta["wp"]
    moved = []
    for rel in owned_files(wp, root):
        dst = backup_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root / rel), str(dst))
        moved.append(rel)
    with zipfile.ZipFile(c.zip_path) as zf:
        for entry in c.meta["files"]:
            target = root / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(entry["path"]))
    return {
        "wp": wp,
        "zip": c.zip_path.name,
        "device": c.meta.get("device"),
        "created_at": c.meta.get("created_at"),
        "files": len(c.meta["files"]),
        "backed_up": len(moved),
        "tests": c.meta.get("tests"),
        "manifest_sha256": c.meta.get("manifest_sha256"),
    }


def assemble(
    zips: list[Path], dry_run: bool = False, root: Path = REPO_ROOT, handoff_dir: Path | None = None
) -> tuple[list[Candidate], list[dict]]:
    env = load_environment(root)
    if env is None:
        raise SystemExit("contracts/ENVIRONMENT.json missing on this PC: run freeze (WP0 device)")
    candidates = [inspect_zip(Path(z), env) for z in zips]
    chosen = choose(candidates)
    for c in candidates:
        label = c.meta.get("wp", "?")
        if not c.accepted:
            print(f"refused  {c.zip_path.name} ({label}):")
            for r in c.reasons:
                print(f"           - {r}")
        elif c.superseded_by is not None:
            print(f"superseded {c.zip_path.name} ({label}) by {c.superseded_by.name}")
        else:
            t = c.meta.get("tests", {})
            print(
                f"accepted {c.zip_path.name} ({label}; {len(c.meta['files'])} files; tests "
                f"{t.get('passed', 0)} passed, {t.get('failed', 0)} failed)"
            )
    if dry_run or not chosen:
        return candidates, []
    hdir = handoff_dir or HANDOFF_DIR
    stamp = wall_clock_now().strftime("%Y%m%d-%H%M%S")
    backup_root = hdir / "backup" / stamp
    applied = [apply(c, root, backup_root) for c in chosen]
    log_path = hdir / "assembly_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    log.append(
        {
            "assembled_at": stamp,
            "backup": backup_root.relative_to(hdir).as_posix(),
            "applied": applied,
        }
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"assembled {', '.join(a['wp'] for a in applied)}; backups in {backup_root}")
    return candidates, applied


def expand(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in patterns:
        matches = glob.glob(p)
        out.extend(Path(m) for m in sorted(matches)) if matches else out.append(Path(p))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assemble hand-off zips on the final PC")
    ap.add_argument("zips", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-integrate", action="store_true")
    args = ap.parse_args(argv)
    candidates, applied = assemble(expand(args.zips), args.dry_run)
    rc = 1 if any(not c.accepted for c in candidates) else 0
    if applied and not args.no_integrate:
        from infra.integration_check import main as integrate

        rc = integrate([]) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
