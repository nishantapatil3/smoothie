"""Settings read/edit endpoints for the web UI Settings page.

``GET /api/settings`` returns the current (editable) settings; ``PUT
/api/settings`` applies a partial update live and persists it to
``config.yaml``. Hidden fields (``data_dir``, ``ffmpeg_bin``)
are never returned or accepted. Settings are shared by this deployment.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .config import HIDDEN_SETTINGS, apply_settings, get_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    """All editable settings (hidden/secret fields excluded)."""

    model_config = ConfigDict(from_attributes=True)

    host: str
    port: int
    transcription_model: str
    transcription_device: Literal["auto", "cpu"]
    transcription_gpu_device: int
    transcription_language: str | None
    vad_enabled: bool
    vad_threshold: float
    vad_min_speech_ms: int
    vad_min_silence_ms: int
    vad_speech_pad_ms: int
    vad_chunk_target_sec: int
    vad_chunk_max_sec: int
    summary_url: str
    summary_key: str
    summary_model: str
    summary_provider: Literal["api", "codex"]
    summary_codex_model: str
    summary_codex_timeout: float
    job_poll_interval: float
    watch_enabled: bool
    watch_poll_interval: float
    watch_stable_seconds: float
    trim_enabled: bool
    trim_threshold: int
    trim_min_silence: float
    tune_enabled: bool
    tune_beam_size: int | None
    tune_condition_on_previous_text: bool | None
    tune_no_speech_threshold: float | None


class SettingsUpdate(BaseModel):
    """Partial settings update; only supplied fields are changed."""

    model_config = ConfigDict(extra="ignore")

    host: str | None = None
    port: int | None = None
    transcription_model: str | None = None
    transcription_device: Literal["auto", "cpu"] | None = None
    transcription_gpu_device: int | None = Field(default=None, ge=0)
    transcription_language: str | None = None
    vad_enabled: bool | None = None
    vad_threshold: float | None = None
    vad_min_speech_ms: int | None = None
    vad_min_silence_ms: int | None = None
    vad_speech_pad_ms: int | None = None
    vad_chunk_target_sec: int | None = None
    vad_chunk_max_sec: int | None = None
    summary_url: str | None = None
    summary_key: str | None = None
    summary_model: str | None = None
    summary_provider: Literal["api", "codex"] | None = None
    summary_codex_model: str | None = None
    summary_codex_timeout: float | None = Field(default=None, gt=0)
    job_poll_interval: float | None = None
    watch_enabled: bool | None = None
    watch_poll_interval: float | None = Field(default=None, ge=0.1)
    watch_stable_seconds: float | None = Field(default=None, ge=0)
    trim_enabled: bool | None = None
    trim_threshold: int | None = None
    trim_min_silence: float | None = None
    tune_enabled: bool | None = None
    tune_beam_size: int | None = None
    tune_condition_on_previous_text: bool | None = None
    tune_no_speech_threshold: float | None = None


@router.get("", response_model=SettingsOut)
async def get_settings_route() -> SettingsOut:
    settings = get_settings()
    return SettingsOut.model_validate(settings.model_dump())


@router.put("", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdate,
) -> SettingsOut:
    updates: dict[str, Any] = {
        k: v for k, v in body.model_dump(exclude_unset=True).items()
    }
    # Defense in depth: never let hidden fields through, even if a future
    # schema change accidentally exposes one.
    updates = {k: v for k, v in updates.items() if k not in HIDDEN_SETTINGS}
    if not updates:
        return SettingsOut.model_validate(get_settings().model_dump())
    try:
        updated = await apply_settings(updates)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid settings: {exc}")
    return SettingsOut.model_validate(updated.model_dump())
