"""SECS — Speaker Encoder Cosine Similarity (voice clone fidelity).

Backend: `microsoft/wavlm-base-plus-sv` — Microsoft's WavLM model
fine-tuned on VoxCeleb1 with an X-Vector head + Additive Margin
Softmax. The model emits a normalized speaker embedding per audio
clip; comparing the embedding of the TTS-generated audio against
the embedding of the voice's REFERENCE audio (the clone source)
via cosine similarity yields a fidelity score in [-1, 1] (typically
0.0–1.0 for any pair of normal speech).

  https://huggingface.co/microsoft/wavlm-base-plus-sv

Score interpretation (informal industry rules of thumb):
  * < 0.40   — unrelated speakers (random pair baseline ~0.3)
  * 0.40-0.60 — same speaker but distorted / different recording
  * 0.60-0.75 — convincing clone
  * > 0.75   — high-fidelity clone

These thresholds vary by domain. Treat absolute scores cautiously
on non-English voices (WavLM-base-plus-sv was fine-tuned on
VoxCeleb1, which is English-dominant); use SECS as a relative drift
signal across re-runs of the same voice.

Why per-voice construction:
  The `Metric` protocol scores one clip at a time without context
  about which voice produced it. SECS uniquely needs the voice's
  reference audio (per-voice, not per-sentence) to compute its
  cosine target. We pass the reference at construction time —
  `SECSMetric.from_reference_pcm(pcm, sample_rate)` — and the
  metric pre-computes the target embedding once. The eval CLI
  registers a fresh SECSMetric for each voice it evaluates.

Why not extend the Metric protocol with a reference_audio kwarg:
  ADR-12 considered + rejected. Mutating the protocol forces every
  existing implementation (`WhisperWERMetric`, `UTMOSv2Metric`) to
  accept-and-ignore a new kwarg. Construction-time injection keeps
  the protocol stable.

Heavy imports (torch + transformers) live inside `_load()` so the
package always loads in CI / no-GPU dev environments.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from . import MetricResult

logger = logging.getLogger("neurovoice.eval.secs")


def _pcm_to_float32(pcm_int16: bytes, sample_rate: int) -> tuple[Any, int]:
    """int16 PCM bytes → float32 numpy array at 16 kHz.

    Returns (numpy_array, 16000). Resamples via linear interp if
    sample_rate != 16000 — same approach as the other metrics. WavLM
    expects 16 kHz mono.
    """
    import numpy as np

    arr = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0
    if sample_rate != 16000:
        ratio = 16000 / sample_rate
        target_len = max(1, int(round(arr.size * ratio)))
        xp = np.linspace(0, arr.size - 1, arr.size, dtype=np.float64)
        x = np.linspace(0, arr.size - 1, target_len, dtype=np.float64)
        arr = np.interp(x, xp, arr).astype(np.float32)
    return arr, 16000


@dataclass
class SECSMetric:
    """WavLM-based speaker similarity. Construct per voice with the
    voice's reference audio loaded in memory; the target embedding is
    computed once on first `score()` and reused across all clips.

    Use `SECSMetric.from_reference_pcm(...)` instead of the dataclass
    constructor — the factory handles the eager state setup.
    """

    name: str = "secs"
    model_id: str = "microsoft/wavlm-base-plus-sv"
    device: str = "auto"

    # Reference audio loaded in memory. Float32 mono at 16 kHz (the
    # factory below normalises whatever sample rate the operator
    # passes in).
    _reference_pcm: Any = None
    _reference_sample_rate: int = 16000
    _reference_embedding: Any = None
    _model: Any = None
    _feature_extractor: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _import_error: str | None = None

    @classmethod
    def from_reference_pcm(
        cls,
        pcm_int16: bytes,
        sample_rate: int,
        *,
        device: str = "auto",
        model_id: str = "microsoft/wavlm-base-plus-sv",
    ) -> "SECSMetric":
        """Build a SECSMetric bound to one voice's reference audio.
        The audio is normalised to float32 mono 16 kHz here so every
        subsequent `score()` call avoids the resample.
        """
        arr, sr = _pcm_to_float32(pcm_int16, sample_rate)
        return cls(
            device=device,
            model_id=model_id,
            _reference_pcm=arr,
            _reference_sample_rate=sr,
        )

    def _load(self) -> bool:
        """Lazy-load the WavLM model + feature extractor. Returns
        True on success; False on graceful degrade (logs the reason).
        Sentinel-cached: a failed load stays failed for the instance.
        """
        if self._model is not None and self._feature_extractor is not None:
            return True
        if self._import_error is not None:
            return False
        with self._lock:
            if self._model is not None and self._feature_extractor is not None:
                return True
            if self._import_error is not None:
                return False
            try:
                import torch
                from transformers import (
                    AutoFeatureExtractor,
                    WavLMForXVector,
                )
            except ImportError as e:
                msg = (
                    "WavLM speaker-verification deps not installed; "
                    "ensure `transformers` and `torch` are present "
                    "(they are pinned in pyproject's main deps)."
                )
                logger.warning("%s — %s", msg, e)
                self._import_error = msg
                return False
            logger.info(
                "loading SECS speaker encoder (model=%s device=%s)",
                self.model_id, self.device,
            )
            try:
                feature_extractor = AutoFeatureExtractor.from_pretrained(self.model_id)
                model = WavLMForXVector.from_pretrained(self.model_id)
            except Exception as e:  # noqa: BLE001 — surface as nan
                msg = f"WavLM load failed: {type(e).__name__}: {e}"
                logger.exception(msg)
                self._import_error = msg
                return False
            if self.device != "auto":
                model = model.to(self.device)
            elif torch.cuda.is_available():
                model = model.to("cuda")
            model.eval()
            self._feature_extractor = feature_extractor
            self._model = model
        return True

    def _embed(self, waveform_f32: Any) -> Any:
        """Run the WavLM x-vector head on a single waveform → return
        a normalized 1D torch tensor embedding."""
        import torch

        with torch.no_grad():
            inputs = self._feature_extractor(
                [waveform_f32],
                sampling_rate=16000,
                return_tensors="pt",
                padding=True,
            )
            device = next(self._model.parameters()).device
            input_values = inputs["input_values"].to(device)
            attention_mask = inputs.get("attention_mask")
            if attention_mask is not None:
                attention_mask = attention_mask.to(device)
            output = self._model(
                input_values, attention_mask=attention_mask,
            )
            # WavLMForXVector's `embeddings` is the raw X-vector
            # projection — the AMSoftmax head normalizes during
            # training, but inference-time output is NOT L2-normalized
            # (typical norm 8-15). Cosine similarity downstream requires
            # explicit normalization, otherwise the dot product is a
            # scaled inner product that can exceed [-1, 1] and the
            # clamp at score() masks a real correctness bug.
            emb = output.embeddings.squeeze(0)
            return torch.nn.functional.normalize(emb, dim=-1)

    def _target_embedding(self) -> Any:
        """Cached reference embedding. Computed on first use; subsequent
        score() calls reuse it across all clips for this voice."""
        if self._reference_embedding is not None:
            return self._reference_embedding
        with self._lock:
            if self._reference_embedding is not None:
                return self._reference_embedding
            emb = self._embed(self._reference_pcm)
            self._reference_embedding = emb
        return emb

    def score(
        self,
        pcm_int16: bytes,
        sample_rate: int,
        *,
        reference_text: str,
    ) -> MetricResult:
        _ = reference_text  # SECS is text-free (speaker similarity only)
        if not pcm_int16:
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="higher",
                detail={"error": "empty_pcm"},
            )
        if self._reference_pcm is None:
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="higher",
                detail={"error": "no_reference_audio_bound"},
            )
        if not self._load():
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="higher",
                detail={"error": self._import_error or "wavlm unavailable"},
            )

        try:
            import torch

            clip_f32, _ = _pcm_to_float32(pcm_int16, sample_rate)
            target = self._target_embedding()
            clip = self._embed(clip_f32)
            # Both embeddings are L2-normalized by WavLMForXVector;
            # cosine = inner product. Clamp to [-1, 1] for numerical
            # safety (tiny float drift past 1.0 surfaces unfriendly).
            cosine = float(torch.dot(target, clip).clamp_(-1.0, 1.0).item())
        except Exception as e:  # noqa: BLE001 — degrade per ADR-12
            logger.exception("SECS scoring failed")
            return MetricResult(
                metric_name=self.name,
                score=float("nan"),
                direction="higher",
                detail={"error": f"{type(e).__name__}: {e}"},
            )
        return MetricResult(
            metric_name=self.name,
            score=cosine,
            direction="higher",
            detail={"model_id": self.model_id},
        )
