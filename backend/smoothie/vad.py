"""Voice-activity-detection chunking for long-audio transcription.

Instead of slicing audio into fixed 30s windows (which can cut a sentence in
half), we detect where speech actually occurs with Silero VAD and merge those
runs into ~22-27s chunks that align with natural pauses. This mirrors how
meetily chunks: one Whisper call per speech-aligned segment, continuous
timeline, no overlap.

Silero is imported lazily so a missing package degrades to the fixed-window
fallback in transcription.py rather than crashing startup.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("smoothie.vad")

SR = 16000  # Whisper / Silero sample rate


def vad_available() -> bool:
    """True when silero-vad can be imported in this environment."""
    try:
        import silero_vad  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - any import/load failure
        return False


def speech_timestamps(
    waveform: np.ndarray,
    *,
    threshold: float = 0.5,
    min_speech_ms: int = 250,
    min_silence_ms: int = 400,
    speech_pad_ms: int = 200,
) -> list[tuple[int, int]]:
    """Return list of (start_sample, end_sample) speech runs at 16 kHz.

    ``waveform`` must be a 1-D float32 array sampled at 16 kHz. Parameters are
    tuned for meeting speech: drop micro-gaps (<250ms) as noise, treat anything
    quieter than ``threshold`` for ``min_silence_ms`` as a real pause so chunks
    break at natural boundaries, and pad each run slightly so word onsets/tails
    aren't clipped.
    """
    import silero_vad

    model = silero_vad.load_silero_vad()
    # get_speech_timestamps accepts a numpy array directly.
    raw = silero_vad.get_speech_timestamps(
        waveform,
        model,
        threshold=threshold,
        min_speech_duration_ms=min_speech_ms,
        min_silence_duration_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
        return_seconds=False,
    )
    return [(int(t["start"]), int(t["end"])) for t in raw]


def merge_into_chunks(
    timestamps: list[tuple[int, int]],
    *,
    target_samples: int,
    max_samples: int,
) -> list[tuple[int, int]]:
    """Merge adjacent speech runs into chunks of ~``target_samples``.

    Adjacent runs are joined (VAD fires on every word gap, so raw timestamps are
    tiny); a chunk keeps growing until it reaches ``target_samples``, then a new
    chunk starts at the next real pause. Caps at ``max_samples``, breaking even
    mid-run, so a single long unbroken utterance can't become an oversized chunk.
    Reproduces meetily's ~24s mean segment length without spawning dozens of tiny
    Whisper calls.
    """
    if not timestamps:
        return []

    chunks: list[tuple[int, int]] = []
    cur_start, cur_end = timestamps[0]
    for start, end in timestamps[1:]:
        gap = start - cur_end
        length = end - cur_start
        # Break the current chunk when:
        #   * this run is a real pause (gap > 0) AND we've already hit the target,
        #     or
        #   * continuing would exceed the hard max even without a pause (a long
        #     unbroken utterance, e.g. a 47s monologue) — break at the last
        #     sample so the oversized run is split.
        if length >= max_samples:
            chunks.append((cur_start, cur_end))
            cur_start, cur_end = start, end
        elif gap > 0 and length >= target_samples:
            chunks.append((cur_start, cur_end))
            cur_start, cur_end = start, end
        else:
            cur_end = end  # merge: extend the current run

    chunks.append((cur_start, cur_end))
    return chunks
