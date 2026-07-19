"""Application configuration loaded from environment variables.

All settings can be overridden with environment variables or a `.env` file.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: backend/ (this file lives in backend/smoothie/)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SMOOTHIE_", env_file=str(BACKEND_ROOT / ".env"), extra="ignore"
    )

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 5173

    # --- Storage ---
    # Directory where the sqlite db and uploaded audio live.
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

    # --- Pre-audio-processing (pluggable modules) ---
    # Modules run before transcription. Each is independently enable/disable-able.
    # See smoothie/preprocess/ for the module implementations.
    ffmpeg_bin: str = ""  # auto-detected via shutil.which if empty

    # trim: remove silence with ffmpeg silenceremove (conservative default).
    trim_enabled: bool = True
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
