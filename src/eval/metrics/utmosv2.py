"""UTMOSv2 metric — naturalness MOS-prediction for TTS clips.

UTMOSv2 (Saeki et al., Interspeech 2024) is a learned ITU-grade MOS
predictor that has become the de-facto open-weights MOS-equivalent
for TTS benchmarks. Score range is roughly 1.0..5.0 (higher = more
natural; real human speech sits around 4.2-4.6, top vendor TTS
around 4.0-4.4).

This module ships the integration scaffold only — the real backend
needs a one-time model download (~70 MB) plus a torch runtime. As
with whisper_wer, the heavy imports stay inside `score()` so the
package always loads in CI / no-GPU dev environments.

Reference implementation:
  https://github.com/sarulab-speech/UTMOSv2
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from . import MetricResult

logger = logging.getLogger("nqai_voice.eval.utmosv2")


@dataclass
class UTMOSv2Metric:
    """Stub-friendly UTMOSv2 wrapper. Lazy-loads the predictor; thread-
    safe. The real call will use the upstream `utmosv2` package once
    operators install it; today the implementation is a placeholder
    that raises `NotImplementedError` on `score()` so we don't ship a
    silent fake-score path. The package structure is what we needed
    to land in PR #3 — the actual model wiring is one focused PR
    away and follows the WhisperWERMetric template above."""

    name: str = "utmosv2"
    device: str = "auto"

    _model: object | None = None
    _lock: threading.Lock = threading.Lock()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lock", threading.Lock())

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                # Intentional: shipping the wiring; the operator
                # installs `utmosv2` + downloads the checkpoint, then
                # this branch becomes a one-liner like:
                #
                #     import utmosv2
                #     self._model = utmosv2.create_model(
                #         device=self.device,
                #     )
                #
                # Keeping it explicit so an accidental import doesn't
                # silently swap to a non-functional backend.
                raise NotImplementedError(
                    "UTMOSv2 backend not wired in PR #3 — "
                    "follow the WhisperWERMetric template + the "
                    "upstream sarulab-speech/UTMOSv2 README to ship "
                    "the actual model call. The protocol + harness "
                    "scaffolding is what landed in PR #3."
                )
        return self._model

    def score(
        self,
        pcm_int16: bytes,
        sample_rate: int,
        *,
        reference_text: str,
    ) -> MetricResult:
        _ = reference_text  # UTMOSv2 is reference-free (quality only)
        self._load()  # raises NotImplementedError until wired
        raise NotImplementedError  # unreachable but satisfies type-check
