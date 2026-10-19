"""Typed settings (PRD/01 §5). The only module that reads the environment (ARC-08),
besides ``sandbox/settings.py``.

Importing this module also registers the clock and ID providers with ``contracts`` so
``get_clock()`` is a FixedClock in test/demo and a SystemClock in dev.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from contracts import clock as _clock
from contracts import ids as _ids
from contracts.enums import Framework
from infra.paths import REPO_ROOT


def _f(env: str, default: object) -> object:
    return Field(default=default, validation_alias=AliasChoices(env, env.lower()))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    # runtime
    env: Literal["dev", "test", "demo"] = _f("LOANREADY_ENV", "dev")  # type: ignore[assignment]
    as_of: date = _f("LOANREADY_AS_OF", date(2026, 10, 14))  # type: ignore[assignment]
    tz: str = _f("LOANREADY_TZ", "Asia/Dhaka")  # type: ignore[assignment]
    seed: int = _f("LOANREADY_SEED", 20261014)  # type: ignore[assignment]
    var_dir: str = _f("LOANREADY_VAR_DIR", "var")  # type: ignore[assignment]
    framework: Framework = _f("LOANREADY_FRAMEWORK", Framework.IFRS_SME)  # type: ignore[assignment]
    currency: str = _f("LOANREADY_CURRENCY", "USD")  # type: ignore[assignment]
    ui_fake: bool = _f("LOANREADY_UI_FAKE", False)  # type: ignore[assignment]

    # sandbox (synthetic tokens; SecretStr keeps them out of repr and logs)
    sandbox_url: str = _f("SANDBOX_URL", "http://127.0.0.1:8100")  # type: ignore[assignment]
    sandbox_read_token: SecretStr = _f("SANDBOX_READ_TOKEN", SecretStr("sbx-read-dev-0001"))  # type: ignore[assignment]
    sandbox_write_token: SecretStr = _f("SANDBOX_WRITE_TOKEN", SecretStr("sbx-write-dev-0001"))  # type: ignore[assignment]
    sandbox_admin_token: SecretStr = _f("SANDBOX_ADMIN_TOKEN", SecretStr("sbx-admin-dev-0001"))  # type: ignore[assignment]
    sandbox_mode: Literal["inprocess", "external"] = _f("SANDBOX_MODE", "inprocess")  # type: ignore[assignment]
    sbx_write_rate_per_min: int = _f("SBX_WRITE_RATE_PER_MIN", 30)  # type: ignore[assignment]
    sbx_max_payment_cents: int = _f("SBX_MAX_PAYMENT_CENTS", 500000)  # type: ignore[assignment]
    sbx_daily_outgoing_cents: int = _f("SBX_DAILY_OUTGOING_CENTS", 1000000)  # type: ignore[assignment]
    bank_provider: Literal["mock", "plaid"] = _f("BANK_PROVIDER", "mock")  # type: ignore[assignment]
    pos_provider: Literal["mock", "square", "stripe"] = _f("POS_PROVIDER", "mock")  # type: ignore[assignment]

    # LLM
    llm_backend: Literal["mock", "ollama", "openai", "hybrid"] = _f("LLM_BACKEND", "mock")  # type: ignore[assignment]
    llm_routes_file: str = _f("LLM_ROUTES_FILE", "")  # type: ignore[assignment]
    ollama_timeout_s: int = _f("OLLAMA_TIMEOUT_S", 60)  # type: ignore[assignment]
    openai_timeout_s: int = _f("OPENAI_TIMEOUT_S", 30)  # type: ignore[assignment]
    ollama_host: str = _f("OLLAMA_HOST", "http://127.0.0.1:11434")  # type: ignore[assignment]
    ollama_model: str = _f("OLLAMA_MODEL", "phi3:mini")  # type: ignore[assignment]
    openai_api_key: SecretStr = _f("OPENAI_API_KEY", SecretStr(""))  # type: ignore[assignment]
    openai_model: str = _f("OPENAI_MODEL", "")  # type: ignore[assignment]
    openai_max_calls: int = _f("OPENAI_MAX_CALLS", 300)  # type: ignore[assignment]
    openai_max_usd: Decimal = _f("OPENAI_MAX_USD", Decimal("8.00"))  # type: ignore[assignment]
    # all-time cap across days (prepaid credit); 0 = no all-time cap (daily caps still apply)
    openai_total_max_usd: Decimal = _f("OPENAI_TOTAL_MAX_USD", Decimal("0"))  # type: ignore[assignment]
    openai_price_in_per_mtok: Decimal = _f("OPENAI_PRICE_IN_PER_MTOK", Decimal("0"))  # type: ignore[assignment]
    openai_price_out_per_mtok: Decimal = _f("OPENAI_PRICE_OUT_PER_MTOK", Decimal("0"))  # type: ignore[assignment]

    # OCR
    ocr_backend: Literal["auto", "tesseract", "sidecar"] = _f("OCR_BACKEND", "auto")  # type: ignore[assignment]
    tesseract_cmd: str = _f("TESSERACT_CMD", "")  # type: ignore[assignment]

    # governance defaults (seeded into the settings table by WP9)
    cap_per_action_cents: int = _f("CAP_PER_ACTION_CENTS", 500000)  # type: ignore[assignment]
    cap_per_day_cents: int = _f("CAP_PER_DAY_CENTS", 1000000)  # type: ignore[assignment]
    cap_per_payee_week_cents: int = _f("CAP_PER_PAYEE_WEEK_CENTS", 800000)  # type: ignore[assignment]
    cap_msgs_per_customer_week: int = _f("CAP_MSGS_PER_CUSTOMER_WEEK", 2)  # type: ignore[assignment]
    approval_ttl_seconds: int = _f("APPROVAL_TTL_SECONDS", 900)  # type: ignore[assignment]
    confidence_confirm_threshold: Decimal = _f("CONFIDENCE_CONFIRM_THRESHOLD", Decimal("0.80"))  # type: ignore[assignment]
    receipt_confirm_threshold: Decimal = _f("RECEIPT_CONFIRM_THRESHOLD", Decimal("0.75"))  # type: ignore[assignment]

    @property
    def deterministic(self) -> bool:
        """True in test and demo: FixedClock and seeded IDs."""
        return self.env in ("test", "demo")

    def env_value(self, env_name: str) -> str:
        """Value of a setting by its .env variable name, as a string (seeding the
        settings table from ``contracts.registry.SETTINGS_ENV``)."""
        for name, info in type(self).model_fields.items():
            alias = info.validation_alias
            choices = getattr(alias, "choices", [alias])
            if env_name in choices:
                value = getattr(self, name)
                if isinstance(value, SecretStr):
                    raise KeyError(f"{env_name} is a secret; read it in its owning module")
                return str(value.value if hasattr(value, "value") else value)
        raise KeyError(env_name)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings, read once. In test mode the developer's ``.env`` is ignored."""
    if os.environ.get("LOANREADY_ENV") == "test":
        return Settings(_env_file=None)  # type: ignore[call-arg]
    return Settings()


def reset_settings() -> None:
    """Forget cached settings and the process clock/ID factory (tests, demo start-up)."""
    get_settings.cache_clear()
    _clock.reset_clock()
    _ids.reset_id_factory()


def _clock_provider() -> _clock.Clock:
    s = get_settings()
    return _clock.FixedClock(s.as_of) if s.deterministic else _clock.SystemClock(s.as_of)


def _id_provider() -> _ids.IdFactory:
    s = get_settings()
    return _ids.IdFactory(s.seed if s.deterministic else None)


_clock.register_clock_provider(_clock_provider)
_ids.register_id_provider(_id_provider)
