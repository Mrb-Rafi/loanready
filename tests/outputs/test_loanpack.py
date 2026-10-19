"""T-OUT-06 proves OUT-04..OUT-08, PRD-20: all section keys in the outline, footer content
hash, deterministic bytes, masked accounts, verify_pack detects modification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts.canonical import file_sha256, params_hash
from contracts.dto_outputs import LENDER_REQUIRED_SECTIONS, LOAN_PACK_SECTIONS
from outputs.loanpack.builder import build_pdf
from outputs.loanpack.sections import mask


def test_sections_in_outline(content: Any, tmp_path: Path) -> None:
    """T-OUT-06: one outline entry per section key; sidecar lists them; lender's required
    sections all present."""
    built = build_pdf(content, "pck_t", tmp_path)
    raw = built.path.read_bytes()
    assert raw.startswith(b"%PDF-")
    for key in LOAN_PACK_SECTIONS:
        assert f"/Title ({key})".encode() in raw, key
    assert built.sections == list(LOAN_PACK_SECTIONS)
    assert set(LENDER_REQUIRED_SECTIONS) <= set(built.sections)
    side = json.loads(built.path.with_name(built.path.stem + ".sections.json").read_text("utf-8"))
    assert side["sections"] == list(LOAN_PACK_SECTIONS) and side["pack_id"] == "pck_t"


def test_footer_content_hash(content: Any, tmp_path: Path) -> None:
    """T-OUT-06 / OUT-04: footer carries pack id, page n/N and content_hash[:16]."""
    built = build_pdf(content, "pck_t", tmp_path, compress=False)
    raw = built.path.read_bytes()
    assert built.content_hash == params_hash(content)
    assert f"content hash {built.content_hash[:16]}".encode() in raw
    assert b"LoanReady pack pck_t" in raw and b"page 1/" in raw
    assert built.metadata["content_hash"] == built.content_hash


def test_deterministic_bytes(content: Any, tmp_path: Path) -> None:
    """T-OUT-06 / OUT-05: same content and pack id -> byte-identical PDF."""
    a = build_pdf(content, "pck_t", tmp_path / "a")
    b = build_pdf(content, "pck_t", tmp_path / "b")
    assert a.path.read_bytes() == b.path.read_bytes()
    assert a.sha256 == b.sha256 == file_sha256(a.path)
    assert b"D:20261014000000" in a.path.read_bytes()


def test_accounts_masked(content: Any, tmp_path: Path) -> None:
    """T-OUT-06 / OUT-08: account numbers never appear in full."""
    assert mask("pay SBX-GM-00412277 or 99887766554433") == "pay SBX-GM-****2277 or ****4433"
    purpose = "Stock; supplier account SBX-GM-00412277"
    built = build_pdf(
        content.model_copy(update={"purpose": purpose}), "pck_m", tmp_path, compress=False
    )
    raw = built.path.read_bytes()
    assert b"00412277" not in raw and b"****2277" in raw


def test_service_build_verify_submit(outputs: Any, content: Any) -> None:
    """T-OUT-06 / OUT-06, OUT-07: stored sha256 verifies; a changed byte fails; a submission
    is recorded against the pack hash."""
    pack = outputs.build_loan_pack(content)
    assert Path(pack.path).is_file() and Path(pack.path).is_absolute()
    assert pack.sha256 == file_sha256(Path(pack.path))
    assert outputs.loan_pack(pack.pack_id) == pack
    assert outputs.verify_pack(pack.pack_id)
    outputs.record_submission(pack.pack_id, "lender_demo", "rcv_123")
    assert outputs.submissions(pack.pack_id) == [("lender_demo", "rcv_123")]
    data = bytearray(Path(pack.path).read_bytes())
    data[-10] ^= 0x01
    Path(pack.path).write_bytes(bytes(data))
    assert not outputs.verify_pack(pack.pack_id)
    assert not outputs.verify_pack("pck_missing")


def test_default_packs_dir_is_var(app_db: Any, clock: Any, ids: Any, settings: Any) -> None:
    """T-OUT-06: without packs_dir, packs go to infra.paths.var_dir() / "packs"."""
    from infra.paths import var_dir
    from outputs.service import Outputs

    assert Outputs(app_db, clock, ids, settings).packs_dir == var_dir() / "packs"
