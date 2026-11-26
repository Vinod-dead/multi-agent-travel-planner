from __future__ import annotations

import httpx
import pytest
from openai import AsyncOpenAI

from tests.fake_llm import create_fake_llm
from travel_planner.config import get_settings
from travel_planner.llm import LLMClient


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Every test gets its own data dir and a known configuration."""
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "OPENAI_API_KEY", "LLM_ENABLED", "TAVILY_API_KEY", "MAX_DAYS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_MODEL", "fake")
    monkeypatch.setenv("LLM_RETRIES", "1")
    monkeypatch.setenv("DEFAULT_CURRENCY", "INR")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def make_llm(settings):
    """Build an LLMClient that talks to the fake server through the real OpenAI client."""
    clients: list[httpx.AsyncClient] = []

    def factory(mode: str = "clean"):
        fake = create_fake_llm(mode)
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake), base_url="http://fake/v1")
        clients.append(http)
        openai_client = AsyncOpenAI(base_url="http://fake/v1", api_key="test", http_client=http, max_retries=0)
        return LLMClient(settings, client=openai_client), fake

    yield factory
