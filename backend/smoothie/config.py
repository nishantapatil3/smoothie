"""Application configuration loaded from ``config.yaml``.

``config.yaml`` (in the backend root) is the single source of truth. If it is
missing, built-in defaults are used and the file is written on first save. No
environment variables or ``.env`` files are consulted. The running server's
settings can be updated at runtime via :func:`apply_settings` (persisted back to
``config.yaml``), so the web UI's Settings page can edit them live.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("smoothie")

# Project root: backend/ (this file lives in backend/smoothie/)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_ROOT / "data"

# config.yaml lives in the backend root. Secrets (summary_key, auth_secret) may
# be written here, so it should be git-ignored.
CONFIG_PATH = BACKEND_ROOT / "config.yaml"

# Settings the web UI is not allowed to read or write (secret / DB-stability).
HIDDEN_SETTINGS = {"auth_secret", "data_dir", "ffmpeg_bin"}


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 5173

    # --- Storage ---
    # Directory where the sqlite db and uploaded audio live. Not user-editable
    # via the UI (changing it mid-run would orphan the live database).
    data_dir: Path = DATA_DIR

    # --- Transcription provider (MLX Whisper) ---
    # Any mlx-community whisper model id, or a local path.
    # Common ids: mlx-community/whisper-tiny (fast), whisper-small,
    # whisper-medium, whisper-large-v3. Verify availability on HuggingFace.
    transcription_model: str = "mlx-community/whisper-large-v3-mlx"
    # Spoken language for transcription. Defaults to "en" (forced, like meetily)
    # for stable, higher-quality results on English meetings. Set to None for
    # auto-detection, or any language code (e.g. "es", "fr").
    transcription_language: str | None = "en"

    # --- VAD-based chunking (long audio) ---
    # When enabled (and silero-vad is installed), long audio is split at natural
    # speech pauses instead of fixed 30s windows, matching meetily's chunking:
    # one Whisper call per speech-aligned segment, continuous timeline, no
    # overlap. Falls back to fixed-window chunking if VAD is unavailable.
    vad_enabled: bool = True
    vad_threshold: float = 0.5          # Silero speech probability threshold
    vad_min_speech_ms: int = 250        # ignore sounds shorter than this
    vad_min_silence_ms: int = 400       # pause length that ends a chunk run
    vad_speech_pad_ms: int = 200        # pad each run so onsets/tails aren't cut
    vad_chunk_target_sec: int = 24      # target merged chunk length (~meetily 22-27s)
    vad_chunk_max_sec: int = 30         # hard cap on a single chunk

    # --- Summary provider (generic OpenAI-compatible API endpoint) ---
    # The backend talks to a single /chat/completions endpoint. Point it at any
    # summarizer LLM — a local server (e.g. http://localhost:11434/v1), a remote
    # OpenAI-compatible provider, vLLM, LM Studio, etc. It's up to the user where
    # it goes.
    summary_url: str = "http://localhost:11434/v1"
    # Optional bearer token; left empty for local/keyless endpoints.
    summary_key: str = ""
    summary_model: str = "llama3.2"

    # --- Worker ---
    # Polling interval (seconds) for the background job loop.
    job_poll_interval: float = 2.0

    # --- Auth ---
    # Secret used to sign stateless Bearer access tokens (HMAC-SHA256). Generate
    # a strong random value and keep it stable across restarts, or existing
    # tokens become invalid on every boot. If left empty, an ephemeral secret is
    # generated at startup (tokens won't survive a restart). Not exposed via the
    # UI for safety — set it in config.yaml directly.
    auth_secret: str = ""
    # Access token lifetime in minutes (short-lived; refreshed silently).
    auth_access_ttl_minutes: int = 60
    # Refresh token lifetime in days (long-lived; rotated on each use).
    auth_refresh_ttl_days: int = 7
    # Minimum accepted password length at registration.
    auth_min_password_len: int = 8

    # --- Tier administration ---
    # Usernames allowed to list users and change their subscription tier via
    # the admin endpoints. Empty = no one can change tiers through the UI/API
    # (free-tier limits still apply to everyone). Add usernames here to grant
    # admin rights, e.g. ["admin"].
    admin_usernames: list[str] = Field(default_factory=list)

    # --- Pre-audio-processing (pluggable modules) ---
    # Modules run before transcription. Each is independently enable/disable-able.
    # See smoothie/preprocess/ for the module implementations.
    ffmpeg_bin: str = ""  # auto-detected via shutil.which if empty

    # trim: remove silence with ffmpeg silenceremove. Disabled because it
    # strips internal silence too, collapsing real duration (e.g. 6 min -> 99s)
    # and corrupting duration_sec + segment timestamps.
    trim_enabled: bool = False
    # Noise floor (dB) below which audio is treated as silence, e.g. -40.
    trim_threshold: int = -40
    # Minimum silent stretch (seconds) to cut, e.g. 0.5.
    trim_min_silence: float = 0.5

    # tune: surface Whisper decode options. None = leave Whisper default
    # (safe/non-mutating). Set to override.
    tune_enabled: bool = True
    tune_beam_size: int | None = None
    tune_condition_on_previous_text: bool | None = None
    tune_no_speech_threshold: float | None = None
    tune_hallucination_silence_threshold: float | None = None

    @property
    def db_url(self) -> str:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.data_dir / 'smoothie.db'}"

    @property
    def audio_dir(self) -> Path:
        path = self.data_dir / "audio"
        path.mkdir(parents=True, exist_ok=True)
        return path


def load_config() -> Settings:
    """Load settings from ``config.yaml``; fall back to defaults if absent."""
    if not CONFIG_PATH.exists():
        return Settings()
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            logger.warning("config.yaml is not a mapping; using defaults.")
            return Settings()
        # Drop keys the UI must not inject; pydantic extra="ignore" also covers
        # unknown keys defensively.
        data = {k: v for k, v in data.items() if k not in HIDDEN_SETTINGS}
        return Settings(**data)
    except Exception as exc:  # noqa: BLE001 - never let a bad file crash startup
        logger.warning("Failed to read config.yaml (%s); using defaults.", exc)
        return Settings()


def save_config(settings: Settings) -> None:
    """Persist ``settings`` to ``config.yaml`` (hidden fields excluded)."""
    payload = settings.model_dump()
    payload = {k: v for k, v in payload.items() if k not in HIDDEN_SETTINGS}
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)


@lru_cache
def get_settings() -> Settings:
    return load_config()


def apply_settings(updates: dict[str, Any]) -> Settings:
    """Update the cached settings in place, persist to ``config.yaml``, return it.

    ``updates`` is a partial dict of editable fields. Hidden fields
    (``auth_secret``, ``data_dir``, ``ffmpeg_bin``) and any unknown keys are
    ignored. Values are validated/coerced by the ``Settings`` model so e.g. a
    JSON boolean becomes a real ``bool``. Raises ``ValueError`` on invalid data.
    """
    settings = get_settings()
    current = settings.model_dump()
    for key, value in updates.items():
        if key in HIDDEN_SETTINGS or key not in current:
            continue
        current[key] = value
    # Re-validate the merged result (coerces types, rejects bad ones).
    updated = Settings(**current)
    # Mutate the cached singleton in place so existing get_settings() callers
    # see the new values immediately.
    for key, value in updated.model_dump().items():
        setattr(settings, key, value)
    save_config(settings)
    return settings
