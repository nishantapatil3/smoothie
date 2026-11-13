"""Progress and segment contract for the transcription entry point."""
import numpy as np
import pytest

from smoothie import transcription as t
from smoothie.config import Settings


def test_transcribe_returns_progress_and_segments(monkeypatch):
    settings = Settings(vad_enabled=False)
    monkeypatch.setattr(t, "get_settings", lambda: settings)
    monkeypatch.setattr(t, "_load_model_safe", lambda _: None)
    monkeypatch.setattr(t, "_load_waveform", lambda _: np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(t, "run_audio_transforms", lambda path, _: path)
    monkeypatch.setattr(t, "collect_transcribe_options", lambda _: {})
    expected = [{"start": 0.1, "end": 0.9, "text": "Hello"}]
    monkeypatch.setattr(t, "_decode", lambda *args: {"text": "Hello", "segments": expected})
    progress = []
    result = t.transcribe("speech.wav", progress_callback=progress.append)
    assert result.text == "Hello"
    assert result.segments == expected
    assert result.progress == 1.0
    assert progress == [1.0]


def test_processed_file_removed_on_decode_failure(monkeypatch, tmp_path):
    original = tmp_path / "original.wav"
    processed = tmp_path / "processed.wav"
    original.touch()
    processed.touch()
    monkeypatch.setattr(t, "_load_model_safe", lambda _: None)
    monkeypatch.setattr(t, "run_audio_transforms", lambda *args: str(processed))
    def fail(_):
        raise t.TranscriptionError("bad audio")
    monkeypatch.setattr(t, "_load_waveform", fail)
    with pytest.raises(t.TranscriptionError, match="bad audio"):
        t.transcribe(str(original))
    assert original.exists()
    assert not processed.exists()
