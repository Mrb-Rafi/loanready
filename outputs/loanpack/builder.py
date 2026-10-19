"""Loan-pack PDF (PRD/08 §5; OUT-04, OUT-05, OUT-08).

- A4, reportlab platypus, built-in Helvetica only.
- One outline entry (bookmark) per section, titled with the section key.
- Footer on every page: "LoanReady pack {pack_id} · page n/N · content hash {hash[:16]}",
  where content_hash = params_hash(content) is known before rendering. The file hash
  (sha256 of the PDF bytes) is returned and stored; it cannot be printed inside the file.
- Deterministic: ``invariant=1`` and a creation date fixed to the content's AS_OF, so the
  same content and pack id give byte-identical PDFs on Windows and Linux.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import PageBreak, SimpleDocTemplate

from contracts.canonical import file_sha256, params_hash
from contracts.dto_outputs import LOAN_PACK_SECTIONS, LoanPackContent
from outputs.loanpack import styles as st
from outputs.loanpack.sections import SECTIONS, SectionMarker

PAGE_BREAK_BEFORE = frozenset({"pnl", "cash_flow", "balance_sheet", "forecast", "financing"})


@dataclass(frozen=True)
class BuiltPack:
    path: Path
    sha256: str
    content_hash: str
    sections: list[str]
    metadata: dict[str, Any]


def _canvas_class(pack_id: str, content_hash: str, as_of_text: str, compress: bool) -> type[Canvas]:
    """A canvas that defers page output so the footer can show page n of N."""

    class NumberedCanvas(Canvas):
        def __init__(self, *args: Any, **kwargs: Any):
            kwargs["invariant"] = 1
            kwargs["pageCompression"] = 1 if compress else 0
            super().__init__(*args, **kwargs)
            self._doc.info.creator = "LoanReady"
            self._doc.info.producer = "LoanReady (reportlab)"
            self._doc.info._dateFormatter = lambda *_a, **_k: as_of_text
            self._saved: list[dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 (reportlab API name)
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._saved)
            for n, state in enumerate(self._saved, start=1):
                self.__dict__.update(state)
                self.setFont(st.FONT, 7)
                self.setFillColor(st.MUTED)
                self.drawString(
                    15 * mm,
                    10 * mm,
                    f"LoanReady pack {pack_id} · page {n}/{total} · "
                    f"content hash {content_hash[:16]}",
                )
                super().showPage()
            super().save()

    return NumberedCanvas


def build_pdf(
    content: LoanPackContent, pack_id: str, out_dir: Path, compress: bool = True
) -> BuiltPack:
    """Render the pack to ``out_dir / loan_pack_{as_of}_{pack_id}.pdf`` and write the
    ``.sections.json`` sidecar. Pure apart from the two files. ``compress=False`` leaves
    page streams readable (tests check the footer text)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"loan_pack_{content.as_of.isoformat()}_{pack_id}.pdf"
    content_hash = params_hash(content)
    metadata = {
        "business": content.business_name,
        "as_of": content.as_of.isoformat(),
        "requested_amount_cents": content.requested_amount_cents,
        "term_days": content.term_days,
        "framework": content.framework.value,
        "sections": list(LOAN_PACK_SECTIONS),
        "content_hash": content_hash,
    }
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=f"LoanReady loan pack {pack_id}",
        author="LoanReady",
        subject=f"{content.business_name} as of {content.as_of.isoformat()}",
        keywords=f"content_hash={content_hash}",
        creator="LoanReady",
        invariant=1,
    )
    story: list[Any] = []
    for key in LOAN_PACK_SECTIONS:
        if key in PAGE_BREAK_BEFORE and story:
            story.append(PageBreak())
        story.append(SectionMarker(key))
        story.extend(SECTIONS[key](content))
    as_of_text = f"D:{content.as_of.strftime('%Y%m%d')}000000+00'00'"
    doc.build(story, canvasmaker=_canvas_class(pack_id, content_hash, as_of_text, compress))
    sections = list(LOAN_PACK_SECTIONS)
    sidecar = path.with_name(path.stem + ".sections.json")
    sidecar.write_text(
        json.dumps({"pack_id": pack_id, "sections": sections, **metadata}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return BuiltPack(path, file_sha256(path), content_hash, sections, metadata)
