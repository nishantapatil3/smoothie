"""Application configuration loaded from ``config.yaml``.

``config.yaml`` (in the backend root) is the single source of truth. If it is
missing, built-in defaults are used and the file is written on first save. No
environment variables or ``.env`` files are consulted. The running server's
settings can be updated at runtime via :func:`apply_settings` (persisted back to
``config.yaml``), so the web UI's Settings page can edit them live.
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger("smoothie")

# Lock to protect concurrent settings mutations (e.g., simultaneous PUT /api/settings).
_settings_lock = asyncio.Lock()

# Project root: backend/ (this file lives in backend/smoothie/)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
DATA_DIR = BACKEND_ROOT / "data"

# config.yaml lives in the backend root. The summary_key secret may
# be written here, so it should be git-ignored.
CONFIG_PATH = BACKEND_ROOT / "config.yaml"

# Settings the web UI is not allowed to read or write (secret / DB-stability).
HIDDEN_SETTINGS = {"data_dir", "ffmpeg_bin", "watch_dir", "output_dir", "codex_bin", "codex_home"}


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 5173

    # --- Storage ---
    # Directory where the sqlite db and uploaded audio live. Not user-editable
    # via the UI (changing it mid-run would orphan the live database).
    data_dir: Path = DATA_DIR

    @field_validator("data_dir", mode="before")
    @classmethod
    def resolve_data_dir(cls, value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else BACKEND_ROOT / path).resolve()

    # Paths are resolved relative to the repository, independent of the cwd.
    watch_dir: Path = REPO_ROOT / "watch"
    output_dir: Path = REPO_ROOT / "output"
    watch_enabled: bool = True
    watch_poll_interval: float = Field(default=2.0, ge=0.1)
    watch_stable_seconds: float = Field(default=5.0, ge=0)

    @field_validator("watch_dir", "output_dir", mode="before")
    @classmethod
    def resolve_folder(cls, value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else REPO_ROOT / path).resolve()

    @model_validator(mode="after")
    def check_folders(self):
        if self.watch_dir == self.output_dir or self.watch_dir.is_relative_to(self.output_dir):
            raise ValueError("watch_dir must be outside output_dir")
        return self

    # --- Transcription provider (whisper.cpp) ---
    # Model name from ggerganov/whisper.cpp (e.g. tiny, large-v3, small-q8_0),
    # or a local ggml .bin model file. MLX weights cannot be reused.
    transcription_model: str = "large-v3"
    # Auto lets whisper.cpp use an available compiled GPU backend or CPU.
    transcription_device: Literal["auto", "cpu"] = "auto"
    transcription_gpu_device: int = Field(default=0, ge=0)
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
    # Use the local Codex CLI's ChatGPT sign-in, or an API endpoint.
    summary_provider: Literal["api", "codex"] = "api"
    summary_codex_model: str = ""  # empty = installed Codex's default model
    summary_codex_timeout: float = Field(default=300.0, gt=0)
    codex_bin: str = ""  # auto-detected on PATH if empty
    codex_home: Path = Path.home() / ".codex"

    # --- Worker ---
    # Polling interval (seconds) for the background job loop.
    job_poll_interval: float = 2.0

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
    tune_beam_size: int | None = Field(default=None, ge=1)
    tune_condition_on_previous_text: bool | None = None
    tune_no_speech_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator("transcription_model")
    @classmethod
    def migrate_transcription_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("transcription_model cannot be empty")
        # Preserve the model size selected in configurations from the MLX era.
        prefix = "mlx-community/whisper-"
        if value.startswith(prefix):
            name = value[len(prefix):].removesuffix("-mlx")
            if name in {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}:
                return name
        return value

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
        logger.info("Config file not found at %s, using defaults", CONFIG_PATH)
        return Settings()
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            logger.warning("config.yaml is not a mapping; using defaults.")
            return Settings()
        # Hidden paths are configurable in YAML, but never through the API.
        settings = Settings(**data)
        logger.info("Loaded settings from %s", CONFIG_PATH)
        return settings
    except Exception as exc:  # noqa: BLE001 - never let a bad file crash startup
        logger.warning("Failed to read config.yaml (%s); using defaults.", exc)
        return Settings()


def save_config(settings: Settings) -> None:
    """Persist settings, including configured paths, to YAML."""
    payload = settings.model_dump(mode="json")
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)
    logger.info("Saved settings to %s", CONFIG_PATH)


@lru_cache
def get_settings() -> Settings:
    return load_config()


async def apply_settings(updates: dict[str, Any]) -> Settings:
    """Update the cached settings in place, persist to ``config.yaml``, return it.

    ``updates`` is a partial dict of editable fields. Hidden fields
    (``data_dir``, ``ffmpeg_bin``) and any unknown keys are
    ignored. Values are validated/coerced by the ``Settings`` model so e.g. a
    JSON boolean becomes a real ``bool``. Raises ``ValueError`` on invalid data.

    This function is async and uses a lock to prevent concurrent mutations.
    """
    async with _settings_lock:
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
        logger.info("Applied settings updates: %s", list(updates.keys()))
        return settings
