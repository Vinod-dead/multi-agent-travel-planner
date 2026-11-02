"""Application settings, read from environment variables (and a local ``.env`` file)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(_env(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(_env(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _env_json_object(name: str) -> dict:
    """A JSON object from the environment, e.g. {"chat_template_kwargs": {"enable_thinking": false}}."""
    raw = _env(name, "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON (for example {{\"a\": 1}}): {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object, not {type(value).__name__}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """All runtime configuration in one immutable object."""

    # --- language model (any OpenAI-compatible endpoint: Ollama, OpenAI, LM Studio, ...)
    llm_enabled: bool
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    llm_retries: int
    llm_concurrency: int
    llm_temperature: float
    llm_max_tokens: int
    llm_json_mode: bool
    llm_extra_body: dict

    # --- optional web research
    tavily_api_key: str

    # --- app
    default_currency: str
    max_days: int
    host: str
    port: int
    data_dir: Path
    log_level: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "travel_planner.db"

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once. Call ``get_settings.cache_clear()`` after changing env vars."""
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()  # also honour a .env in the current working directory

    data_dir = Path(_env("DATA_DIR", str(PROJECT_ROOT / "data")))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    return Settings(
        llm_enabled=_env_bool("LLM_ENABLED", True),
        llm_base_url=_env("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
        llm_api_key=_env("LLM_API_KEY", _env("OPENAI_API_KEY", "ollama")),
        llm_model=_env("LLM_MODEL", "qwen2.5:3b"),
        llm_timeout=_env_float("LLM_TIMEOUT", 180.0, minimum=5.0),
        llm_retries=_env_int("LLM_RETRIES", 2, minimum=0, maximum=5),
        llm_concurrency=_env_int("LLM_CONCURRENCY", 2, minimum=1, maximum=16),
        llm_temperature=_env_float("LLM_TEMPERATURE", 0.4),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 2048, minimum=256, maximum=16000),
        llm_json_mode=_env_bool("LLM_JSON_MODE", True),
        llm_extra_body=_env_json_object("LLM_EXTRA_BODY"),
        tavily_api_key=_env("TAVILY_API_KEY"),
        default_currency=_env("DEFAULT_CURRENCY", "INR").upper()[:3],
        max_days=_env_int("MAX_DAYS", 21, minimum=1, maximum=60),
        host=_env("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8000, minimum=1, maximum=65535),
        data_dir=data_dir,
        log_level=_env("LOG_LEVEL", "INFO").upper(),
    )
