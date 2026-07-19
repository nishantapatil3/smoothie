"""Pluggable summary provider.

A single generic provider talks to any OpenAI-compatible ``/chat/completions``
endpoint. It is up to the user where that endpoint points — a local summarizer
LLM (``http://localhost:11434/v1``), a remote OpenAI-compatible provider, vLLM,
LM Studio, etc. The backend only needs a URL, an optional API key, and a model
name.
"""
from __future__ import annotations

import abc

import httpx

from .config import get_settings
from .prompts import SUMMARY_SYSTEM_PROMPT, build_summary_prompt


class SummaryError(RuntimeError):
    pass


class SummaryProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def summarize(self, transcript: str) -> str:
        ...


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

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise SummaryError(
                f"Could not reach summarizer LLM at {url}. Is it running?"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise SummaryError(f"Summarizer LLM returned error: {exc}") from exc

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
