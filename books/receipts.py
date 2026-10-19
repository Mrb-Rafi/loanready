"""Receipt capture (PRD/05 §10, BKS-23..BKS-27, CTL-DQ-01).

OCR: the sidecar ``<image stem>.ocr.json`` (``{"text", "mean_conf"}``). The Tesseract path
is a SHOULD module and is not built: ``OCR_BACKEND=auto`` and ``tesseract`` both read the
sidecar for now. The text is untrusted (``Untrusted(source=RECEIPT)``): it is scanned and
stored, and only the parsed TOTAL line can become an amount (BKS-26).

confidence = total_found (1.0 | 0.3) x date_found (1.0 | 0.7)
             x items (sum equals total 1.0 | no items 0.9 | mismatch 0.6) x mean_conf / 100.
Below ``receipt_confirm_threshold`` -> NEEDS_CONFIRMATION, else AUTO.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from books.repo import RawRow
from books.similarity import jaccard
from contracts.enums import ReceiptStatus, SourceKind
from contracts.money import parse
from contracts.untrusted import ScanFlag, Untrusted, scan_untrusted

TOTAL_RE = re.compile(r"(?:TOTAL|AMOUNT DUE|GRAND TOTAL)\s*[:$]?\s*([0-9][0-9.,]*)", re.I)
ITEM_RE = re.compile(r"^(.+?)\s+([0-9]+\.[0-9]{2})$")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DMY_DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
TEXT_DATE = re.compile(r"\b(\d{1,2}) ([A-Za-z]{3}) (\d{4})\b")
_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, start=1)}
MATCH_DAYS = 3
VENDOR_MATCH = Decimal("0.6")
UNDATED_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class Parsed:
    vendor: str | None
    total_cents: int | None
    date: date | None
    items_cents: list[int]
    confidence: Decimal


def sidecar_path(image: Path) -> Path:
    return image.with_name(image.stem + ".ocr.json")


def read_sidecar(image: Path) -> tuple[str, int]:
    """(text, mean_conf 0..100) from the sidecar next to the image."""
    data = json.loads(sidecar_path(image).read_text(encoding="utf-8"))
    return str(data["text"]), int(data["mean_conf"])


def _amount(text: str) -> int | None:
    try:
        return parse(text.replace(",", "")) if "." in text else parse(text)
    except ValueError:
        return None


def find_date(text: str) -> date | None:
    """First of YYYY-MM-DD, DD/MM/YYYY, DD Mon YYYY that is a real date."""
    candidates: list[tuple[int, int, int]] = []
    for m in ISO_DATE.finditer(text):
        candidates.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in DMY_DATE.finditer(text):
        candidates.append((int(m.group(3)), int(m.group(2)), int(m.group(1))))
    for m in TEXT_DATE.finditer(text):
        month = MONTHS.get(m.group(2).lower())
        if month:
            candidates.append((int(m.group(3)), month, int(m.group(1))))
    for y, mth, d in candidates:
        try:
            return date(y, mth, d)
        except ValueError:
            continue
    return None


def parse_text(text: str, mean_conf: int) -> Parsed:
    """BKS-24 parsing and BKS-25 confidence."""
    lines = [ln.strip() for ln in text.splitlines()]
    total = None
    for ln in lines:
        m = TOTAL_RE.search(ln)
        if m:
            total = _amount(m.group(1))
    found_date = find_date(text)
    vendor = next((ln[:40] for ln in lines if re.search(r"[A-Za-z]", ln)), None)
    items = []
    for ln in lines:
        if TOTAL_RE.search(ln):
            continue
        m = ITEM_RE.match(ln)
        if m:
            cents = _amount(m.group(2))
            if cents is not None:
                items.append(cents)
    conf = Decimal(1) if total is not None else Decimal("0.3")
    conf *= Decimal(1) if found_date is not None else Decimal("0.7")
    if not items:
        conf *= Decimal("0.9")
    elif total is None or sum(items) != total:
        conf *= Decimal("0.6")
    conf *= Decimal(mean_conf) / Decimal(100)
    return Parsed(vendor, total, found_date, items, conf.quantize(Decimal("0.0001")))


def status_for(conf: Decimal, threshold: Decimal) -> ReceiptStatus:
    return ReceiptStatus.NEEDS_CONFIRMATION if conf < threshold else ReceiptStatus.AUTO


def flags(text: str, ref: str) -> list[ScanFlag]:
    return scan_untrusted(Untrusted(text, SourceKind.RECEIPT, ref))


def match_bank(
    vendor: str | None, total_cents: int | None, d: date | None, bank: list[RawRow], today: date
) -> RawRow | None:
    """BKS-14 / BKS-27: the bank outflow for the same purchase (amount equal, date within
    3 days, vendor similarity >= 0.6). Undated receipts match by amount and vendor within
    the last 30 days. Closest date first, then id."""
    if total_cents is None or vendor is None:
        return None
    cands = [
        r
        for r in bank
        if r.amount_cents == -total_cents
        and jaccard(vendor, r.description) >= VENDOR_MATCH
        and (
            abs((r.date - d).days) <= MATCH_DAYS
            if d is not None
            else 0 <= (today - r.date).days <= UNDATED_LOOKBACK_DAYS
        )
    ]
    if not cands:
        return None
    ref = d or today
    return min(cands, key=lambda r: (abs((r.date - ref).days), r.id))
