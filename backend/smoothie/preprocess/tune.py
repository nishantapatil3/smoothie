"""Map Smoothie's decode controls to whisper.cpp parameters.

The old MLX hallucination_silence_threshold has no direct equivalent. Native
non-speech suppression and the no-speech threshold are used instead.
"""
from __future__ import annotations

from ..config import get_settings
from . import TranscribeTuner


class TuneModule(TranscribeTuner):
    name = "tune"

    @property
    def enabled(self) -> bool:
        return get_settings().tune_enabled

    def options(self) -> dict:
        settings = get_settings()
        return {
            "no_context": not (
                settings.tune_condition_on_previous_text
                if settings.tune_condition_on_previous_text is not None else True
            ),
            "no_speech_thold": (
                settings.tune_no_speech_threshold
                if settings.tune_no_speech_threshold is not None else 0.6
            ),
            "greedy": {"best_of": 2},
            "beam_search": {"beam_size": settings.tune_beam_size or -1, "patience": -1.0},
            "suppress_nst": True,
        }
