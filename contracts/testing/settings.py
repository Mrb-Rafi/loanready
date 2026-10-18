"""Environment for tests (never a developer's ``.env``)."""

from __future__ import annotations

from pathlib import Path

TEST_AS_OF = "2026-10-14"
TEST_SEED = 20261014


def settings_for_tests(var_dir: Path | str | None = None) -> dict[str, str]:
    """Environment variables every test runs with: test mode, mock LLM, sidecar OCR,
    in-process sandbox, AS_OF 2026-10-14, seed 20261014, ``var_dir`` (a pytest tmp dir)."""
    env = {
        "LOANREADY_ENV": "test",
        "LOANREADY_AS_OF": TEST_AS_OF,
        "LOANREADY_TZ": "Asia/Dhaka",
        "LOANREADY_SEED": str(TEST_SEED),
        "LOANREADY_FRAMEWORK": "IFRS_SME",
        "LOANREADY_CURRENCY": "USD",
        "LOANREADY_UI_FAKE": "0",
        "SANDBOX_URL": "http://127.0.0.1:8100",
        "SANDBOX_READ_TOKEN": "sbx-read-test-0001",
        "SANDBOX_WRITE_TOKEN": "sbx-write-test-0001",
        "SANDBOX_ADMIN_TOKEN": "sbx-admin-test-0001",
        "SANDBOX_MODE": "inprocess",
        "SBX_WRITE_RATE_PER_MIN": "30",
        "SBX_MAX_PAYMENT_CENTS": "500000",
        "SBX_DAILY_OUTGOING_CENTS": "1000000",
        "BANK_PROVIDER": "mock",
        "POS_PROVIDER": "mock",
        "LLM_BACKEND": "mock",
        "LLM_ROUTES_FILE": "",
        "OLLAMA_HOST": "http://127.0.0.1:11434",
        "OLLAMA_MODEL": "phi3:mini",
        "OPENAI_API_KEY": "",
        "OPENAI_MODEL": "",
        "OPENAI_MAX_CALLS": "300",
        "OPENAI_MAX_USD": "8.00",
        "OCR_BACKEND": "sidecar",
        "TESSERACT_CMD": "",
        "CAP_PER_ACTION_CENTS": "500000",
        "CAP_PER_DAY_CENTS": "1000000",
        "CAP_PER_PAYEE_WEEK_CENTS": "800000",
        "CAP_MSGS_PER_CUSTOMER_WEEK": "2",
        "APPROVAL_TTL_SECONDS": "900",
        "CONFIDENCE_CONFIRM_THRESHOLD": "0.80",
        "RECEIPT_CONFIRM_THRESHOLD": "0.75",
    }
    if var_dir is not None:
        env["LOANREADY_VAR_DIR"] = str(var_dir)
    return env
