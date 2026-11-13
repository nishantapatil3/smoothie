"""Local whisper.cpp transcription with a cached, serialized model context.

GPU discovery and CPU fallback are handled by whisper.cpp using the backends
compiled into pywhispercpp. Decode calls share native state, so warming,
reloading, and transcription all run under the same lock.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

import numpy as np
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import HFValidationError
from huggingface_hub.utils import RepositoryNotFoundError

from .config import get_settings
from .preprocess import collect_transcribe_options, run_audio_transforms
from . import vad as _vad
from .logging_config import get_logger

logger = get_logger(__name__)


class TranscriptionError(RuntimeError):
    pass


@dataclass
class TranscriptionResult:
    text: str
    segments: list[dict]  # [{start, end, text}, ...]
    # Completed transcription progress (0.0-1.0).
    progress: float = 0.0


# Keep bounded windows for predictable progress and long-recording coverage.
_CHUNK_SECONDS = 30
_CHUNK_OVERLAP_SECONDS = 1
_MODEL_REPO = "ggerganov/whisper.cpp"
_model_lock = RLock()
_model = None
_model_key: tuple | None = None


@atexit.register
def release_model() -> None:
    """Free native weights before Python tears down the binding module."""
    global _model, _model_key
    with _model_lock:
        _model = None
        _model_key = None


def _model_id() -> str:
    return get_settings().transcription_model


def _download_model(model_id: str) -> str:
    """Resolve a named ggml model or an existing local .bin file."""
    local = Path(model_id).expanduser()
    if local.is_file():
        return str(local.resolve())
    if local.exists():
        raise TranscriptionError("Choose a whisper.cpp .bin model file, not a directory.")

    from pywhispercpp.constants import AVAILABLE_MODELS

    if model_id not in AVAILABLE_MODELS:
        raise TranscriptionError(
            f"Unknown whisper.cpp model '{model_id}'. Choose a model name such as "
            "tiny, small, large-v3, or a local ggml .bin file. MLX weights are not compatible."
        )
    try:
        return hf_hub_download(repo_id=_MODEL_REPO, filename=f"ggml-{model_id}.bin")
    except (RepositoryNotFoundError, HFValidationError) as exc:
        raise TranscriptionError(f"Whisper model '{model_id}' was not found.") from exc
    except Exception as exc:
        raise TranscriptionError(f"Failed to download Whisper model '{model_id}': {exc}") from exc


def _load_model_safe(model_id: str):
    """Reuse a context until model, device, or decoder strategy changes.

    Caller must hold _model_lock. Sampling strategy is a constructor option in
    pywhispercpp; other decoding settings are reset on every transcribe call.
    """
    global _model, _model_key
    settings = get_settings()
    beam = settings.tune_enabled and settings.tune_beam_size is not None
    key = (model_id, settings.transcription_device, settings.transcription_gpu_device, beam)
    if _model is not None and _model_key == key:
        return _model
    try:
        from .whisper_cpp import CheckedModel

        model_path = _download_model(model_id)
        # Release old weights before allocating a replacement (important on
        # machines that only have enough memory for one large model).
        _model = None
        _model_key = None
        use_gpu = settings.transcription_device == "auto"
        kwargs = dict(
            params_sampling_strategy=int(beam),
            context_params={"use_gpu": use_gpu, "gpu_device": settings.transcription_gpu_device},
            print_progress=False, print_realtime=False, print_timestamps=False,
        )
        try:
            model = CheckedModel(model_path, **kwargs)
        except Exception:
            if not use_gpu:
                raise
            logger.warning("GPU model initialization failed; retrying on CPU.", exc_info=True)
            kwargs["context_params"] = {"use_gpu": False}
            model = CheckedModel(model_path, **kwargs)
        _model, _model_key = model, key
        logger.info(
            "Loaded whisper.cpp model '%s' (device=%s, GPU index=%d). "
            "Native whisper.cpp logs identify the selected backend; auto falls back to CPU.",
            model_id, settings.transcription_device, settings.transcription_gpu_device,
        )
        return model
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError(f"Failed to load Whisper model '{model_id}': {exc}") from exc


def warm_model() -> None:
    """Download and load weights at startup; defer failures to the first job."""
    started = time.monotonic()
    try:
        with _model_lock:
            _load_model_safe(_model_id())
        logger.info("Whisper model ready in %.1fs.", time.monotonic() - started)
    except Exception as exc:
        logger.warning("Could not preload Whisper model (%s); the first job will retry.", exc)


def _decode(audio: np.ndarray, model_id: str, lang: str | None, options: dict) -> dict:
    """Adapt whisper.cpp's 10 ms segment ticks to our seconds-based API."""
    with _model_lock:
        model = _load_model_safe(model_id)
        try:
            segments = model.transcribe(
                np.ascontiguousarray(audio, dtype=np.float32),
                language=lang or "auto",
                # The binding retains decode overrides between calls. Reset
                # defaults so toggling tuning never leaks the previous options.
                **{
                    "no_context": True, "no_speech_thold": 0.6,
                    "greedy": {"best_of": 5},
                    "beam_search": {"beam_size": -1, "patience": -1.0},
                    "suppress_nst": False, **options,
                },
            )
        except Exception as exc:
            raise TranscriptionError(f"Whisper transcription failed: {exc}") from exc
        normalized = [
            {"start": float(s.t0) / 100, "end": float(s.t1) / 100, "text": s.text.strip()}
            for s in segments
        ]
        return {"text": " ".join(s["text"] for s in normalized if s["text"]), "segments": normalized}


