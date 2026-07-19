"""Regression tests for the silence-trim pre-processing module.

The key regression: ``silenceremove`` with ``stop_periods=1`` kept only the
FIRST spoken chunk and discarded the rest of a long recording (a 6:36 audio was
trimmed to ~2.5s). These tests pin the correct behavior: all speech is
preserved in order, only silence is removed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from smoothie.config import get_settings
from smoothie.preprocess.trim import TrimModule


def _duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def _make_long_audio(path: Path) -> float:
    """Build a file: speech - silence - speech - silence - speech.

    Returns the source duration. Three distinct spoken chunks separated by long
    silences exercise the 'keep all speech' regression.
    """
    # 2s tone, 20s silence, 2s tone, 20s silence, 2s tone -> ~46s total, ~6s speech
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:d=20",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:d=20",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=2",
            "-filter_complex",
            "[1:a][0:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[a]",
            "-map",
            "[a]",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return _duration(path)


@pytest.fixture
def long_audio(tmp_path: Path) -> Path:
    src = tmp_path / "long.wav"
    _make_long_audio(src)
    return src


@pytest.fixture(autouse=True)
def _require_ffmpeg():
    if not (shutil.which("ffmpeg") or get_settings().ffmpeg_bin):
        pytest.skip("ffmpeg not available")


def test_trim_preserves_all_speech(long_audio: Path, tmp_path: Path):
    """The core regression: every spoken chunk is kept, not just the first."""
    src_dur = _duration(long_audio)
    dst = tmp_path / "trimmed.wav"

    TrimModule().process(long_audio, dst)

    assert dst.exists(), "trim produced no output"
    out_dur = _duration(dst)
    # Speech is ~6s; silence removed should leave roughly that (allow padding).
    # Critically it must be far more than a single 2s chunk.
    assert out_dur > 5.0, f"trim collapsed long audio to {out_dur:.1f}s (regression)"
    # And it must be shorter than the source (silence was actually removed).
    assert out_dur < src_dur, "trim removed no silence"


def test_trim_does_not_exceed_source(long_audio: Path, tmp_path: Path):
    """Trimmed output must never be longer than the source."""
    dst = tmp_path / "trimmed.wav"
    TrimModule().process(long_audio, dst)
    assert _duration(dst) <= _duration(long_audio)


def test_trim_disabled_passthrough(tmp_path: Path):
    """When disabled, run_audio_transforms returns the input unchanged."""
    from smoothie.preprocess import run_audio_transforms

    get_settings().trim_enabled = False
    try:
        src = tmp_path / "in.wav"
        _make_long_audio(src)
        result = run_audio_transforms(str(src))
        assert result == str(src), "disabled trim should pass through unchanged"
    finally:
        get_settings().trim_enabled = True
