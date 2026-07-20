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
import logging
import random

import httpx

from .config import get_settings
from .prompts import SUMMARY_SYSTEM_PROMPT, build_summary_prompt

logger = logging.getLogger("smoothie.summarize")


class SummaryError(RuntimeError):
    pass


class SummaryProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def summarize(self, transcript: str) -> str:
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

    async def summarize(self, transcript: str) -> str:
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
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise SummaryError(f"Unexpected summary endpoint response: {data!r}") from exc


def get_provider() -> SummaryProvider:
    settings = get_settings()
    return ApiEndpointProvider(
        settings.summary_url,
        settings.summary_model,
        settings.summary_key,
    )
