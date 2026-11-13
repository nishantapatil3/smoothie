"""Pluggable summary provider.

A single generic provider talks to any OpenAI-compatible ``/chat/completions``
endpoint. It is up to the user where that endpoint points — a local summarizer
LLM (``http://localhost:11434/v1``), a remote OpenAI-compatible provider, vLLM,
LM Studio, etc. The backend only needs a URL, an optional API key, and a model
name.
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
import random
import re

import httpx

from .config import get_settings
from .prompts import (
    SUMMARY_SYSTEM_PROMPT,
    TITLE_SYSTEM_PROMPT,
    build_summary_prompt,
    build_title_prompt,
)

logger = logging.getLogger("smoothie.summarize")


class SummaryError(RuntimeError):
    pass


class SummaryResult:
    """Outcome of a summarize call: the short title plus the markdown summary."""

    def __init__(self, title: str, summary: str) -> None:
        self.title = title
        self.summary = summary


class SummaryProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def summarize(self, transcript: str) -> SummaryResult:
        ...

    @abc.abstractmethod
    async def generate_title(self, summary: str) -> str:
        """Produce a short (3-4 word) title from an existing summary."""
        ...


async def _retry_with_backoff(coro, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    """Execute a coroutine with exponential backoff retry on transient errors."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await coro()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.1), max_delay)
            logger.warning(
                "Summarizer request failed (attempt %d/%d): %s. Retrying in %.1fs...",
                attempt + 1, max_retries + 1, exc, delay
            )
            await asyncio.sleep(delay)
        except httpx.HTTPStatusError as exc:
            # Retry on 5xx server errors, but not on 4xx client errors
            if 500 <= exc.response.status_code < 600:
                last_exc = exc
                if attempt == max_retries:
                    break
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.1), max_delay)
                logger.warning(
                    "Summarizer returned %s (attempt %d/%d). Retrying in %.1fs...",
                    exc.response.status_code, attempt + 1, max_retries + 1, delay
                )
                await asyncio.sleep(delay)
            else:
                # 4xx errors are not retryable
                raise
    raise last_exc


class ApiEndpointProvider(SummaryProvider):
    """Summarizes via an OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:  # noqa: FBT001, FBT002
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    async def summarize(self, transcript: str) -> SummaryResult:
        # If the user gave the bare base (e.g. http://host:11434/v1) we append
        # the chat completions path; if they already included it, don't double up.
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": build_summary_prompt(transcript)},
            ],
            "temperature": 0.2,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async def _do_request():
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await _retry_with_backoff(_do_request)
        except httpx.ConnectError as exc:
            raise SummaryError(
                f"Could not reach summarizer LLM at {url}. Is it running?"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SummaryError(f"Summarizer LLM returned error: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise SummaryError(f"Summarizer request timed out after retries") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SummaryError(f"Unexpected summary endpoint response: {data!r}") from exc

        return _parse_summary_result(content)

    async def generate_title(self, summary: str) -> str:
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": build_title_prompt(summary)},
            ],
            "temperature": 0.2,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async def _do_request():
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await _retry_with_backoff(_do_request)
        except httpx.ConnectError as exc:
            raise SummaryError(
                f"Could not reach summarizer LLM at {url}. Is it running?"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SummaryError(f"Summarizer LLM returned error: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise SummaryError(f"Summarizer request timed out after retries") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SummaryError(f"Unexpected title endpoint response: {data!r}") from exc

        return _parse_title(content)


def get_provider() -> SummaryProvider:
    settings = get_settings()
    if settings.summary_provider == "codex":
        from .codex_summarize import CodexProvider

        return CodexProvider(
            settings.codex_bin, settings.codex_home,
            settings.summary_codex_model, settings.summary_codex_timeout,
        )
    return ApiEndpointProvider(
        settings.summary_url,
        settings.summary_model,
        settings.summary_key,
    )


def _parse_summary_result(content: str) -> SummaryResult:
    """Extract a ``SummaryResult`` from a model's text response.

    The provider asks for a JSON ``{"title", "summary"}`` object, but models are
    unreliable: they may add a code fence, surrounding prose, or only return the
    summary. Parse defensively so we always get a usable result.
    """
    raw = content.strip()

    # 1. Try the whole thing as JSON first (the happy path the prompt requests).
    # 2. Fall back to the first JSON object found, in case of prose/fences.
    candidates: list[str] = [raw]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    obj_match = re.search(r"\{[^{}]*" + '"' + r"(?:title|summary)" + '"' + r"[^{}]*\}", raw, re.DOTALL)
    if obj_match:
        candidates.append(obj_match.group(0))

    title: str | None = None
    summary: str | None = None
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            title = (obj.get("title") or "").strip() or None
            summary = (obj.get("summary") or "").strip() or None
            if title is not None or summary is not None:
                break

    # Always return something. If the summary is missing, the raw text is the
    # safest fallback (the model likely returned markdown without JSON).
    if summary is None and title is None:
        summary = raw

    return SummaryResult(title=title or "", summary=summary or "")


def _parse_title(content: str) -> str:
    """Extract a short title string from a model's text response.

    The provider asks for a JSON ``{"title": ...}`` object, but defensively
    handle code fences, prose wrapping, or a bare title string.
    """
    raw = content.strip()

    candidates: list[str] = [raw]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    obj_match = re.search(r"\{[^{}]*" + '"' + "title" + '"' + r"[^{}]*\}", raw, re.DOTALL)
    if obj_match:
        candidates.append(obj_match.group(0))

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            title = (obj.get("title") or "").strip()
            if title:
                return title

    # No JSON object with a title: if the whole response is a single short line,
    # treat it as the title; otherwise give up with an empty string.
    if "\n" not in raw and raw:
        return raw
    return ""
