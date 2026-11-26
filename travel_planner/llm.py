"""A small, defensive client for any OpenAI-compatible chat endpoint.

Works with Ollama (default), OpenAI, LM Studio, vLLM, Groq, OpenRouter, ... Anything that
speaks ``/v1/chat/completions``.

Two failure classes are kept apart on purpose:

* :class:`LLMUnavailable` - the server cannot be used (down, wrong model, bad key,
  timeout). Retrying will not help, so the planner switches to its offline estimator.
* :class:`LLMBadOutput` - the server answered, but not with usable JSON. This is common
  with small models, so we retry with a corrective message and repair what we can.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import openai
from openai import AsyncOpenAI

from travel_planner.config import Settings, get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Base class for language-model problems."""


class LLMUnavailable(LLMError):
    """The model server cannot be used at all (retrying will not help)."""


class LLMBadOutput(LLMError):
    """The model replied, but the reply could not be parsed."""


# --------------------------------------------------------------------------- JSON repair
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_PY_LITERALS = (
    (re.compile(r"\bTrue\b"), "true"),
    (re.compile(r"\bFalse\b"), "false"),
    (re.compile(r"\bNone\b"), "null"),
)


def _first_json_block(text: str) -> str:
    """Return the first balanced ``{...}``/``[...]`` block, or the unbalanced tail."""
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start < 0:
        raise LLMBadOutput("the reply contained no JSON")

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]  # truncated: let the repair step close it


def _closers_for(prefix: str) -> str | None:
    """Return the characters needed to close all open strings/brackets in ``prefix``."""
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in prefix:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack:
                return None
            stack.pop()
    return ('"' if in_str else "") + "".join(reversed(stack))


