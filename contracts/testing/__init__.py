"""Testing helpers (CON-22): test settings, in-memory fakes, DTO builders, fixtures."""

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
TINY_BUSINESS_PATH = FIXTURES_DIR / "tiny_business.json"
INJECTION_CORPUS_PATH = FIXTURES_DIR / "injection_corpus.json"
