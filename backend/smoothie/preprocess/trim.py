"""Trim silence from audio before transcription (ffmpeg ``silenceremove``).

Conservative by default: only cuts near-total silence (<= -40 dB held for at
least 0.5 s), preserving natural pauses and the onsets/tails of quiet speech.
Output is re-encoded to 16 kHz mono WAV, which is what Whisper expects anyway.
"""
from __future__ import annotations

import shutil
import subprocess

from ..config import get_settings
from . import AudioProcessor


class TrimModule(AudioProcessor):
    name = "trim"

    @property
    def enabled(self) -> bool:
        return get_settings().trim_enabled

    def process(self, src, dst) -> None:
        settings = get_settings()
        ffmpeg = settings.ffmpeg_bin or shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg not found. Install ffmpeg or set ffmpeg_bin in config.yaml."
            )

        # Remove silence throughout the file while keeping all speech in order.
        # `stop_periods=-1` means "keep removing silence across the whole clip"
        # rather than stopping after the first silent gap (which would discard
        # everything after the first utterance). `start_periods=1` trims only
        # leading silence. `min_silence` is the minimum silent stretch cut;
        # `threshold` is the noise floor.
        threshold = settings.trim_threshold  # dB, e.g. -40
        min_silence = settings.trim_min_silence  # seconds, e.g. 0.5
        af = (
            f"silenceremove=start_periods=1:start_duration={min_silence}"
            f":start_threshold={threshold}dB"
            f":stop_periods=-1:stop_duration={min_silence}"
            f":stop_threshold={threshold}dB"
        )

        cmd = [
            ffmpeg,
            "-y",
            "-i", str(src),
            "-af", af,
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            str(dst),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
