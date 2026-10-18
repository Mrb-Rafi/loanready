"""Hash-chained, append-only audit log (PRD/03 §5; CTL-LOG-01..04).

``row_hash = sha256_hex(prev_hash + canonical_json(record without row_hash))``; row 1's
``prev_hash`` is ``ZERO_HASH``. Appends are serialised with a process lock plus a SQLite
``BEGIN IMMEDIATE`` transaction, so two writers cannot fork the chain (GOV-13). This module
has no update or delete path (GOV-12): there is no function for it and no such SQL.
"""

from __future__ import annotations

import csv
import io
import json
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from contracts.actions import CSV_COLUMNS, SYSTEM, AuditRecord, Principal, mask_params
from contracts.canonical import ZERO_HASH, canonical_json, sha256_hex
from contracts.clock import Clock
from contracts.enums import Decision, EventType, Origin, ReasonCode

_APPEND_LOCK = threading.Lock()
_TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_COLUMNS = (
    "seq, ts, action_id, action_type, tier, origin, principal_id, decision, reason_code, "
    "approved_by, approved_at, params_masked_json, result_masked_json, sbx_ref, "
    "control_ids_json, prev_hash, row_hash, created_at"
)


def _ts(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).strftime(_TS_FMT)


def _parse_ts(s: str | None) -> datetime | None:
    return None if s is None else datetime.strptime(s, _TS_FMT).replace(tzinfo=UTC)


def _normalise(d: dict[str, Any]) -> dict[str, Any]:
    """Masked dict as plain JSON types, exactly as it will be read back."""
    return json.loads(canonical_json(mask_params(d)))


def row_hash_of(record: dict[str, Any]) -> str:
    """Hash of one record (all AuditRecord fields except row_hash)."""
    return sha256_hex(record["prev_hash"] + canonical_json(record))


