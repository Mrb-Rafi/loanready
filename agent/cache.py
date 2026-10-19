"""Disk cache for model answers (PRD/09 §6, AGT-12, AGT-21).

``key = sha256(backend | model | task | canonical_json(messages) | canonical_json(schema))``,
so an answer from one backend or model is never served as another's. Stored in
``var/llm_cache.db`` (agent-owned SQLite file, not the app database). Only real backends are
cached; the mock is deterministic and needs no cache. A hit costs nothing and is not counted
against the remote budget.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from contracts.canonical import canonical_json, sha256_hex

DB_NAME = "llm_cache.db"


def default_db_path() -> Path:
    from infra.paths import var_dir

    return var_dir() / DB_NAME


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def cache_key(
    backend: str, model: str, task: str, messages: list[dict[str, str]], schema: dict[str, Any]
) -> str:
    return sha256_hex(
        "|".join([backend, model, task, canonical_json(messages), canonical_json(schema)])
    )


class LLMCache:
    """``get``/``put`` validated outputs by key. ``path=None`` keeps it in memory (tests)."""

    def __init__(self, path: Path | None = None) -> None:
        self._mem: dict[str, dict[str, Any]] | None = {} if path is None else None
        self.path = path
        if path is not None:
            with closing(connect(path)) as conn, conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS llm_cache "
                    "(key TEXT PRIMARY KEY, backend TEXT, model TEXT, task TEXT, output TEXT)"
                )

    def get(self, key: str) -> dict[str, Any] | None:
        if self._mem is not None:
            return self._mem.get(key)
        assert self.path is not None
        with closing(connect(self.path)) as conn:
            row = conn.execute("SELECT output FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def put(self, key: str, backend: str, model: str, task: str, output: dict[str, Any]) -> None:
        if self._mem is not None:
            self._mem[key] = dict(output)
            return
        assert self.path is not None
        with closing(connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?, ?)",
                # plain JSON: model outputs carry a float confidence (never money), which
                # canonical_json rejects by design
                (key, backend, model, task, json.dumps(output, sort_keys=True)),
            )
