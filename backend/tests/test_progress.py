"""Tests for transcription progress tracking.

Progress is now stored per-job in the database instead of a global variable.
These tests verify the transcription result includes progress information.
"""
from __future__ import annotations

import smoothie.config as c
from smoothie import transcription


def test_transcribe_returns_progress():
    """Transcription returns progress=1.0 on completion."""
    c.get_settings().transcription_model = "mlx-community/whisper-tiny"
    # Use the short synthetic-ish real file if present, else skip.
    import glob
    import os

    candidates = glob.glob("data/audio/*.m4a") + glob.glob("data/audio/*.wav")
    if not candidates:
        import pytest

        pytest.skip("no audio fixture available")
    path = candidates[0]

    result = transcription.transcribe(path)
    # Final captured progress should be 1.0 (completed).
    assert result.progress == 1.0


def test_transcribe_returns_segments():
    """Transcription returns segments with start/end/text."""
    c.get_settings().transcription_model = "mlx-community/whisper-tiny"
    import glob

    candidates = glob.glob("data/audio/*.m4a") + glob.glob("data/audio/*.wav")
    if not candidates:
        import pytest

        pytest.skip("no audio fixture available")
    path = candidates[0]

    result = transcription.transcribe(path)
    assert isinstance(result.segments, list)
    if result.segments:
        seg = result.segments[0]
        assert "start" in seg
        assert "end" in seg
        assert "text" in seg