def _transcribe_locked(path: str, language: str | None = None, progress_callback=None) -> TranscriptionResult:
    """Transcribe an audio file at ``path`` and return text + timed segments.

    Runs the pluggable pre-audio-processing modules first (e.g. silence trim),
    then transcribes the (possibly transformed) audio with any tuned Whisper
    options. Temporary transformed files are cleaned up afterwards.

    Args:
        path: Path to the audio file.
        language: Optional language code (e.g., "en"). Defaults to settings.
        progress_callback: Optional callable(fraction) invoked
            after each chunk is transcribed. Used to report progress for long audio.
    """
    settings = get_settings()
    lang = language or settings.transcription_language

    model_id = _model_id()
    _load_model_safe(model_id)

    processed_path = run_audio_transforms(path, settings)
    try:
        audio = _load_waveform(processed_path)
        if _needs_chunking(audio):
            # Prefer speech-aligned (VAD) chunking when enabled and available:
            # it splits at natural pauses like meetily, rather than fixed 30s
            # windows that can cut a sentence in half.
            vad_chunks = None
            if settings.vad_enabled and _vad.vad_available():
                vad_chunks = _plan_vad_chunks(audio, settings)
            if vad_chunks:
                logger.info(
                    "Audio is %.1fs; transcribing %d VAD speech-aligned chunk(s) "
                    "(~%ds target) to avoid single-pass truncation.",
                    len(audio) / 16000.0,
                    len(vad_chunks),
                    settings.vad_chunk_target_sec,
                )
                result = _transcribe_vad_chunks(
                    audio, vad_chunks, model_id, lang,
                    collect_transcribe_options(settings),
                    progress_callback,
                )
            else:
                logger.info(
                    "Audio is %.1fs; transcribing in %ds windows to avoid "
                    "single-pass truncation.",
                    len(audio) / 16000.0,
                    _CHUNK_SECONDS,
                )
                result = _transcribe_chunked(
                    audio, model_id, lang, collect_transcribe_options(settings),
                    progress_callback,
                )
        else:
            # Single-pass path: very short clips (under ~1s) often produce no
            # usable audio for Whisper. Surface a clear error instead of letting
            # the underlying call fail cryptically.
            if len(audio) / 16000.0 < 0.5:
                raise TranscriptionError(
                    "Audio is too short to transcribe. Record at least a second "
                    "of speech and try again."
                )
            result = _decode(audio, model_id, lang, collect_transcribe_options(settings))
            if progress_callback:
                progress_callback(1.0)
    finally:
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
        progress=1.0,  # transcription completed
    )


def transcribe(path: str, language: str | None = None, progress_callback=None) -> TranscriptionResult:
    """Serialize access to the native context and retain chunk progress callbacks."""
    with _model_lock:
        return _transcribe_locked(path, language, progress_callback)


def _load_waveform(path: str) -> np.ndarray:
    """Decode any supported upload to mono float32 PCM at 16 kHz via ffmpeg."""
    ffmpeg = get_settings().ffmpeg_bin or shutil.which("ffmpeg")
    if not ffmpeg:
        raise TranscriptionError("ffmpeg not found. Install ffmpeg to decode uploaded audio.")
    try:
        decoded = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-i", path,
             "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
            check=True, capture_output=True,
        )
        return np.frombuffer(decoded.stdout, dtype="<f4").astype(np.float32)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.decode(errors="replace").strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise TranscriptionError(f"Could not decode audio: {detail}") from exc


def _needs_chunking(audio: np.ndarray) -> bool:
    """True when the audio is long enough that single-pass decode may truncate."""
    return (audio.shape[0] / 16000.0) > _CHUNK_SECONDS


