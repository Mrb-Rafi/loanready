"""T-CON-01: canonical JSON and the cross-device golden hash (PRD/15 §3.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from contracts.actions import OWNER
from contracts.canonical import (
    ZERO_HASH,
    bytes_sha256,
    canonical_json,
    file_sha256,
    params_hash,
    sha256_hex,
)
from contracts.enums import Scenario, Tier

GOLDEN_OBJECT = {"b": Decimal("1.50"), "a": date(2026, 10, 14), "c": [3, "x"], "d": None}
GOLDEN_JSON = '{"a":"2026-10-14","b":"1.50","c":[3,"x"],"d":null}'
# Computed once on the WP0 device; every PC must reproduce it.
GOLDEN_SHA256 = "dadc0cdf05e986ecbaadbe293cc9c5d729571d0afdff392ffaa4b4aaeafe6194"


def test_golden_hash() -> None:
    """T-CON-01 proves CON-04: byte-identical canonical JSON on every device and OS."""
    assert canonical_json(GOLDEN_OBJECT) == GOLDEN_JSON
    assert sha256_hex(GOLDEN_JSON) == GOLDEN_SHA256
    assert params_hash(GOLDEN_OBJECT) == GOLDEN_SHA256


def test_normalisation_rules() -> None:
    """T-CON-01 proves CON-04: key order, Decimal without exponent, UTC datetime with Z,
    enums by value, sets sorted, pydantic models dumped."""
    assert canonical_json({"z": 1, "a": 2}) == '{"a":2,"z":1}'
    assert canonical_json(Decimal("1E+3")) == '"1000"'
    assert canonical_json(Decimal("0.000001")) == '"0.000001"'
    dhaka = timezone(timedelta(hours=6))
    assert (
        canonical_json(datetime(2026, 10, 14, 9, 0, tzinfo=dhaka))
        == '"2026-10-14T03:00:00.000000Z"'
    )
    assert canonical_json([Scenario.BASE, Tier.T2_APPROVE]) == '["BASE",2]'
    assert canonical_json({3, 1, 2}) == "[1,2,3]"
    assert canonical_json((1, "é")) == '[1,"é"]'
    assert '"role":"OWNER"' in canonical_json(OWNER)
    assert canonical_json({"flag": True}) == '{"flag":true}'


@pytest.mark.parametrize("bad", [1.5, b"x", datetime(2026, 1, 1), Decimal("NaN"), object()])  # noqa: DTZ001
def test_rejected_types(bad: object) -> None:
    """T-CON-01 proves CON-04: float, bytes, naive datetimes, NaN and unknown types rejected."""
    with pytest.raises(TypeError):
        canonical_json({"x": bad})


def test_repeatable_and_file_hash(tmp_path: Path) -> None:
    """T-CON-01 proves CON-04: same hash on repeated runs; file and bytes hashes agree."""
    obj = {
        "amount_cents": 90000,
        "value_date": date(2026, 10, 14),
        "ts": datetime(2026, 10, 14, tzinfo=UTC),
    }
    assert len({params_hash(obj) for _ in range(5)}) == 1
    f = tmp_path / "x.bin"
    f.write_bytes(b"%PDF-1.4 test")
    assert file_sha256(f) == bytes_sha256(b"%PDF-1.4 test")
    assert ZERO_HASH == "0" * 64
