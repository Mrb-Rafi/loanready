"""Fonts, colours and table styles for the loan pack. Built-in Helvetica only, so the PDF
renders the same on every OS and needs no font files."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import TableStyle

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#616E7C")
ACCENT = colors.HexColor("#0B6E4F")
RULE = colors.HexColor("#CBD2D9")
BAND = colors.HexColor("#F0F4F8")
WARN = colors.HexColor("#B44D12")

_base = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "lr-title",
    parent=_base["Title"],
    fontName=FONT_BOLD,
    fontSize=20,
    textColor=INK,
    leading=24,
    spaceAfter=10,
)
H1 = ParagraphStyle(
    "lr-h1",
    parent=_base["Heading1"],
    fontName=FONT_BOLD,
    fontSize=14,
    textColor=ACCENT,
    spaceBefore=6,
    spaceAfter=6,
)
H2 = ParagraphStyle(
    "lr-h2",
    parent=_base["Heading2"],
    fontName=FONT_BOLD,
    fontSize=11,
    textColor=INK,
    spaceBefore=6,
    spaceAfter=3,
)
BODY = ParagraphStyle(
    "lr-body", parent=_base["BodyText"], fontName=FONT, fontSize=9, leading=12, textColor=INK
)
SMALL = ParagraphStyle("lr-small", parent=BODY, fontSize=7.5, leading=9.5, textColor=MUTED)
BANNER = ParagraphStyle(
    "lr-banner",
    parent=BODY,
    fontName=FONT_BOLD,
    fontSize=10,
    textColor=WARN,
    spaceBefore=6,
    spaceAfter=6,
)


def table_style(header_rows: int = 1, bold_rows: tuple[int, ...] = ()) -> TableStyle:
    cmds: list[tuple[object, ...]] = [
        ("FONT", (0, 0), (-1, -1), FONT, 7),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 0.6, INK),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, BAND]),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]
    if header_rows:
        cmds.append(("FONT", (0, 0), (-1, header_rows - 1), FONT_BOLD, 7))
    for r in bold_rows:
        cmds.append(("FONT", (0, r), (-1, r), FONT_BOLD, 7))
        cmds.append(("LINEABOVE", (0, r), (-1, r), 0.3, RULE))
    return TableStyle(cmds)
