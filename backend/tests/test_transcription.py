"""Portable whisper.cpp adapter tests; no weights, GPU, or network required."""
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess

import numpy as np
import pytest

from smoothie import transcription as t
from smoothie.config import Settings
from smoothie.preprocess import tune


@pytest.fixture
def settings(monkeypatch):
    settings = Settings(transcription_model="tiny")
    monkeypatch.setattr(t, "get_settings", lambda: settings)
    monkeypatch.setattr(tune, "get_settings", lambda: settings)
    monkeypatch.setattr(t, "_model", None)
    monkeypatch.setattr(t, "_model_key", None)
    return settings


@pytest.mark.parametrize("name", ["tiny", "base", "small", "medium", "large-v3"])
@pytest.mark.parametrize("suffix", ["", "-mlx"])
def test_legacy_model_names_migrate(name, suffix):
    assert Settings(transcription_model=f"mlx-community/whisper-{name}{suffix}").transcription_model == name


def test_local_model_and_named_download(monkeypatch, tmp_path):
    local = tmp_path / "model.bin"
    local.touch()
    download = Mock(return_value="cached.bin")
    monkeypatch.setattr(t, "hf_hub_download", download)
    assert t._download_model(str(local)) == str(local)
    download.assert_not_called()
    assert t._download_model("tiny") == "cached.bin"
    download.assert_called_once_with(repo_id="ggerganov/whisper.cpp", filename="ggml-tiny.bin")
    with pytest.raises(t.TranscriptionError, match="not a directory"):
        t._download_model(str(tmp_path))
    with pytest.raises(t.TranscriptionError, match="Unknown whisper.cpp model"):
        t._download_model("mlx-community/custom-weights")


def test_download_failure_is_readable(monkeypatch):
    monkeypatch.setattr(t, "hf_hub_download", Mock(side_effect=OSError("offline")))
    with pytest.raises(t.TranscriptionError, match="Failed to download.*offline"):
        t._download_model("tiny")