def _transcribe_chunked(
    audio: np.ndarray, model_id: str, lang: str | None, options: dict,
    progress_callback=None,
) -> dict:
    """Transcribe long audio by decoding fixed windows and merging the results.

    Each window is ``_CHUNK_SECONDS`` of audio with ``_CHUNK_OVERLAP_SECONDS``
    of overlap on the leading edge (so a word cut at a boundary is recovered).
    Context reuse is disabled per chunk: cross-chunk context is
    what makes large models loop/hallucinate and bail early on long input, and we
    re-assemble the full transcript ourselves. Segment timestamps are shifted by
    each window's start offset so the merged timeline stays continuous.

    If ``progress_callback`` is provided, it will be called with a float (0.0-1.0)
    after each chunk is transcribed.
    """
    sr = 16000
    chunk_samples = _CHUNK_SECONDS * sr
    overlap_samples = _CHUNK_OVERLAP_SECONDS * sr
    step = chunk_samples - overlap_samples

    total = int(audio.shape[0])
    # Windows cover [0, chunk), [step, step+chunk), ...; the last window reaches
    # the end of the audio.
    starts = [0]
    while starts[-1] + chunk_samples < total:
        starts.append(starts[-1] + step)

    all_text: list[str] = []
    all_segments: list[dict] = []
    overlap_skip = float(_CHUNK_OVERLAP_SECONDS)
    total_chunks = len(starts)

    for i, start in enumerate(starts):
        window = audio[start : start + chunk_samples]
        if window.shape[0] < sr:
            window = np.pad(window, (0, sr - window.shape[0]))
        chunk_result = _decode(window, model_id, lang, {**options, "no_context": True})
        if progress_callback:
            progress_callback((i + 1) / total_chunks)
        offset = start / sr
        window_segments = [
            s for s in chunk_result.get("segments", []) if i == 0 or s.get("end", 0.0) > overlap_skip
        ]
        for seg in window_segments:
            all_segments.append(
                {
                    "start": float(seg.get("start", 0.0)) + offset,
                    "end": float(seg.get("end", 0.0)) + offset,
                    "text": seg.get("text", "").strip(),
                }
            )
        text = " ".join(s.get("text", "").strip() for s in window_segments).strip()
        if text:
            all_text.append(text)

    return {
        "text": " ".join(all_text),
        "segments": all_segments,
        "language": lang or "en",
    }


def _plan_vad_chunks(audio: np.ndarray, settings) -> list[tuple[int, int]] | None:
    """Detect speech and merge it into transcription chunks.

    Returns a list of (start_sample, end_sample) boundaries, or ``None`` if VAD
    fails (so the caller falls back to fixed-window chunking). ``audio`` is the
    same numpy waveform used for transcription, without another decode.
    """
    try:
        np_audio = np.asarray(audio, dtype=np.float32)
        timestamps = _vad.speech_timestamps(
            np_audio,
            threshold=settings.vad_threshold,
            min_speech_ms=settings.vad_min_speech_ms,
            min_silence_ms=settings.vad_min_silence_ms,
            speech_pad_ms=settings.vad_speech_pad_ms,
        )
        if not timestamps:
            logger.warning("VAD found no speech; falling back to fixed windows.")
            return None
        # Silero can return a single long unbroken segment (e.g. a 47s monologue
        # with no pause). Subdivide any run longer than the max chunk so the
        # merge step can't produce an oversized chunk.
        max_samples = settings.vad_chunk_max_sec * 16000
        split: list[tuple[int, int]] = []
        for start, end in timestamps:
            if end - start <= max_samples:
                split.append((start, end))
            else:
                for s in range(start, end, max_samples):
                    split.append((s, min(s + max_samples, end)))
        return _vad.merge_into_chunks(
            split,
            target_samples=settings.vad_chunk_target_sec * 16000,
            max_samples=max_samples,
        )
    except Exception as exc:  # noqa: BLE001 - degrade to fallback chunking
        logger.warning("VAD chunking failed (%s); using fixed windows.", exc)
        return None


def _transcribe_vad_chunks(
    audio: np.ndarray,
    chunks: list[tuple[int, int]],
    model_id: str,
    lang: str | None,
    options: dict,
    progress_callback=None,
) -> dict:
    """Transcribe VAD-merged speech chunks with one Whisper call each.

    Each chunk is a continuous run of speech, so Whisper is never cut mid-sentence
    and there is no overlap to dedupe. Segment timestamps are shifted by each
    chunk's start offset so the merged timeline stays continuous and matches the
    real audio clock.

    If ``progress_callback`` is provided, it will be called with a float (0.0-1.0)
    after each chunk is transcribed.
    """
    np_audio = np.asarray(audio, dtype=np.float32)
    sr = 16000

    all_text: list[str] = []
    all_segments: list[dict] = []
    total_chunks = len(chunks)

    for i, (start, end) in enumerate(chunks):
        window = np_audio[start:end]
        if window.shape[0] < sr:
            window = np.pad(window, (0, sr - window.shape[0]))
        chunk_result = _decode(window, model_id, lang, {**options, "no_context": True})
        offset = start / sr
        for seg in chunk_result.get("segments", []):
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
        if progress_callback:
            progress_callback((i + 1) / total_chunks)

    return {
        "text": " ".join(all_text),
        "segments": all_segments,
        "language": lang or "en",
    }