def _basic_clean(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    for pattern, repl in _PY_LITERALS:
        s = pattern.sub(repl, s)
    return _TRAILING_COMMA_RE.sub(r"\1", s)


def _repair_truncated(s: str) -> Any:
    """Close a cut-off JSON document, dropping the last incomplete element if needed."""
    closers = _closers_for(s)
    if closers is not None:
        for tail in ("", "null", ":null"):
            try:
                return json.loads(_basic_clean(s.rstrip().rstrip(",") + tail + closers))
            except json.JSONDecodeError:
                continue
    # Cut back to earlier commas until something parses.
    positions = [m.start() for m in re.finditer(",", s)]
    for pos in reversed(positions[-60:]):
        prefix = s[:pos]
        closers = _closers_for(prefix)
        if closers is None:
            continue
        try:
            return json.loads(_basic_clean(prefix + closers))
        except json.JSONDecodeError:
            continue
    raise LLMBadOutput("the reply was not valid JSON")


def extract_json(text: str | None) -> Any:
    """Parse JSON from a model reply, tolerating the usual small-model mistakes.

    Handles ``<think>`` blocks, Markdown fences, text around the JSON, smart quotes,
    trailing commas, Python literals and replies that were cut off mid-way.
    """
    if not text or not text.strip():
        raise LLMBadOutput("the reply was empty")
    cleaned = _THINK_RE.sub("", text).strip()
    fence = _FENCE_RE.search(cleaned)
    if fence and fence.group(1).strip():
        cleaned = fence.group(1).strip()

    block = _first_json_block(cleaned)
    for candidate in (block, _basic_clean(block)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return _repair_truncated(block)


def _explain_empty(choice: Any, message: Any) -> str:
    """Say *why* a reply had no text, so thinking-model problems are easy to spot."""
    finish = getattr(choice, "finish_reason", None)
    reasoning = ""
    for field in ("reasoning_content", "reasoning"):
        reasoning = reasoning or getattr(message, field, None) or ""
    text = "the model returned an empty reply"
    if finish:
        text += f" (finish_reason={finish})"
    if reasoning:
        text += (
            ". It produced only 'thinking' text and ran out of tokens before answering. Turn thinking off with "
            'LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}} in .env, or pick a model that '
            "does not think"
        )
    elif finish == "length":
        text += ". The token limit was reached before any answer; try a model that does not think"
    return text


# --------------------------------------------------------------------------- client
class LLMClient:
    """Async chat client with concurrency limiting, retries and JSON extraction."""

    def __init__(self, settings: Settings | None = None, client: AsyncOpenAI | Any | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self._sem = asyncio.Semaphore(self.settings.llm_concurrency)
        self._json_mode = self.settings.llm_json_mode
        self._ping_cache: tuple[float, dict[str, Any]] | None = None

    # -- plumbing ---------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key or "none",
                timeout=self.settings.llm_timeout,
                max_retries=0,
            )
        return self._client

    @property
    def host(self) -> str:
        parsed = urlparse(self.settings.llm_base_url)
        return parsed.netloc or self.settings.llm_base_url

    def _unavailable(self, exc: Exception, model: str) -> LLMUnavailable:
        base = self.settings.llm_base_url
        if isinstance(exc, openai.APITimeoutError):
            return LLMUnavailable(
                f"The model '{model}' did not answer within {self.settings.llm_timeout:.0f}s "
                "(increase LLM_TIMEOUT or use a smaller/faster model)."
            )
        if isinstance(exc, openai.APIConnectionError):
            return LLMUnavailable(
                f"Cannot reach the language model at {base}. "
                "If you use Ollama, start it with 'ollama serve'."
            )
        if isinstance(exc, openai.NotFoundError):
            return LLMUnavailable(
                f"Model '{model}' was not found on {self.host}. "
                f"With Ollama run: ollama pull {model}"
            )
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LLMUnavailable("The language-model API key was rejected (check LLM_API_KEY).")
        return LLMUnavailable(f"Language model error: {exc}")

    # -- public API -------------------------------------------------------------
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """Send ``messages`` and return the reply text."""
        if not self.enabled:
            raise LLMUnavailable("The language model is disabled (LLM_ENABLED=false).")
        model = model or self.settings.llm_model
        attempts = self.settings.llm_retries + 1
        last: Exception | None = None

        attempt = 0
        while attempt < attempts:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": self.settings.llm_temperature if temperature is None else temperature,
                "max_tokens": max_tokens or self.settings.llm_max_tokens,
            }
            if json_mode and self._json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if self.settings.llm_extra_body:
                kwargs["extra_body"] = self.settings.llm_extra_body
            try:
                async with self._sem:
                    response = await self.client.chat.completions.create(**kwargs)
            except openai.BadRequestError as exc:
                if "response_format" in kwargs and self._json_mode:
                    log.info("Server rejected JSON mode; continuing without it (%s)", exc)
                    self._json_mode = False
                    continue  # retry immediately, without using up an attempt
                raise self._unavailable(exc, model) from exc
            except (openai.RateLimitError, openai.InternalServerError) as exc:
                last = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2**attempt, 8))
                attempt += 1
                continue
            except openai.OpenAIError as exc:
                raise self._unavailable(exc, model) from exc
            except Exception as exc:  # noqa: BLE001 - never let a client bug crash a plan
                raise LLMUnavailable(f"Unexpected language-model error: {exc}") from exc

            choices = getattr(response, "choices", None) or []
            message = getattr(choices[0], "message", None) if choices else None
            content = getattr(message, "content", None)
            if content and content.strip():
                return content
            last = LLMBadOutput(_explain_empty(choices[0] if choices else None, message))
            log.warning("Empty model reply: %s", last)
            attempt += 1

        if isinstance(last, (openai.RateLimitError, openai.InternalServerError)):
            raise LLMUnavailable(f"The language model is overloaded or failing: {last}")
        raise LLMBadOutput(str(last) if last else "no reply from the model")

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Any:
        """Ask for JSON and return the parsed value, retrying with a corrective hint."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last: LLMBadOutput | None = None
        for _ in range(self.settings.llm_retries + 1):
            reply = await self.chat(
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=True,
            )
            try:
                return extract_json(reply)
            except LLMBadOutput as exc:
                last = exc
                log.warning("Unusable JSON from model (%s); asking again", exc)
                messages = [
                    *messages[:2],
                    {"role": "assistant", "content": reply[:1500]},
                    {
                        "role": "user",
                        "content": "That was not valid JSON. Reply again with ONLY the JSON "
                        "object described above - no explanations, no Markdown.",
                    },
                ]
        raise last or LLMBadOutput("no usable JSON")

    async def ping(self, *, use_cache: bool = True) -> dict[str, Any]:
        """Check that the server answers. Returns ``{ok, models, error}`` (never raises)."""
        if not self.enabled:
            return {"ok": False, "models": [], "error": "Language model disabled (LLM_ENABLED=false)."}
        now = time.monotonic()
        if use_cache and self._ping_cache and now - self._ping_cache[0] < 10:
            return self._ping_cache[1]

        try:
            listing = await asyncio.wait_for(self.client.models.list(), timeout=6)
            models = sorted({m.id for m in getattr(listing, "data", [])})[:2000]
            result: dict[str, Any] = {"ok": True, "models": models, "error": ""}
        except asyncio.TimeoutError:
            result = {"ok": False, "models": [], "error": f"{self.host} did not respond in time."}
        except openai.OpenAIError as exc:
            result = {"ok": False, "models": [], "error": str(self._unavailable(exc, self.settings.llm_model))}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "models": [], "error": f"Language model error: {exc}"}
        self._ping_cache = (now, result)
        return result

    async def aclose(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()
