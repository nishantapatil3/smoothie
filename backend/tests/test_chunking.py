"""Regression test: long recordings must be transcribed in full.

``mlx-whisper`` can stop decoding partway through a single very long clip
(the large-v3 model has been observed truncating after ~100s), which silently
drops the rest of the transcript. Smoothie works around this by slicing long
audio into bounded windows and merging the results. This test pins that
behavior: a generated multi-minute *speech* clip must yield a transcript that
spans (almost) the entire duration, not just the first ~100s.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from mlx_whisper.audio import load_audio
from smoothie import transcription

_SAY = shutil.which("say")


def _make_long_speech(path: Path, seconds: float = 180.0) -> None:
    """Build a real speech clip longer than the 30s chunk size.

    Uses macOS ``say`` to synthesize speech (which Whisper treats as real
    speech, unlike a synthetic tone). Phrases are repeated to exceed the target
    duration. Skips the test if ``say`` is unavailable.
    """
    if _SAY is None:
        pytest.skip("macOS 'say' not available to generate a speech fixture")

    phrases = [
        "The team reviewed the quarterly roadmap and agreed on the launch date.",
        "Engineering finished the new pipeline and will plan the rollout next week.",
        "Marketing will coordinate the campaign and the social announcement.",
        "Support flagged high priority bugs and will triage them today.",
        "Design improved the onboarding flow and reduced time to first value.",
    ]
    # Loop phrases until we have enough spoken content for ~`seconds`.
    parts: list[Path] = []
    tmp = path.parent
    i = 0
    while True:
        p = tmp / f"_say_{i}.aiff"
        subprocess.run(
            [_SAY, "-v", "Samantha", "-o", str(p), phrases[i % len(phrases)]],
            check=True,
            capture_output=True,
        )
        parts.append(p)
        # Stop once cumulative duration is comfortably above the target.
        try:
            dur = float(
                subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(p)],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
            )
        except Exception:
            dur = 5.0
        if sum(_dur(q) for q in parts) >= seconds:
            break
        i += 1
        if i > 60:  # safety
            break

    inputs = []
    for p in parts:
        inputs += ["-i", str(p)]
    filter_parts = "".join(f"[{j}:a]" for j in range(len(parts)))
    subprocess.run(
        [
            "ffmpeg", "-y", *inputs,
            "-filter_complex",
            f"{filter_parts}concat=n={len(parts)}:v=0:a=1[a]",
            "-map", "[a]", "-ar", "16000", "-ac", "1", str(path),
        ],
        check=True,
        capture_output=True,
    )
    for p in parts:
        p.unlink(missing_ok=True)


def _dur(path: Path) -> float:
    try:
        return float(
            subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
        )
    except Exception:
        return 0.0


def test_long_audio_is_not_truncated():
    """A >100s speech clip must produce segments spanning near its full duration."""
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "long_speech.wav"
        _make_long_speech(clip, seconds=180.0)

        # Use the fast tiny model so the test stays quick; the chunking logic is
        # model-independent, so this exercises the same merge path.
        transcription.get_settings().transcription_model = "mlx-community/whisper-tiny"
        result = transcription.transcribe(str(clip))

    assert result.segments, "expected at least one segment"
    last_end = result.segments[-1]["end"]
    assert last_end >= 150.0, f"transcript truncated at {last_end:.1f}s (expected >=150s)"


def test_chunked_merge_covers_full_duration():
    """_transcribe_chunked must cover the whole clip, not just the first window."""
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "long_speech.wav"
        _make_long_speech(clip, seconds=200.0)
        audio = load_audio(str(clip))

    opts = transcription.collect_transcribe_options(transcription.get_settings())
    res = transcription._transcribe_chunked(
        audio, "mlx-community/whisper-tiny", "en", opts
    )
    assert res["segments"], "expected segments from chunked transcription"
    last_end = res["segments"][-1]["end"]
    assert last_end >= 170.0, f"chunked result truncated at {last_end:.1f}s"
    assert res["segments"][0]["start"] <= 1.0
