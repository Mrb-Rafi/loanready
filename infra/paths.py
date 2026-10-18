"""Repository paths (ARC-09). Always ``pathlib``; never string concatenation."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
VAR_DIR = REPO_ROOT / "var"  # default; the configured one is var_dir()
EVIDENCE_DIR = REPO_ROOT / "evidence"
HANDOFF_DIR = REPO_ROOT / "handoff"


def var_dir() -> Path:
    """The configured runtime directory (``LOANREADY_VAR_DIR``, relative to the repo root)."""
    from infra.settings import get_settings

    p = Path(get_settings().var_dir)
    return p if p.is_absolute() else REPO_ROOT / p


def ensure_dirs() -> Path:
    """Create var/ and its standard subfolders; returns var_dir()."""
    root = var_dir()
    for sub in ("", "seed", "packs", "uploads", "lender_inbox", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
