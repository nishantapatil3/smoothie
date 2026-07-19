"""Audio file storage on local disk."""
from __future__ import annotations

import shutil
import subprocess
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


def audio_duration_sec(path: str | Path) -> float | None:
    """Return audio duration in seconds via ffprobe, else None.

    Used to reject uploads longer than a user's remaining transcription budget
    before they are queued. Returns ``None`` when ffmpeg/ffprobe is missing or
    the probe fails, so callers can treat an unknown duration as "don't block"
    rather than rejecting every upload.
    """
    ffprobe = get_settings().ffmpeg_bin or shutil.which("ffprobe")
    ffprobe = ffprobe.replace("ffmpeg", "ffprobe") if ffprobe else None
    if not ffprobe:
        # Fall back to deriving ffprobe from an ffmpeg path, else give up.
        ffmpeg = get_settings().ffmpeg_bin or shutil.which("ffmpeg")
        if ffmpeg:
            ffprobe = str(ffmpeg).replace("ffmpeg", "ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


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