def test_context_cache_and_device_switch(settings, monkeypatch):
    import smoothie.whisper_cpp
    factory = Mock(side_effect=lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(smoothie.whisper_cpp, "CheckedModel", factory)
    monkeypatch.setattr(t, "_download_model", lambda _: "tiny.bin")
    first = t._load_model_safe("tiny")
    assert t._load_model_safe("tiny") is first
    assert factory.call_count == 1
    assert factory.call_args.kwargs["context_params"] == {"use_gpu": True, "gpu_device": 0}
    settings.transcription_device = "cpu"
    assert t._load_model_safe("tiny") is not first
    assert factory.call_args.kwargs["context_params"]["use_gpu"] is False
    settings.tune_beam_size = 3
    t._load_model_safe("tiny")
    assert factory.call_args.kwargs["params_sampling_strategy"] == 1
    settings.tune_enabled = False
    t._load_model_safe("tiny")
    assert factory.call_args.kwargs["params_sampling_strategy"] == 0


def test_gpu_initialization_exception_retries_cpu(settings, monkeypatch):
    import smoothie.whisper_cpp
    cpu = object()
    factory = Mock(side_effect=[RuntimeError("GPU unavailable"), cpu])
    monkeypatch.setattr(smoothie.whisper_cpp, "CheckedModel", factory)
    monkeypatch.setattr(t, "_download_model", lambda _: "tiny.bin")
    assert t._load_model_safe("tiny") is cpu
    assert factory.call_args.kwargs["context_params"] == {"use_gpu": False}


def test_decode_converts_ticks_and_resets_options(monkeypatch):
    native = Mock()
    native.transcribe.return_value = [SimpleNamespace(t0=25, t1=175, text=" Hello ")]
    monkeypatch.setattr(t, "_load_model_safe", lambda _: native)
    result = t._decode(np.zeros(16000), "tiny", None, {"suppress_nst": True})
    assert result == {"text": "Hello", "segments": [{"start": 0.25, "end": 1.75, "text": "Hello"}]}
    assert native.transcribe.call_args.kwargs["language"] == "auto"
    assert native.transcribe.call_args.args[0].dtype == np.float32
    t._decode(np.zeros(16000), "tiny", "en", {})
    assert native.transcribe.call_args.kwargs["suppress_nst"] is False
    assert native.transcribe.call_args.kwargs["no_context"] is True


def test_native_failure_is_wrapped(monkeypatch):
    monkeypatch.setattr(t, "_load_model_safe", lambda _: SimpleNamespace(transcribe=Mock(side_effect=RuntimeError("decode error"))))
    with pytest.raises(t.TranscriptionError, match="Whisper transcription failed"):
        t._decode(np.zeros(16000), "tiny", "en", {})


def test_fixed_windows_cover_tail_and_keep_opening_segment(monkeypatch):
    windows = []
    def decode(audio, model, lang, options):
        windows.append((len(audio), options))
        return {"segments": [
            {"start": 0, "end": 0.8, "text": "opening"},
            {"start": 1, "end": len(audio) / 16000, "text": "speech"},
        ]}
    monkeypatch.setattr(t, "_decode", decode)
    progress = []
    result = t._transcribe_chunked(np.zeros(60 * 16000), "tiny", "en", {}, progress.append)
    assert [n for n, _ in windows] == [30 * 16000, 30 * 16000, 2 * 16000]
    assert result["segments"][0]["text"] == "opening"
    assert result["segments"][-1]["end"] == 60
    assert result["text"].count("opening") == 1
    assert all(opts["no_context"] for _, opts in windows)
    assert progress == sorted(progress)
    assert progress[-1] == 1.0


def test_vad_windows_keep_real_timeline_and_short_speech(monkeypatch):
    calls = []
    def decode(audio, *args):
        calls.append(len(audio))
        return {"text": "word", "segments": [{"start": 0, "end": 0.4, "text": "word"}]}
    monkeypatch.setattr(t, "_decode", decode)
    progress = []
    result = t._transcribe_vad_chunks(np.zeros(40 * 16000), [(2*16000, 3*16000), (35*16000, 35*16000+8000)], "tiny", "en", {}, progress.append)
    assert calls == [16000, 16000]
    assert [s["start"] for s in result["segments"]] == [2, 35]
    assert progress == [0.5, 1]


def test_decode_audio_uses_ffmpeg_and_normalizes_pcm(settings, monkeypatch):
    settings.ffmpeg_bin = "/custom/ffmpeg"
    run = Mock(return_value=SimpleNamespace(stdout=np.array([0.25, -0.5], dtype="<f4").tobytes()))
    monkeypatch.setattr(t.subprocess, "run", run)
    assert t._load_waveform("stereo.webm").tolist() == [0.25, -0.5]
    args = run.call_args.args[0]
    assert args[0] == "/custom/ffmpeg"
    assert args[args.index("-ar") + 1] == "16000"
    assert args[args.index("-ac") + 1] == "1"
    run.side_effect = subprocess.CalledProcessError(1, args, stderr=b"invalid audio")
    with pytest.raises(t.TranscriptionError, match="invalid audio"):
        t._load_waveform("broken.webm")


def test_tuning_maps_supported_options(settings):
    settings.tune_beam_size = 4
    settings.tune_condition_on_previous_text = False
    settings.tune_no_speech_threshold = 0.7
    options = tune.TuneModule().options()
    assert options["beam_search"]["beam_size"] == 4
    assert options["no_context"] is True
    assert options["no_speech_thold"] == 0.7
    assert options["suppress_nst"] is True
    assert "hallucination_silence_threshold" not in options


def test_fixed_windows_do_not_repeat_an_already_covered_tail(monkeypatch):
    decode = Mock(return_value={"text": "", "segments": []})
    monkeypatch.setattr(t, "_decode", decode)
    t._transcribe_chunked(np.zeros(int(58.2 * 16000)), "tiny", "en", {})
    assert decode.call_count == 2


def test_native_status_error_does_not_read_stale_segments(monkeypatch):
    from smoothie.whisper_cpp import CheckedModel, native

    # Exercise the adapter without allocating a native context.
    context = SimpleNamespace(_ctx=object(), _params=object(), _get_segments=Mock())
    monkeypatch.setattr(native, "whisper_full", Mock(return_value=-7))
    read_count = Mock()
    monkeypatch.setattr(native, "whisper_full_n_segments", read_count)
    with pytest.raises(RuntimeError, match="code -7"):
        CheckedModel._transcribe(context, np.zeros(16000, dtype=np.float32))
    read_count.assert_not_called()
    context._get_segments.assert_not_called()
