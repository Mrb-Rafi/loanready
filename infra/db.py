"""SQLite engines (ARC-14): one engine per DB file, WAL on, ``check_same_thread=False``."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlmodel import Session

from contracts.schema import APP_METADATA
from infra.paths import var_dir

_engines: dict[str, Engine] = {}


def db_path(name: str = "app") -> Path:
    """``var/<name>.db`` (app, llm_cache, sbx_bank, ...)."""
    return var_dir() / f"{name}.db"


def _on_connect(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def engine_for(path: Path) -> Engine:
    """Engine for an explicit SQLite file (cached per resolved path)."""
    key = str(Path(path).resolve())
    eng = _engines.get(key)
    if eng is None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(f"sqlite:///{key}", connect_args={"check_same_thread": False})
        event.listen(eng, "connect", _on_connect)
        _engines[key] = eng
    return eng


def engine(name: str = "app") -> Engine:
    return engine_for(db_path(name))


@contextmanager
def session(name: str = "app", eng: Engine | None = None) -> Iterator[Session]:
    """Unit of work: commits on success, rolls back on error."""
    with Session(eng or engine(name)) as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise


def create_all(eng: Engine | None = None) -> Engine:
    """Create every ``contracts.schema`` table on var/app.db (or the given engine)."""
    eng = eng or engine("app")
    APP_METADATA.create_all(eng)
    return eng


def dispose_all() -> None:
    """Close every pooled connection (before deleting var/ or switching var_dir)."""
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()
