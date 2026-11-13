"""Checked inference calls for the pinned pywhispercpp binding.

pywhispercpp 1.5.1's Model._transcribe discards whisper_full's return code.
Propagate native failures so a failed decode cannot save stale/partial segments
as a successful transcript. Other model and segment handling stays upstream.
"""
from __future__ import annotations

import _pywhispercpp as native
from pywhispercpp.model import Model


class CheckedModel(Model):
    def _transcribe(self, audio, n_processors=None, extract_probability=False):
        if n_processors:
            status = native.whisper_full_parallel(
                self._ctx, self._params, audio, audio.size, n_processors
            )
        else:
            status = native.whisper_full(self._ctx, self._params, audio, audio.size)
        if status != 0:
            raise RuntimeError(f"whisper.cpp decode failed (code {status}); see native logs")
        count = native.whisper_full_n_segments(self._ctx)
        return self._get_segments(self._ctx, 0, count, extract_probability)
