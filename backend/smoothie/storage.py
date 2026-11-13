"""Managed audio copies and per-recording output directories."""
from __future__ import annotations

import mimetypes
import os
import re
import uuid
from pathlib import Path

from .config import get_settings
from .logging_config import get_logger

logger = get_logger(__name__)

# Common audio formats and containers from which ffmpeg can extract audio.
AUDIO_EXTENSIONS = frozenset({
    ".3g2", ".3gp", ".aac", ".ac3", ".aif", ".aifc", ".aiff", ".alac",
    ".amr", ".ape", ".au", ".caf", ".dts", ".flac", ".m4a", ".m4b",
    ".mka", ".mp2", ".mp3", ".mp4", ".oga", ".ogg", ".opus", ".ra",
    ".snd", ".wav", ".wave", ".webm", ".wma", ".wv",
})


def supported_audio(path: Path) -> bool:
    kind = mimetypes.guess_type(path.name)[0] or ""
    return path.suffix.lower() in AUDIO_EXTENSIONS or kind.startswith("audio/")


def job_output_dir(recording_id: str, original_name: str) -> Path:
    stem = re.sub(r"[^\w.-]+", "-", Path(original_name).stem).strip(".-")[:64] or "recording"
    return get_settings().output_dir / f"{stem}-{recording_id}"


def audio_filename(original_name: str | None, content_type: str | None) -> str:
    suffix = Path(original_name or "").suffix.lower()
    if original_name and supported_audio(Path(original_name)):
        return f"audio{suffix}"
    return f"audio{_extension_for(content_type)}"


def atomic_write(path: Path, data: bytes) -> None:
    """Publish complete artifacts; a crash must never leave a half-written final file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_audio(
    data: bytes, content_type: str | None, original_name: str | None = None,
    recording_id: str | None = None,
) -> tuple[str, str]:
    """Store uploads beside their generated files, leaving legacy reads supported."""
    recording_id = recording_id or uuid.uuid4().hex
    directory = job_output_dir(recording_id, original_name or "recording")
    filename = audio_filename(original_name, content_type)
    path = directory / filename
    atomic_write(path, data)
    return filename, str(path)


def audio_path(filename: str, output_path: str | None = None) -> Path:
    return (Path(output_path) if output_path else get_settings().audio_dir) / filename


def delete_audio(filename: str, output_path: str | None = None) -> None:
    """Delete managed copies/artifacts, never files from the watched folder."""
    path = audio_path(filename, output_path)
    path.unlink(missing_ok=True)
    if output_path:
        directory = Path(output_path)
        for name in ("transcript.txt", "transcript.json", "summary.md", "metadata.json"):
            (directory / name).unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass  # Preserve any extra files the user put in the output directory.


def _extension_for(content_type: str | None) -> str:
    return {
        "audio/webm": ".webm", "audio/wav": ".wav", "audio/x-wav": ".wav",
        "audio/wave": ".wav", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
        "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/ogg": ".ogg",
        "audio/flac": ".flac", "audio/aac": ".aac", "audio/aiff": ".aiff",
        "audio/opus": ".opus", "audio/x-ms-wma": ".wma",
    }.get((content_type or "").split(";", 1)[0], ".webm")
