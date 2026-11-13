"""Pluggable pre-audio-processing framework.

Modules run *before* transcription. There are two kinds:

  * ``AudioProcessor``  — transforms the audio file on disk (e.g. trim silence,
    normalize loudness). Chained in order; each reads the current file and
    writes a new one, returning the next path.
  * ``TranscribeTuner`` — does not touch the audio; it contributes keyword
    arguments to the whisper.cpp decoder (e.g. beam size, thresholds).

Each module is independently enable/disable-able via settings. A transform that
fails falls back to its input path so a misconfigured filter never kills a job.

Add a new module by implementing one of the ABCs and registering it in
``_AUDIO_PROCESSORS`` / ``_TRANSCRIBE_TUNERS`` below.
"""
from __future__ import annotations

import abc
from pathlib import Path

from ..config import get_settings


class AudioProcessor(abc.ABC):
    """Transforms an audio file on disk. Reads ``src``, writes ``dst``."""

    name: str = "base"

    @property
    def enabled(self) -> bool:
        return True

    @abc.abstractmethod
    def process(self, src: Path, dst: Path) -> None:
        ...


class TranscribeTuner(abc.ABC):
    """Contributes kwargs to the whisper.cpp decoder."""

    name: str = "base"

    @property
    def enabled(self) -> bool:
        return True

    @abc.abstractmethod
    def options(self) -> dict:
        ...


# --- Module registry -------------------------------------------------------
# Imported lazily inside the builders to avoid import cycles with config.
def _audio_processors() -> list[AudioProcessor]:
    from .trim import TrimModule

    return [TrimModule()]


def _transcribe_tuners() -> list[TranscribeTuner]:
    from .tune import TuneModule

    return [TuneModule()]


def run_audio_transforms(path: str, settings=None) -> str:
    """Run all enabled ``AudioProcessor`` modules in sequence.

    Returns the path of the final (possibly transformed) audio. On any
    transform failure, logs a warning and falls back to the previous path so
    the job can still proceed with the unmodified audio.
    """
    import logging

    logger = logging.getLogger("smoothie.preprocess")
    if settings is None:
        settings = get_settings()

    current = Path(path)
    for module in _audio_processors():
        if not module.enabled:
            continue
        dst = current.with_name(f"{current.stem}.proc{module.name}{current.suffix}")
        try:
            module.process(current, dst)
            if dst.exists() and dst.stat().st_size > 0:
                current = dst
                logger.info("Preprocess module '%s' applied -> %s", module.name, current)
            else:
                logger.warning(
                    "Preprocess module '%s' produced no output; skipping.", module.name
                )
        except Exception as exc:  # noqa: BLE001 - fail soft, don't kill the job
            logger.warning(
                "Preprocess module '%s' failed (%s); falling back to input.",
                module.name,
                exc,
            )
    return str(current)


def collect_transcribe_options(settings=None) -> dict:
    """Merge ``options()`` from all enabled ``TranscribeTuner`` modules."""
    if settings is None:
        settings = get_settings()
    merged: dict = {}
    for tuner in _transcribe_tuners():
        if not tuner.enabled:
            continue
        merged.update(tuner.options())
    return merged
