"""Optional live web research through Tavily (only used when TAVILY_API_KEY is set)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from travel_planner.config import Settings

log = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"


async def web_search(query: str, settings: Settings, *, max_results: int = 4) -> list[dict[str, str]]:
    """Return ``[{title, url, content}]``. Never raises: research is a bonus, not a requirement."""
    if not settings.tavily_enabled:
        return []
    payload: dict[str, Any] = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    headers = {"Authorization": f"Bearer {settings.tavily_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(TAVILY_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Web research failed (%s); continuing without it", exc)
        return []

    results: list[dict[str, str]] = []
    for item in data.get("results", []) if isinstance(data, dict) else []:
        url = str(item.get("url", ""))
        if not url.startswith(("http://", "https://")):
            continue
        results.append(
            {
                "title": str(item.get("title", "Untitled"))[:160],
                "url": url,
                "content": str(item.get("content", ""))[:600],
            }
        )
    return results
