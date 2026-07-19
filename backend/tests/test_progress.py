"""Tests for live transcription progress capture."""
from __future__ import annotations

import smoothie.config as c
from smoothie import transcription


def test_get_progress_resets_after_transcribe():
    """Progress is captured during a call and reset to 0 afterwards."""
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
    # Final captured progress should be a valid fraction in [0, 1].
    assert 0.0 <= result.progress <= 1.0
    # After the call, the live counter is reset.
    assert transcription.get_progress() == 0.0


def test_get_progress_idle_is_zero():
    assert transcription.get_progress() == 0.0