class AuditLog:
    """AuditLogService (WP1). Public API: append_decision, log_event, rows, verify_chain,
    export_csv, counts, last_hash."""

    def __init__(self, engine: Engine, clock: Clock):
        self._engine = engine
        self._clock = clock

    # writing (append only) --------------------------------------------------------------
    def append_decision(
        self,
        *,
        action_type: str,
        decision: Decision,
        reason_code: ReasonCode,
        principal_id: str,
        action_id: str | None = None,
        tier: int | None = None,
        origin: Origin | None = None,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        params: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        sbx_ref: str | None = None,
        control_ids: Sequence[str] = (),
    ) -> int:
        """Append one row; returns its seq. Params and results are masked before hashing."""
        params_masked = _normalise(params or {})
        result_masked = _normalise(result or {})
        controls = sorted(set(control_ids))
        with _APPEND_LOCK:
            raw = self._engine.raw_connection()
            try:
                cur = raw.cursor()
                cur.execute("BEGIN IMMEDIATE")
                last = cur.execute(
                    "SELECT seq, row_hash FROM action_log ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq, prev = (last[0] + 1, last[1]) if last else (1, ZERO_HASH)
                ts = self._clock.now()
                record = {
                    "seq": seq,
                    "ts": ts,
                    "action_id": action_id,
                    "action_type": action_type,
                    "tier": tier,
                    "origin": origin,
                    "principal_id": principal_id,
                    "decision": decision,
                    "reason_code": reason_code,
                    "approved_by": approved_by,
                    "approved_at": approved_at,
                    "params_masked": params_masked,
                    "result_masked": result_masked,
                    "sbx_ref": sbx_ref,
                    "control_ids": controls,
                    "prev_hash": prev,
                }
                rh = row_hash_of(record)
                cur.execute(
                    f"INSERT INTO action_log ({_COLUMNS}) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        seq,
                        _ts(ts),
                        action_id,
                        action_type,
                        tier,
                        origin.value if origin is not None else None,
                        principal_id,
                        decision.value,
                        reason_code.value,
                        approved_by,
                        _ts(approved_at),
                        canonical_json(params_masked),
                        canonical_json(result_masked),
                        sbx_ref,
                        canonical_json(controls),
                        prev,
                        rh,
                        _ts(ts),
                    ),
                )
                raw.commit()
            except Exception:
                raw.rollback()
                raise
            finally:
                raw.close()
        return seq

    def log_event(
        self,
        event: EventType,
        details: dict[str, Any],
        principal: Principal,
        control_ids: Sequence[str] = (),
    ) -> int:
        return self.append_decision(
            action_type=event.value,
            decision=Decision.EVENT,
            reason_code=ReasonCode.OK,
            principal_id=principal.id,
            params=details,
            control_ids=control_ids,
        )

    # reading ----------------------------------------------------------------------------
    def _fetch(self, limit: int | None = None) -> list[tuple[Any, ...]]:
        raw = self._engine.raw_connection()
        try:
            sql = f"SELECT {_COLUMNS} FROM action_log ORDER BY seq"
            rows = raw.cursor().execute(sql).fetchall()
        finally:
            raw.close()
        return rows if limit is None else rows[-limit:] if limit > 0 else []

    @staticmethod
    def _to_record(r: tuple[Any, ...]) -> AuditRecord:
        return AuditRecord(
            seq=r[0],
            ts=_parse_ts(r[1]),
            action_id=r[2],
            action_type=r[3],
            tier=r[4],
            origin=Origin(r[5]) if r[5] is not None else None,
            principal_id=r[6],
            decision=Decision(r[7]),
            reason_code=ReasonCode(r[8]),
            approved_by=r[9],
            approved_at=_parse_ts(r[10]),
            params_masked=json.loads(r[11]),
            result_masked=json.loads(r[12]),
            sbx_ref=r[13],
            control_ids=json.loads(r[14]),
            prev_hash=r[15],
            row_hash=r[16],
        )

    def rows(self, limit: int | None = None) -> list[AuditRecord]:
        """Rows in seq order (the last ``limit`` rows if given)."""
        return [self._to_record(r) for r in self._fetch(limit)]

    def last_hash(self) -> str:
        rows = self._fetch(1)
        return rows[0][16] if rows else ZERO_HASH

    def verify_chain(self) -> int | None:
        """Recompute every row; None if intact, else the first broken seq (GOV-14).

        Detects a changed field (hash), a deleted row (seq gap / prev link) and reordering.
        """
        prev = ZERO_HASH
        for expected, raw_row in enumerate(self._fetch(), start=1):
            try:
                rec = self._to_record(raw_row)
            except Exception:
                return expected
            data = rec.model_dump(mode="python", exclude={"row_hash"})
            if rec.seq != expected or rec.prev_hash != prev or row_hash_of(data) != rec.row_hash:
                return expected
            prev = rec.row_hash
        return None

    def counts(self) -> dict[str, int]:
        out = {d.value: 0 for d in Decision}
        for r in self._fetch():
            out[r[7]] = out.get(r[7], 0) + 1
        return out

    def export_csv(self, path: Path) -> Path:
        """CSV_COLUMNS, UTF-8, LF; dict/list columns as canonical JSON (GOV-16). The
        EXPORT_CREATED event is logged after writing, so it is the next row."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for rec in self.rows():
            d = rec.model_dump(mode="python")
            out = []
            for col in CSV_COLUMNS:
                v = d[col]
                if isinstance(v, (dict, list)):
                    out.append(canonical_json(v))
                elif v is None:
                    out.append("")
                elif isinstance(v, datetime):
                    out.append(_ts(v))
                else:
                    out.append(getattr(v, "value", v))
            writer.writerow(out)
        path.write_text(buf.getvalue(), encoding="utf-8", newline="")
        self.log_event(
            EventType.EXPORT_CREATED,
            {"file": path.name, "rows": len(buf.getvalue().splitlines()) - 1},
            SYSTEM,
            ["CTL-LOG-03"],
        )
        return path
