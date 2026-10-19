"""Build the PDF editions of the technical documentation and the usage guide.

``python -m scripts.build_pdfs`` writes ``docs/pdf/LoanReady_Technical_Documentation.pdf``
and ``docs/pdf/LoanReady_Usage_Guide.pdf`` from ``docs/technical_doc.md`` and
``docs/usage_guide.md``.

Route: pandoc (GitHub-flavoured Markdown → standalone HTML with a print stylesheet), then
headless Chromium (HTML → A4 PDF with page numbers). Both are external tools, not Python
dependencies; the script stops with a clear message if one is missing. Links to other files
in the repository are rewritten to the GitHub repository so they work inside the PDF;
in-page section links keep working as PDF links.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from infra.paths import REPO_ROOT

REPO_URL = "https://github.com/Mrb-Rafi/loanready"
BRANCH = "main"
OUT_DIR = REPO_ROOT / "docs" / "pdf"
DOCS: tuple[tuple[str, str, str], ...] = (
    (
        "docs/technical_doc.md",
        "LoanReady_Technical_Documentation.pdf",
        "LoanReady: Technical Documentation",
    ),
    ("docs/usage_guide.md", "LoanReady_Usage_Guide.pdf", "LoanReady: Usage Guide"),
)
PDF_NAMES = {Path(src).name: pdf for src, pdf, _title in DOCS}

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm 15mm;
  @bottom-center { content: "LoanReady · page " counter(page) " of " counter(pages);
                   font: 8pt "DejaVu Sans", Arial, sans-serif; color: #666; } }
html { font: 9.6pt/1.45 "DejaVu Sans", "Liberation Sans", Arial, sans-serif; color: #1b1b1b; }
body { margin: 0; }
h1 { font-size: 20pt; color: #0b3d63; border-bottom: 2px solid #0b3d63; padding-bottom: 4px;
     margin: 0 0 10px; }
h2 { font-size: 14pt; color: #0b3d63; margin: 22px 0 8px; border-bottom: 1px solid #c9d6e2;
     padding-bottom: 2px; break-after: avoid; }
h3 { font-size: 11.5pt; color: #134e7a; margin: 16px 0 6px; break-after: avoid; }
h4 { font-size: 10pt; margin: 12px 0 4px; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
a { color: #0b5cad; text-decoration: none; }
code { font: 8.4pt "DejaVu Sans Mono", "Liberation Mono", monospace; background: #f1f4f7;
       padding: 0 2px; border-radius: 2px; }
pre { font: 6.3pt/1.3 "DejaVu Sans Mono", "Liberation Mono", monospace; background: #f5f7f9;
      border: 1px solid #dde3e9; border-radius: 3px; padding: 6px 8px;
      white-space: pre !important; overflow: hidden; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: inherit;
           white-space: pre !important; word-wrap: normal !important; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 8.4pt;
        break-inside: auto; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { background: #e8eef4; text-align: left; font-weight: bold; }
th, td { border: 1px solid #c9d3dc; padding: 3px 5px; vertical-align: top; }
blockquote { margin: 8px 0; padding: 6px 10px; border-left: 4px solid #7aa7d1;
             background: #f2f7fc; color: #22313f; }
blockquote p { margin: 2px 0; }
hr { border: 0; border-top: 1px solid #d5dde5; margin: 16px 0; }
img { display: block; max-width: 100%; margin: 10px auto 2px; border: 1px solid #c9d3dc;
      border-radius: 3px; break-inside: avoid; }
p:has(> img) { break-inside: avoid; margin: 8px 0 12px; }
p:has(> img) > em { display: block; font-size: 8pt; color: #4a5a68; text-align: center;
      margin-top: 3px; }
"""


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise SystemExit(f"{tool} is required to build the PDFs (not found on PATH)")
    return path


def _chromium() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if (path := shutil.which(name)) is not None:
            return path
    raise SystemExit("Chromium or Google Chrome is required to build the PDFs (not found)")


def rewrite_links(markdown: str, source: str) -> str:
    """Repository-relative links → GitHub URLs (the other guide → its PDF); keep #anchors
    and absolute URLs as they are."""
    base = (REPO_ROOT / source).parent

    def fix(m: re.Match[str]) -> str:
        text, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0)
        path, _, frag = target.partition("#")
        resolved = (base / path).resolve()
        try:
            rel = resolved.relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            return m.group(0)
        if resolved.name in PDF_NAMES and resolved.parent == (REPO_ROOT / "docs").resolve():
            url = f"{REPO_URL}/blob/{BRANCH}/docs/pdf/{PDF_NAMES[resolved.name]}"
        else:
            kind = "tree" if resolved.is_dir() else "blob"
            url = f"{REPO_URL}/{kind}/{BRANCH}/{rel}" + (f"#{frag}" if frag else "")
        return f"[{text}]({url})"

    def image(m: re.Match[str]) -> str:  # images: absolute local file, embedded in the PDF
        return f"![{m.group(1)}]({(base / m.group(2)).resolve().as_uri()})"

    markdown = re.sub(r"!\[([^\]]*)\]\(([^)\s:]+)\)", image, markdown)
    return re.sub(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)", fix, markdown)


def build_one(source: str, pdf_name: str, title: str, out_dir: Path, work: Path) -> Path:
    md = rewrite_links((REPO_ROOT / source).read_text(encoding="utf-8"), source)
    md_path, html_path = work / "doc.md", work / "doc.html"
    css_path = work / "style.html"
    md_path.write_text(md, encoding="utf-8")
    css_path.write_text(f"<style>{CSS}</style>\n", encoding="utf-8")
    subprocess.run(
        [
            _need("pandoc"),
            "-f",
            "gfm",
            "-t",
            "html5",
            "--standalone",
            "--metadata",
            f"pagetitle={title}",
            "-H",
            str(css_path),
            "-o",
            str(html_path),
            str(md_path),
        ],
        check=True,
    )
    out = out_dir / pdf_name
    subprocess.run(
        [
            _chromium(),
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--user-data-dir={work / 'chrome'}",
            f"--print-to-pdf={out}",
            html_path.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.build_pdfs", description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for source, pdf_name, title in DOCS:
        with tempfile.TemporaryDirectory() as tmp:
            out = build_one(source, pdf_name, title, args.out_dir, Path(tmp))
        print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
