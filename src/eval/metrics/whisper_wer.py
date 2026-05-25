"""Whisper-TR-WER metric — intelligibility score for Turkish TTS output.

Uses OpenAI's open-weights Whisper (large-v3) as the ASR front-end:
synthesized clip → text → Turkish-normalized WER vs the expected
reference sentence. The lower the WER, the more intelligible the TTS.

Why Whisper specifically:
  * Strong Turkish (the original training set covers TR at meaningful
    scale; large-v3 in particular hits low WER on Common Voice TR).
  * Open weights — no per-call SDK cost.
  * Deterministic with `temperature=0` + `condition_on_previous_text=False`.

Heavy import (whisper / torch) lives inside `score()` so the eval
package can be imported on a no-GPU CI box. The first call triggers
a model download (~3 GB for large-v3). Tests use the registered stub
in `tests/test_eval_harness.py` instead.

Caveats — known sources of WER inflation that have NOTHING to do
with TTS quality:
  * Whisper occasionally inserts "Türkçe:" or "Altyazılı:" preambles.
    We strip a small set of these via `_clean_transcript`.
  * Punctuation differences shouldn't count toward WER on a
    pronunciation-quality benchmark; we apply jiwer's default
    normalisation (lowercase, strip punct, collapse whitespace).
"""

from __future__ import annotations

import io
import logging
import re
import threading
from dataclasses import dataclass

from . import MetricResult

logger = logging.getLogger("nqai_voice.eval.whisper_wer")


_PREAMBLE_RE = re.compile(
    r"^\s*(?:türkçe|altyazılı|tr|turkish)\s*[:.\-—]+\s*",
    flags=re.IGNORECASE,
)


def _clean_transcript(text: str) -> str:
    """Remove well-known Whisper preamble noise. Conservative — only
    strips patterns we've observed in production runs; idempotent."""
    if not text:
        return ""
    cleaned = _PREAMBLE_RE.sub("", text, count=1)
    return cleaned.strip()


@dataclass
class WhisperWERMetric:
    """Real Whisper-backed WER metric. Lazy-loads the model on first
    use; thread-safe via `_lock` because the underlying torch model is
    NOT safe for parallel inference.

    `model_size` defaults to "large-v3" — the smallest size that hits
    publishable WER on Turkish in our domain. Operators can drop to
    "medium" for a 10x cost reduction at the price of a couple of WER
    points; the report writer records the size so cross-run comparisons
    stay honest."""

    name: str = "whisper_wer"
    model_size: str = "large-v3"
    device: str = "auto"

    _model: object | None = None
    _lock: threading.Lock = threading.Lock()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # `threading.Lock()` returns a different instance per call —
        # the dataclass default would share a single lock across all
        # instances, so we re-create per-instance.
        object.__setattr__(self, "_lock", threading.Lock())

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                import whisper  # imported lazily — eval pkg loads w/o GPU
                logger.info("loading whisper model=%s", self.model_size)
                self._model = whisper.load_model(
                    self.model_size,
                    device=None if self.device == "auto" else self.device,
                )
        return self._model

    def score(
        self,
        pcm_int16: bytes,
        sample_rate: int,
        *,
        reference_text: str,
    ) -> MetricResult:
        if not pcm_int16:
            # Empty clip — no transcription possible. WER as nan;
            # report writer renders as "—" without poisoning the
            # aggregate. (alternative: WER=1.0 to penalise truncation,
            # but conflating "no audio" with "garbled audio" hurts
            # debugging — keep them distinct.)
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="lower",
                detail={"error": "empty_pcm"},
            )

        # Heavy deps imported inside the call path so `from
        # eval.metrics.whisper_wer import WhisperWERMetric` works on a
        # no-GPU dev box.
        import numpy as np
        import soundfile as sf
        from jiwer import compose, wer
        from jiwer.transforms import (
            ReduceToListOfListOfWords,
            RemoveMultipleSpaces,
            RemovePunctuation,
            Strip,
            ToLowerCase,
        )

        # Whisper takes a float32 mono waveform at 16 kHz. We resample
        # via soundfile + numpy rather than dragging in librosa here.
        arr = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000:
            # `soundfile` doesn't resample; we use numpy linear interp
            # (good enough for ASR — sub-WER-noise quality loss).
            ratio = 16000 / sample_rate
            target_len = max(1, int(round(arr.size * ratio)))
            xp = np.linspace(0, arr.size - 1, arr.size, dtype=np.float64)
            x = np.linspace(0, arr.size - 1, target_len, dtype=np.float64)
            arr = np.interp(x, xp, arr).astype(np.float32)

        model = self._load()
        # Buffer round-trip keeps Whisper's loader happy on bytes input;
        # cheap because the clip is small.
        buf = io.BytesIO()
        sf.write(buf, arr, 16000, format="WAV", subtype="PCM_16")
        buf.seek(0)

        transcript = model.transcribe(  # type: ignore[attr-defined]
            buf,
            language="tr",
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        raw_text = transcript.get("text", "") if isinstance(transcript, dict) else ""
        hyp = _clean_transcript(raw_text)
        ref = reference_text.strip()

        # jiwer default transformation: lowercase + strip punct + ws.
        transform = compose([
            ToLowerCase(),
            RemovePunctuation(),
            RemoveMultipleSpaces(),
            Strip(),
            ReduceToListOfListOfWords(),
        ])
        score = wer(
            ref, hyp,
            truth_transform=transform,
            hypothesis_transform=transform,
        )
        return MetricResult(
            metric_name=self.name,
            score=float(score),
            direction="lower",
            detail={"hypothesis": hyp, "reference": ref,
                    "model_size": self.model_size},
        )


# Don't register the real metric at import time — the operator opts in
# via `scripts/eval_run.py --enable-real-metrics` or by calling
# `register_metric("whisper_wer", WhisperWERMetric())` from their
# notebook / script. That way `import eval.metrics.whisper_wer` is
# safe on a no-GPU box and tests can register a stub.
