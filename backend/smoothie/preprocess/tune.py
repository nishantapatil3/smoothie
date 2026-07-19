"""Tune Whisper transcription parameters (no audio transformation).

Mirrors meetily's Whisper decode quality settings so transcription behaves
like meetily:
  * temperature fallback (0.0 -> 0.2 -> ...)  — MLX Whisper default, kept
  * forced language (transcription_language, default "en")
  * no_speech_threshold = 0.6                  — MLX Whisper default, kept
  * condition_on_previous_text = True          — MLX Whisper default, kept
  * hallucination_silence_threshold            — suppress non-speech rambling
    (meetily's "suppress non-speech tokens")

Each knob is overridable via settings; the tune module applies meetily's
sensible defaults when a knob is left unset.

Supported knobs:
  * ``beam_size``                  -> decode_options["beam_size"] (None = greedy, like meetily's beam_size=-1)
  * ``condition_on_previous_text`` -> bool
  * ``no_speech_threshold``        -> float
  * ``hallucination_silence_threshold`` -> float (drops likely-hallucinated segments)
"""
from __future__ import annotations

from ..config import get_settings
from . import TranscribeTuner


class TuneModule(TranscribeTuner):
    name = "tune"

    # meetily suppresses non-speech hallucinations; this threshold (seconds of
    # silence that triggers a reset) is the MLX Whisper equivalent.
    HALLUCINATION_SILENCE_THRESHOLD = 2.0
    # meetily uses best_of=2 (sample 2 candidates, keep the best) at temp 0.
    BEST_OF = 2

    @property
    def enabled(self) -> bool:
        return get_settings().tune_enabled

    def options(self) -> dict:
        settings = get_settings()
        opts: dict = {}

        if settings.tune_beam_size is not None:
            opts["beam_size"] = settings.tune_beam_size
        if settings.tune_condition_on_previous_text is not None:
            opts["condition_on_previous_text"] = (
                settings.tune_condition_on_previous_text
            )
        if settings.tune_no_speech_threshold is not None:
            opts["no_speech_threshold"] = settings.tune_no_speech_threshold
        # Apply meetily's anti-hallucination default unless overridden.
        opts["hallucination_silence_threshold"] = (
            settings.tune_hallucination_silence_threshold
            if settings.tune_hallucination_silence_threshold is not None
            else self.HALLUCINATION_SILENCE_THRESHOLD
        )
        # best_of improves stability at temp 0 (meetily's default). Only meaningful
        # when not using beam search, which matches meetily (greedy beam_size=-1).
        opts["best_of"] = self.BEST_OF

        return opts

