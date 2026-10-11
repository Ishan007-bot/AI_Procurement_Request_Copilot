"""Central configuration: data location, policy reference date, model settings.

Values are read lazily from the environment so tests and eval runners can point
the system at a different data snapshot (PROCUREMENT_DATA_DIR) at runtime.
"""
from __future__ import annotations

import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"

# Used only if the policy file has no parseable reference-date line.
FALLBACK_REFERENCE_DATE = date(2026, 9, 30)
SECURITY_REVIEW_VALIDITY_DAYS = 365

DEFAULT_PROVIDER = "gemini"

# Both providers are called through their OpenAI-compatible endpoints.
# rpm = conservative free-tier requests/minute used when LLM_MAX_REQUESTS_PER_MINUTE is unset.
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "default_model": "gemini-3.8-flash",
        "rpm": 10,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "openai/gpt-oss-120b",
        "rpm": 30,
    },
}

_REFERENCE_DATE_PATTERN = re.compile(
    r"reference date[^0-9]*(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)


def get_data_dir() -> Path:
    configured = os.getenv("PROCUREMENT_DATA_DIR", "").strip()
    if not configured:
        return DEFAULT_DATA_DIR
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


@lru_cache(maxsize=8)
def _reference_date_for(policy_path: Path) -> date:
    try:
        text = policy_path.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_REFERENCE_DATE
    match = _REFERENCE_DATE_PATTERN.search(text)
    if not match:
        return FALLBACK_REFERENCE_DATE
    return date.fromisoformat(match.group(1))


def get_reference_date() -> date:
    """Policy reference date for all date-based checks (never the system clock)."""
    return _reference_date_for(get_data_dir() / "procurement_policy.md")


def get_vendor_risk_base_url() -> str:
    return os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


def get_vendor_risk_timeout() -> float:
    return float(os.getenv("VENDOR_RISK_TIMEOUT_SECONDS", "3"))


def get_llm_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower() or DEFAULT_PROVIDER
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported LLM_PROVIDER '{provider}'. Use one of: {', '.join(PROVIDERS)}")
    return provider


def get_provider_settings() -> dict:
    return PROVIDERS[get_llm_provider()]


def get_llm_api_key() -> str:
    return os.getenv(get_provider_settings()["api_key_env"], "").strip()


def get_model_name() -> str:
    return os.getenv("MODEL_NAME", "").strip() or get_provider_settings()["default_model"]


def get_llm_timeout() -> float:
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))


def get_llm_max_rpm() -> int:
    configured = int(os.getenv("LLM_MAX_REQUESTS_PER_MINUTE", "0") or 0)
    return configured or get_provider_settings()["rpm"]


def get_llm_max_retries() -> int:
    return int(os.getenv("LLM_MAX_RETRIES", "4"))


def get_llm_reasoning_effort() -> str | None:
    """Thinking depth for reasoning models (Gemini 3.x, gpt-oss). Blank/none disables the parameter."""
    value = os.getenv("LLM_REASONING_EFFORT", "low").strip().lower()
    return None if value in {"", "default", "unset"} else value
