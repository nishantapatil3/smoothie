"""Settings read/edit endpoints for the web UI Settings page.

``GET /api/settings`` returns the current (editable) settings; ``PUT
/api/settings`` applies a partial update live and persists it to
``config.yaml``. Hidden fields (``auth_secret``, ``data_dir``, ``ffmpeg_bin``)
are never returned or accepted. All routes require an authenticated user.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import get_current_user
from .config import HIDDEN_SETTINGS, Settings, apply_settings, get_settings
from .models import User

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    """All editable settings (hidden/secret fields excluded)."""

    model_config = ConfigDict(from_attributes=True)

    host: str
    port: int
    transcription_model: str
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
    job_poll_interval: float
    auth_access_ttl_minutes: int
    auth_refresh_ttl_days: int
    auth_min_password_len: int
    admin_usernames: list[str]
    trim_enabled: bool
    trim_threshold: int
    trim_min_silence: float
    tune_enabled: bool
    tune_beam_size: int | None
    tune_condition_on_previous_text: bool | None
    tune_no_speech_threshold: float | None
    tune_hallucination_silence_threshold: float | None


class SettingsUpdate(BaseModel):
    """Partial settings update; only supplied fields are changed."""

    model_config = ConfigDict(extra="ignore")

    host: str | None = None
    port: int | None = None
    transcription_model: str | None = None
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
    job_poll_interval: float | None = None
    auth_access_ttl_minutes: int | None = None
    auth_refresh_ttl_days: int | None = None
    auth_min_password_len: int | None = None
    admin_usernames: list[str] | None = None
    trim_enabled: bool | None = None
    trim_threshold: int | None = None
    trim_min_silence: float | None = None
    tune_enabled: bool | None = None
    tune_beam_size: int | None = None
    tune_condition_on_previous_text: bool | None = None
    tune_no_speech_threshold: float | None = None
    tune_hallucination_silence_threshold: float | None = None


@router.get("", response_model=SettingsOut)
async def get_settings_route(user: User = Depends(get_current_user)) -> SettingsOut:
    settings = get_settings()
    return SettingsOut.model_validate(settings.model_dump())


@router.put("", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdate,
    user: User = Depends(get_current_user),
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
        updated = apply_settings(updates)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid settings: {exc}")
    return SettingsOut.model_validate(updated.model_dump())
