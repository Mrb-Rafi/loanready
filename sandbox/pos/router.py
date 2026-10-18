"""POS sales feed (PRD/04 §9.1). Notes are untrusted free text, returned as stored."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from contracts.sandbox_api import HEADER_NEXT_CURSOR, ErrorOut, PosItem, PosSaleOut
from sandbox import db
from sandbox.auth import require
from sandbox.bank.service import decode_cursor, encode_cursor
from sandbox.clock import parse_ts

router = APIRouter(prefix="/pos", tags=["pos"], responses={"4XX": {"model": ErrorOut}})


def sale_out(row: dict[str, Any], items: list[dict[str, Any]]) -> PosSaleOut:
    return PosSaleOut(
        id=row["id"],
        ts=parse_ts(row["ts"]),
        amount_cents=row["amount_cents"],
        tender=row["tender"],
        items=[PosItem(name=i["name"], qty=i["qty"], unit_cents=i["unit_cents"]) for i in items],
        note=row["note"],
    )


def insert_sale(conn: Any, sale: PosSaleOut, ts: str) -> None:
    db.run(
        conn,
        "INSERT INTO sales (id, ts, date, amount_cents, tender, note) VALUES (:i, :ts, :d, :a, "
        ":t, :n)",
        i=sale.id,
        ts=ts,
        d=sale.ts.date().isoformat(),
        a=sale.amount_cents,
        t=sale.tender,
        n=sale.note,
    )
    for pos, item in enumerate(sale.items):
        db.run(
            conn,
            "INSERT INTO sale_items (sale_id, pos, name, qty, unit_cents) VALUES (:s, :p, :n, :q, "
            ":u)",
            s=sale.id,
            p=pos,
            n=item.name,
            q=item.qty,
            u=item.unit_cents,
        )


@router.get("/sales", response_model=list[PosSaleOut])
def list_sales(
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    limit: int = Query(500, ge=1, le=1000),
    cursor: str | None = Query(None),
    _scope: str = Depends(require("read")),
) -> JSONResponse:
    after_ts, after_id = "", ""
    if cursor:
        after_ts, after_id = decode_cursor(cursor, 2)
    with db.tx("pos") as c:
        rows = db.rows(
            c,
            "SELECT * FROM sales WHERE date >= :s AND date <= :e AND (ts > :at OR (ts = :at AND "
            "id > :ai)) "
            "ORDER BY ts, id LIMIT :lim",
            s=start.isoformat(),
            e=end.isoformat(),
            at=after_ts,
            ai=after_id,
            lim=limit + 1,
        )
        more = len(rows) > limit
        rows = rows[:limit]
        out = [
            sale_out(
                r, db.rows(c, "SELECT * FROM sale_items WHERE sale_id = :s ORDER BY pos", s=r["id"])
            )
            for r in rows
        ]
    headers = (
        {HEADER_NEXT_CURSOR: encode_cursor(rows[-1]["ts"], rows[-1]["id"])} if more and rows else {}
    )
    return JSONResponse([s.model_dump(mode="json") for s in out], headers=headers)
