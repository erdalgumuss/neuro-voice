"""UTMOSv2 metric — naturalness MOS-prediction for TTS clips.

UTMOSv2 (Saeki et al., Interspeech 2024) is a learned ITU-grade MOS
predictor that has become the de-facto open-weights MOS-equivalent
for TTS benchmarks. Score range is roughly 1.0..5.0 (higher = more
natural; real human speech sits around 4.2-4.6, top vendor TTS
around 4.0-4.4).

Backend: `sarulab-speech/UTMOSv2`. Operator install:

    pip install git+https://github.com/sarulab-speech/UTMOSv2.git

The package is git-only (not on PyPI), so we don't pin it as a hard
dependency in pyproject — operators opt in via the `[eval]` extras
group (see ADR-12). When the import fails at runtime, we degrade
gracefully to a nan score with `error="utmosv2 not installed"`
instead of crashing the eval run.

Reference implementation:
  https://github.com/sarulab-speech/UTMOSv2

Caveats:
  * UTMOSv2 was trained on English-dominant MOS data. Absolute scores
    on Turkish (or other underserved languages) should be interpreted
    as a relative drift signal across runs of the same voice, not as
    an absolute naturalness assertion. ADR-12 risk section.
  * Reference-free metric — `reference_text` is ignored.
  * Heavy import path: torch + utmosv2 + model checkpoint (~70 MB).
    First call triggers the download via Hugging Face Hub.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from . import MetricResult

logger = logging.getLogger("neurovoice.eval.utmosv2")


@dataclass
class UTMOSv2Metric:
    """UTMOSv2-backed naturalness MOS predictor.

    Thread-safe lazy load: the underlying torch model is held under a
    per-instance lock; `score()` is safe to call concurrently from
    multiple workers (each call only reads, the lock just gates the
    one-time load).

    `device` defaults to "auto" — the upstream package picks cuda when
    available, CPU otherwise. Pin to `"cpu"` for deterministic CI runs
    at the cost of a single-clip cold start of ~3-5 s.
    """

    name: str = "utmosv2"
    device: str = "auto"

    _model: object | None = None
    # field(default_factory=threading.Lock) gives every instance its
    # own lock — the previous pattern (class-level Lock() default +
    # __post_init__ rebind) worked but masked the smell that a
    # mutable shared default would cause cross-instance contention.
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _import_error: str | None = None

    def _load(self) -> object | None:
        """Return the loaded predictor, or None if the upstream package
        is unavailable. Sentinel-cached: a failed import stays failed
        for the lifetime of this instance so we don't repeat the
        ImportError logging on every clip.
        """
        if self._model is not None:
            return self._model
        if self._import_error is not None:
            return None
        with self._lock:
            if self._model is not None:
                return self._model
            if self._import_error is not None:
                return None
            try:
                import utmosv2  # type: ignore[import-not-found]
            except ImportError as e:
                # Graceful degrade — the eval run continues with WER /
                # CER / SECS, this metric reports nan with the import
                # message in `detail` so the report writer can render
                # "(utmosv2 missing)" instead of crashing.
                msg = (
                    "utmosv2 package not installed; "
                    "pip install git+https://github.com/sarulab-speech/UTMOSv2.git"
                )
                logger.warning("%s — %s", msg, e)
                self._import_error = msg
                return None
            logger.info(
                "loading UTMOSv2 predictor (device=%s)", self.device,
            )
            # upstream API: utmosv2.create_model(pretrained=True)
            # downloads the checkpoint from HF Hub on first call.
            kwargs: dict[str, object] = {"pretrained": True}
            if self.device != "auto":
                kwargs["device"] = self.device
            try:
                self._model = utmosv2.create_model(**kwargs)
            except Exception as e:  # noqa: BLE001 — surface as nan
                msg = f"UTMOSv2 model load failed: {type(e).__name__}: {e}"
                logger.exception(msg)
                self._import_error = msg
                return None
        return self._model

    def score(
        self,
        pcm_int16: bytes,
        sample_rate: int,
        *,
        reference_text: str,
    ) -> MetricResult:
        _ = reference_text  # UTMOSv2 is reference-free (quality only)
        if not pcm_int16:
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="higher",
                detail={"error": "empty_pcm"},
            )
        model = self._load()
        if model is None:
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="higher",
                detail={"error": self._import_error or "utmosv2 unavailable"},
            )

        # Heavy imports inside the call path so `from
        # eval.metrics.utmosv2 import UTMOSv2Metric` stays cheap on a
        # no-GPU dev box.
        import numpy as np

        # UTMOSv2 wants a float32 mono waveform. Resample to 16 kHz
        # via numpy linear interp — same approach as WhisperWERMetric;
        # the predictor's internal feature extractor is robust to the
        # small linear-resample artifacts at the scale of MOS noise.
        arr = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000:
            ratio = 16000 / sample_rate
            target_len = max(1, int(round(arr.size * ratio)))
            xp = np.linspace(0, arr.size - 1, arr.size, dtype=np.float64)
            x = np.linspace(0, arr.size - 1, target_len, dtype=np.float64)
            arr = np.interp(x, xp, arr).astype(np.float32)

        try:
            # upstream API accepts numpy array + sample rate.
            score = float(model.predict(input=arr, input_sr=16000))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001 — degrade per ADR-12
            logger.exception("UTMOSv2 predict failed")
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="higher",
                detail={"error": f"{type(e).__name__}: {e}"},
            )
        return MetricResult(
            metric_name=self.name,
            score=score,
            direction="higher",
            detail={"sample_rate_in": sample_rate, "sample_rate_used": 16000},
        )
