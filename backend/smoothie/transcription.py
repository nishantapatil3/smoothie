"""MLX Whisper transcription wrapper.

The model is loaded once into MLX memory (warmed at backend startup via
``warm_model``) and reused across requests through MLX Whisper's internal model
cache. On Apple Silicon this runs on the GPU via MLX for fast local
transcription.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import mlx.core as mx
import mlx_whisper
import mlx_whisper.load_models as _load_models
import tqdm
from huggingface_hub import snapshot_download

from .config import get_settings
from .preprocess import collect_transcribe_options, run_audio_transforms

logger = logging.getLogger("smoothie.transcription")


class TranscriptionError(RuntimeError):
    pass


@dataclass
class TranscriptionResult:
    text: str
    segments: list[dict]  # [{start, end, text}, ...]
    # Final transcription progress (0.0-1.0) captured just before the live
    # progress counter is reset.
    progress: float = 0.0


# Whisper is far more reliable on bounded audio windows than on a single very
# long clip. Passing a whole multi-minute recording to ``mlx_whisper.transcribe``
# in one call causes the large-v3 model to stop decoding partway through
# (empirically ~100s in), silently truncating the transcript. Slicing long audio
# into fixed windows and transcribing each independently avoids that and matches
# how Whisper is intended to handle long input.
_CHUNK_SECONDS = 30
_CHUNK_OVERLAP_SECONDS = 1

# Live transcription progress (0.0-1.0), captured from mlx_whisper's internal
# frame progress bar. Single-worker app, so a module-level value is safe.
_progress: dict = {"n": 0, "total": 0}


def get_progress() -> float:
    """Return current transcription progress as a fraction (0.0-1.0)."""
    total = _progress["total"]
    if not total:
        return 0.0
    return min(1.0, _progress["n"] / total)


def _install_progress_hook() -> None:
    """Monkeypatch tqdm so each frame update records live progress.

    mlx_whisper already iterates its decoder over audio frames with a tqdm
    bar (when verbose is not True). By spying on ``update`` we capture the
    real n/total without changing transcription speed or accuracy. The
    original is restored by the caller.
    """
    orig_update = tqdm.tqdm.update

    def _spy(self, n=1):
        _progress["n"] = self.n
        _progress["total"] = self.total
        return orig_update(self, n)

    tqdm.tqdm.update = _spy  # type: ignore[assignment]


def _model_id() -> str:
    """Return the configured model id/path."""
    return get_settings().transcription_model


# The model id that was successfully warmed into MLX memory at startup. Used by
# ``transcribe`` to confirm the preloaded model matches the one it's about to
# use, so it reuses the cache instead of reloading.
_warmed_id: str | None = None


def _resolve_source(model_id: str) -> str:
    """Classify the model spec as a local path or a Hugging Face repo id."""
    if Path(model_id).expanduser().exists():
        return "local"
    return "huggingface"


def warm_model() -> None:
    """Load the Whisper model into MLX memory at backend startup.

    Downloads (first time) and loads the weights so the first transcription
    request doesn't pay that cost. Failures are logged but non-fatal: if the
    model can't be loaded now (e.g. no network), the first request will attempt
    it. The loaded model populates MLX Whisper's internal cache keyed by model
    id, which ``transcribe`` reuses via the id string.
    """
    model_id = _model_id()
    source = _resolve_source(model_id)

    if source == "local":
        logger.info("Loading Whisper model from local path: %s", model_id)
    elif _hf_cache_has(model_id):
        logger.info("Loading Whisper model '%s' from Hugging Face cache.", model_id)
    else:
        logger.info(
            "Downloading Whisper model '%s' from Hugging Face...", model_id
        )

    start = time.monotonic()
    try:
        _load_models.load_model(model_id, dtype=mx.float16)
        elapsed = time.monotonic() - start
        logger.info(
            "Whisper model '%s' loaded into MLX memory in %.1fs.", model_id, elapsed
        )
        global _warmed_id
        _warmed_id = model_id
    except Exception as exc:  # noqa: BLE001 - don't crash startup on load failure
        logger.warning(
            "Could not preload Whisper model '%s' at startup (%s). It will be "
            "loaded on the first transcription request.",
            model_id,
            exc,
        )


def _hf_cache_has(model_id: str) -> bool:
    """Return True if the model already exists in the local HF cache."""
    try:
        snapshot_download(model_id, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001 - not cached
        return False


def transcribe(path: str, language: str | None = None) -> TranscriptionResult:
    """Transcribe an audio file at ``path`` and return text + timed segments.

    Runs the pluggable pre-audio-processing modules first (e.g. silence trim),
    then transcribes the (possibly transformed) audio with any tuned Whisper
    options. Temporary transformed files are cleaned up afterwards.
    """
    settings = get_settings()
    lang = language or settings.transcription_language

    model_id = _model_id()
    if _warmed_id == model_id:
        logger.info("Using preloaded Whisper model '%s'.", model_id)
    else:
        # Either warm_model() didn't run/succeed, or the configured model
        # changed; mlx_whisper will load (and cache) it on this call.
        logger.info("Loading Whisper model '%s' for this request.", model_id)

    processed_path = run_audio_transforms(path, settings)
    # Capture live progress from mlx_whisper's frame bar during this call.
    _progress["n"] = 0
    _progress["total"] = 0
    orig_update = tqdm.tqdm.update
    _install_progress_hook()
    try:
        audio = _load_waveform(processed_path)
        if _needs_chunking(audio):
            logger.info(
                "Audio is %.1fs; transcribing in %ds windows to avoid "
                "single-pass truncation.",
                len(audio) / 16000.0,
                _CHUNK_SECONDS,
            )
            result = _transcribe_chunked(
                audio, model_id, lang, collect_transcribe_options(settings)
            )
        else:
            result = mlx_whisper.transcribe(
                processed_path,
                path_or_hf_repo=model_id,
                language=lang,
                word_timestamps=False,
                verbose=False,
                **collect_transcribe_options(settings),
            )
    finally:
        # Capture the final progress before restoring/resetting, so the caller
        # can record it.
        final_progress = get_progress()
        # Restore the original tqdm and clear progress so a stale value isn't
        # reported after transcription finishes.
        tqdm.tqdm.update = orig_update  # type: ignore[assignment]
        _progress["n"] = 0
        _progress["total"] = 0
        # Remove any temp file produced by the preprocessing chain, but never
        # the original uploaded audio.
        if processed_path != path and os.path.exists(processed_path):
            try:
                os.unlink(processed_path)
            except OSError:
                pass

    segments = [
        {
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": seg.get("text", "").strip(),
        }
        for seg in result.get("segments", [])
    ]

    return TranscriptionResult(
        text=result.get("text", "").strip(),
        segments=segments,
        progress=final_progress,
    )


def _load_waveform(path: str) -> "mx.array":
    """Decode an audio file to a mono float32 waveform using mlx_whisper's loader.

    Returns an MLX array (samples at 16 kHz). Raises on decode failure so the
    caller's existing error handling applies.
    """
    return mlx_whisper.audio.load_audio(path)


def _needs_chunking(audio: "mx.array") -> bool:
    """True when the audio is long enough that single-pass decode may truncate."""
    return (audio.shape[0] / 16000.0) > _CHUNK_SECONDS


def _transcribe_chunked(
    audio: "mx.array", model_id: str, lang: str | None, options: dict
) -> dict:
    """Transcribe long audio by decoding fixed windows and merging the results.

    Each window is ``_CHUNK_SECONDS`` of audio with ``_CHUNK_OVERLAP_SECONDS``
    of overlap on the leading edge (so a word cut at a boundary is recovered).
    ``condition_on_previous_text`` is disabled per chunk: cross-chunk context is
    what makes large models loop/hallucinate and bail early on long input, and we
    re-assemble the full transcript ourselves. Segment timestamps are shifted by
    each window's start offset so the merged timeline stays continuous.
    """
    sr = 16000
    chunk_samples = _CHUNK_SECONDS * sr
    overlap_samples = _CHUNK_OVERLAP_SECONDS * sr
    step = chunk_samples - overlap_samples

    total = int(audio.shape[0])
    # Windows cover [0, chunk), [step, step+chunk), ...; the last window reaches
    # the end of the audio.
    starts = list(range(0, max(1, total), step))
    if starts[-1] + chunk_samples < total:
        starts.append(max(0, total - chunk_samples))

    all_text: list[str] = []
    all_segments: list[dict] = []
    overlap_skip = float(_CHUNK_OVERLAP_SECONDS)

    for start in starts:
        window = audio[start : start + chunk_samples]
        if window.shape[0] < sr:  # ignore sub-second tails
            continue
        chunk_result = mlx_whisper.transcribe(
            window,
            path_or_hf_repo=model_id,
            language=lang,
            word_timestamps=False,
            verbose=False,
            condition_on_previous_text=False,
            **options,
        )
        offset = start / sr
        window_segments = [
            s for s in chunk_result.get("segments", []) if s.get("end", 0.0) > overlap_skip
        ]
        for seg in window_segments:
            all_segments.append(
                {
                    "start": float(seg.get("start", 0.0)) + offset,
                    "end": float(seg.get("end", 0.0)) + offset,
                    "text": seg.get("text", "").strip(),
                }
            )
        text = chunk_result.get("text", "").strip()
        if text:
            all_text.append(text)

    return {
        "text": " ".join(all_text),
        "segments": all_segments,
        "language": lang or "en",
    }
