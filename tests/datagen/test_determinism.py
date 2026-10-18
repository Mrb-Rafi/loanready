"""T-DGN-01: the same seed gives identical output (DGN determinism, PRD-NF-02)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def test_two_builds_same_bundle_hash(generated: Any) -> None:
    """T-DGN-01 proves PRD/10 §1 determinism: two runs give the same bundle hash."""
    from datagen.generate import build, bundle_hash

    again = build(generated.params)
    assert bundle_hash(again) == generated.bundle_sha256


def test_written_files_byte_identical(generated: Any, tmp_path: Path) -> None:
    """T-DGN-01: writing twice gives byte-identical bundle, events, expected and receipts."""
    from datagen.generate import Generated, build, write_outputs

    rebuilt = Generated(build(generated.params), generated.params, generated.tuning)
    first = write_outputs(generated, tmp_path / "a" / "var", tmp_path / "a" / "data")
    second = write_outputs(rebuilt, tmp_path / "b" / "var", tmp_path / "b" / "data")
    for key in ("bundle", "events", "expected"):
        assert first[key].read_bytes() == second[key].read_bytes(), key
    names = sorted(p.name for p in first["receipts"].iterdir())
    assert names == sorted(p.name for p in second["receipts"].iterdir())
    for name in names:
        a = (first["receipts"] / name).read_bytes()
        assert a == (second["receipts"] / name).read_bytes(), name


def test_json_is_utf8_lf_no_bom(written: dict[str, Path]) -> None:
    """T-DGN-01 / ARC-10: UTF-8 without BOM, LF line endings, trailing newline."""
    for key in ("bundle", "events", "expected"):
        raw = written[key].read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), key
        assert b"\r\n" not in raw, key
        assert raw.endswith(b"\n"), key


def test_different_seed_differs(generated: Any) -> None:
    """T-DGN-01: the seed matters (no hidden constant output)."""
    from dataclasses import replace

    from datagen.generate import build, bundle_hash

    other = build(replace(generated.params, seed=generated.params.seed + 1))
    assert bundle_hash(other) != generated.bundle_sha256
