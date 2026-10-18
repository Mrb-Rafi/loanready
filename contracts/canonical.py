"""Canonical JSON and hashing (CON-04). The only way any package computes a hash.

Approval binding, ticket binding, log chaining and loan-pack hashes depend on this being
byte-identical on every device and OS.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ZERO_HASH = "0" * 64


def _normalize(obj: Any) -> Any:
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, Enum):
        return _normalize(obj.value)
    if isinstance(obj, str):
        return str(obj)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        raise TypeError("float is not allowed in canonical JSON; use Decimal")
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            raise TypeError("non-finite Decimal is not allowed")
        return format(obj, "f")
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise TypeError("naive datetime is not allowed; use UTC-aware datetimes")
        return obj.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, BaseModel):
        return _normalize(obj.model_dump(mode="python"))
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            nkey = _normalize(key)
            if not isinstance(nkey, str):
                nkey = json.dumps(nkey)
            out[nkey] = _normalize(value)
        return out
    if isinstance(obj, (set, frozenset)):
        items = [_normalize(v) for v in obj]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        raise TypeError("bytes are not allowed in canonical JSON")
    if hasattr(obj, "__dataclass_fields__"):
        return _normalize({f: getattr(obj, f) for f in obj.__dataclass_fields__})
    raise TypeError(f"type not allowed in canonical JSON: {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    """``json.dumps(normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)``.

    normalize: Decimal -> plain string (no exponent); date -> YYYY-MM-DD; datetime -> UTC
    ``YYYY-MM-DDTHH:MM:SS.ffffffZ``; Enum -> value; pydantic model -> model_dump;
    tuple/set -> list (sets sorted); float, bytes and naive datetimes are rejected.
    """
    return json.dumps(_normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    """SHA-256 hex digest of the UTF-8 bytes of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def params_hash(params: Mapping[str, Any] | BaseModel) -> str:
    """``sha256_hex(canonical_json(params))``."""
    return sha256_hex(canonical_json(params))


def file_sha256(path: Path) -> str:
    """Streamed SHA-256 of a file's raw bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(data: bytes) -> str:
    """SHA-256 of raw bytes (PDF payloads, decoded uploads)."""
    return hashlib.sha256(data).hexdigest()
