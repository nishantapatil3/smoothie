"""Audio file storage on local disk."""
from __future__ import annotations

import uuid
from pathlib import Path

from .config import get_settings


def save_audio(data: bytes, content_type: str | None) -> tuple[str, str]:
    """Save audio bytes to disk. Returns (filename, abs_path)."""
    ext = _extension_for(content_type)
    filename = f"{uuid.uuid4().hex}{ext}"
    path = get_settings().audio_dir / filename
    path.write_bytes(data)
    return filename, str(path)


def audio_path(filename: str) -> Path:
    return get_settings().audio_dir / filename


def delete_audio(filename: str) -> None:
    path = audio_path(filename)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _extension_for(content_type: str | None) -> str:
    if not content_type:
        return ".webm"
    ct = content_type.lower()
    return {
        "audio/webm": ".webm",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/wave": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/ogg": ".ogg",
        "audio/flac": ".flac",
    }.get(ct, ".webm")
