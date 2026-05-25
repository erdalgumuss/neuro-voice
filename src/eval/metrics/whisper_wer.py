"""Whisper-TR-WER + CER metric — intelligibility score for Turkish TTS output.

Uses OpenAI's open-weights Whisper (large-v3) as the ASR front-end:
synthesized clip → text → Turkish-normalized WER (or CER) vs the
expected reference sentence. The lower the score, the more intelligible
the TTS.

Why both WER and CER:
  * WER is the standard intelligibility metric in English-heavy
    benchmarks (Seed-TTS, F5-TTS, ElevenLabs voice clone).
  * CER is the preferred metric for agglutinative languages like
    Turkish — a single-character suffix divergence inflates WER to
    100 % on a word but only ~10 % on the characters of that word.
    See: "Advocating Character Error Rate for Multilingual ASR
    Evaluation" (Liang et al., 2024) — https://arxiv.org/pdf/2410.07400
  * Whisper transcription is the expensive step; emitting BOTH from
    one decode is essentially free. `WhisperCERMetric` accepts a
    `shared_metric=` to reuse the loaded model across both passes.

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
        from jiwer import cer, compose, wer
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
        # CER is computed from the same transcript — pure win on cost.
        # The standalone `WhisperCERMetric` reuses the same Whisper
        # decode via `shared_metric=`, but operators that only register
        # `whisper_wer` still get the CER number in `detail` for
        # ad-hoc inspection (and the report writer surfaces it).
        cer_score = cer(
            ref, hyp,
            truth_transform=transform,
            hypothesis_transform=transform,
        )
        return MetricResult(
            metric_name=self.name,
            score=float(score),
            direction="lower",
            detail={
                "cer": float(cer_score),
                "hypothesis": hyp,
                "reference": ref,
                "model_size": self.model_size,
            },
        )


@dataclass
class WhisperCERMetric:
    """Sibling of `WhisperWERMetric` that reports CER as the primary
    score. CER is the preferred intelligibility metric for Turkish
    (agglutinative morphology — see module docstring).

    Set `shared_metric=` to an already-instantiated `WhisperWERMetric`
    to avoid loading the ~3 GB model twice; the two metrics will share
    a single decode pipeline. Default = independent load (useful when
    you only want CER and not WER).
    """

    name: str = "whisper_cer"
    model_size: str = "large-v3"
    device: str = "auto"
    shared_metric: WhisperWERMetric | None = None

    _model: object | None = None
    _lock: threading.Lock = threading.Lock()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lock", threading.Lock())
        if self.shared_metric is not None:
            # Reuse the WER metric's already-loaded model — avoids a
            # second whisper.load_model call (~3 GB download / VRAM).
            # We deliberately bind to the WER metric's `_model` slot:
            # if it hasn't loaded yet, the first `score()` call here
            # will trigger its loader, and subsequent calls share state.
            object.__setattr__(self, "_model", self.shared_metric._model)
            # Inherit model_size from the shared metric so reports
            # render consistent "model_size" detail across both.
            object.__setattr__(self, "model_size", self.shared_metric.model_size)
            object.__setattr__(self, "device", self.shared_metric.device)

    def _load(self) -> object:
        # If we have a shared WER metric, delegate to its loader so the
        # cached model lives in one place. This keeps the second decode
        # of a (WER + CER) pair from hitting whisper.load_model again.
        if self.shared_metric is not None:
            model = self.shared_metric._load()
            object.__setattr__(self, "_model", model)
            return model
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                import whisper  # lazy import — see WhisperWERMetric
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
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="lower",
                detail={"error": "empty_pcm"},
            )

        import numpy as np
        import soundfile as sf
        from jiwer import cer, compose, wer
        from jiwer.transforms import (
            ReduceToListOfListOfWords,
            RemoveMultipleSpaces,
            RemovePunctuation,
            Strip,
            ToLowerCase,
        )

        arr = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000:
            ratio = 16000 / sample_rate
            target_len = max(1, int(round(arr.size * ratio)))
            xp = np.linspace(0, arr.size - 1, arr.size, dtype=np.float64)
            x = np.linspace(0, arr.size - 1, target_len, dtype=np.float64)
            arr = np.interp(x, xp, arr).astype(np.float32)

        model = self._load()
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

        transform = compose([
            ToLowerCase(),
            RemovePunctuation(),
            RemoveMultipleSpaces(),
            Strip(),
            ReduceToListOfListOfWords(),
        ])
        cer_score = cer(
            ref, hyp,
            truth_transform=transform,
            hypothesis_transform=transform,
        )
        # WER computed alongside for the symmetric "both numbers in
        # detail" pattern — same rationale as WhisperWERMetric.score().
        wer_score = wer(
            ref, hyp,
            truth_transform=transform,
            hypothesis_transform=transform,
        )
        return MetricResult(
            metric_name=self.name,
            score=float(cer_score),
            direction="lower",
            detail={
                "wer": float(wer_score),
                "hypothesis": hyp,
                "reference": ref,
                "model_size": self.model_size,
            },
        )


# Don't register the real metrics at import time — the operator opts in
# via `scripts/eval_run.py --enable-real-metrics` or by calling
# `register_metric("whisper_wer", WhisperWERMetric())` (and / or
# `whisper_cer`) from their notebook / script. That way
# `import eval.metrics.whisper_wer` is safe on a no-GPU box and tests
# can register a stub.
