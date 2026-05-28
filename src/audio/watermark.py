"""AudioSeal watermark wrapper — ADR-13.

Two classes, both lazy-loading and thread-safe in the same pattern
established by the eval-metric modules (ADR-12):

  * WatermarkApplier   — embed a 16-bit payload into a PCM clip
  * WatermarkDetector  — recover the payload + detection probability

Both degrade gracefully when the `audioseal` package isn't installed
or the model fails to load. The worker treats a graceful-degrade
applier as "skip watermarking for this chunk" + emit a Prometheus
counter; the operator forensics endpoint treats a degrade-detector
as 503 (forensics MUST be honest about not knowing).

Upstream: https://github.com/facebookresearch/audioseal (MIT).
Install: `pip install neurovoice[watermark]` or
         `pip install audioseal>=0.2`.

AudioSeal supports 16/24/44.1/48 kHz. Clips at other sample rates
are linearly resampled to 16 kHz before watermarking; the output
sample rate matches the input.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger("neurovoice.audio.watermark")


@dataclass(frozen=True)
class WatermarkDetectionResult:
    """One detection probe over a clip. `probability` is AudioSeal's
    averaged per-sample watermark presence score in [0, 1]. `message`
    is the recovered 16-bit payload (0..65535) iff `probability` is
    above the threshold — None otherwise, signalling "we don't think
    this clip carries our watermark"."""

    probability: float
    message: int | None
    sample_rate_used: int
    duration_seconds: float
    detail: dict[str, Any] | None = None


def _resample_to(arr: Any, source_sr: int, target_sr: int) -> Any:
    """Linear-interp resample. Same approach as the eval metric
    modules — torch.nn.functional.interpolate would be faster but
    pulls torch into the resample path; numpy keeps the wrapper
    cheap when only watermark detection is called."""
    import numpy as np

    if source_sr == target_sr:
        return arr
    ratio = target_sr / source_sr
    target_len = max(1, int(round(arr.size * ratio)))
    xp = np.linspace(0, arr.size - 1, arr.size, dtype=np.float64)
    x = np.linspace(0, arr.size - 1, target_len, dtype=np.float64)
    return np.interp(x, xp, arr).astype(np.float32)


def _pcm16_to_float32(pcm_int16: bytes) -> Any:
    import numpy as np
    return np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32) / 32768.0


def _float32_to_pcm16(arr: Any) -> bytes:
    import numpy as np
    clipped = np.clip(arr, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


@dataclass
class WatermarkApplier:
    """Embed a 16-bit payload into PCM. Lazy-loads the AudioSeal
    generator on first use; subsequent calls are O(audio length).

    Construction is cheap — no model load until `watermark_pcm()` is
    called. This lets a worker boot when audioseal isn't installed
    and still answer non-watermarked requests; only voices with
    `watermark_enabled=True` AND `watermark_key_id IS NOT NULL` ever
    touch this code path.
    """

    model_name: str = "audioseal_wm_16bits"
    device: str = "auto"

    _model: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _import_error: str | None = None

    def is_available(self) -> bool:
        """True iff the model has loaded successfully or hasn't been
        probed yet. False once an import / load failure is cached."""
        return self._import_error is None

    def _load(self) -> Any:
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
                from audioseal import AudioSeal  # type: ignore[import-not-found]
            except ImportError as e:
                msg = (
                    "audioseal package not installed; "
                    "pip install neurovoice[watermark]"
                )
                logger.warning("%s — %s", msg, e)
                object.__setattr__(self, "_import_error", msg)
                return None
            try:
                logger.info(
                    "loading AudioSeal generator (model=%s device=%s)",
                    self.model_name, self.device,
                )
                model = AudioSeal.load_generator(self.model_name)
                if self.device != "auto":
                    model = model.to(self.device)
                else:
                    try:
                        import torch
                        if torch.cuda.is_available():
                            model = model.to("cuda")
                    except ImportError:
                        pass
            except Exception as e:  # noqa: BLE001 — degrade
                msg = f"AudioSeal load failed: {type(e).__name__}: {e}"
                logger.exception(msg)
                object.__setattr__(self, "_import_error", msg)
                return None
            object.__setattr__(self, "_model", model)
        return self._model

    def watermark_pcm(
        self, pcm_int16: bytes, sample_rate: int, *, message_bits: int,
    ) -> bytes:
        """Return PCM with the 16-bit `message_bits` embedded. On
        graceful degrade (model missing / load failed), returns the
        INPUT pcm unchanged — callers MUST emit a metric so operators
        see the gap; this module logs it but doesn't double-count.

        `message_bits` is 0..65535. The model embeds the bits inaudibly
        and the detector recovers them (AudioSeal default).
        """
        if not (0 <= message_bits <= 0xFFFF):
            raise ValueError(
                f"message_bits must be 0..65535; got {message_bits}"
            )
        model = self._load()
        if model is None:
            # Graceful degrade — return input unchanged so the synth
            # stream proceeds. Worker side counts the skip.
            return pcm_int16
        try:
            import numpy as np
            import torch

            arr_f32 = _pcm16_to_float32(pcm_int16)
            arr_16k = _resample_to(arr_f32, sample_rate, 16000)
            # AudioSeal expects (batch=1, channels=1, samples).
            wav = torch.from_numpy(arr_16k).reshape(1, 1, -1)
            device = next(model.parameters()).device
            wav = wav.to(device)
            # 16-bit payload as a (1, 16) tensor of {0, 1} bits.
            bits = [(message_bits >> i) & 1 for i in range(16)]
            msg = torch.tensor([bits], dtype=torch.int32, device=device)
            with torch.no_grad():
                watermark = model.get_watermark(
                    wav, sample_rate=16000, message=msg,
                )
                watermarked = (wav + watermark).clamp(-1.0, 1.0)
            out_arr = watermarked.squeeze().cpu().numpy().astype(np.float32)
            # Restore the caller's sample rate so downstream encoders
            # (mp3/opus/wav) keep working without rate-mismatch errors.
            out_arr = _resample_to(out_arr, 16000, sample_rate)
            return _float32_to_pcm16(out_arr)
        except Exception as e:  # noqa: BLE001 — degrade
            logger.exception(
                "AudioSeal watermarking failed; emitting unwatermarked PCM"
            )
            # Sentinel the failure so subsequent calls fast-skip.
            object.__setattr__(
                self, "_import_error",
                f"runtime: {type(e).__name__}: {e}",
            )
            return pcm_int16


@dataclass
class WatermarkDetector:
    """Recover the 16-bit payload (and presence probability) from a
    clip. Used by the operator forensics endpoint and (optionally)
    by ADR-12 eval as a 4th metric in future.

    Detection threshold defaults to 0.5; lower values surface more
    "maybe" results but raise false-positive risk. The forensics
    endpoint exposes the threshold so an operator can run a strict
    audit (e.g. 0.9) when needed.
    """

    model_name: str = "audioseal_detector_16bits"
    device: str = "auto"
    detection_threshold: float = 0.5

    _model: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _import_error: str | None = None

    def is_available(self) -> bool:
        return self._import_error is None

    def _load(self) -> Any:
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
                from audioseal import AudioSeal  # type: ignore[import-not-found]
            except ImportError as e:
                msg = (
                    "audioseal package not installed; "
                    "pip install neurovoice[watermark]"
                )
                logger.warning("%s — %s", msg, e)
                object.__setattr__(self, "_import_error", msg)
                return None
            try:
                logger.info(
                    "loading AudioSeal detector (model=%s device=%s)",
                    self.model_name, self.device,
                )
                model = AudioSeal.load_detector(self.model_name)
                if self.device != "auto":
                    model = model.to(self.device)
                else:
                    try:
                        import torch
                        if torch.cuda.is_available():
                            model = model.to("cuda")
                    except ImportError:
                        pass
            except Exception as e:  # noqa: BLE001
                msg = f"AudioSeal detector load failed: {type(e).__name__}: {e}"
                logger.exception(msg)
                object.__setattr__(self, "_import_error", msg)
                return None
            object.__setattr__(self, "_model", model)
        return self._model

    def detect(
        self, pcm_int16: bytes, sample_rate: int,
    ) -> WatermarkDetectionResult:
        """Probe the clip for a NeuroVoice watermark. Returns
        `WatermarkDetectionResult` with `probability` in [0, 1] and
        the decoded 16-bit `message` (or None if below threshold).

        Raises RuntimeError on graceful-degrade — forensics callers
        MUST get an honest "I don't know" rather than a silent miss.
        """
        model = self._load()
        if model is None:
            raise RuntimeError(
                self._import_error or "AudioSeal detector unavailable"
            )
        import numpy as np
        import torch

        arr_f32 = _pcm16_to_float32(pcm_int16)
        duration = arr_f32.size / max(sample_rate, 1)
        arr_16k = _resample_to(arr_f32, sample_rate, 16000)
        wav = torch.from_numpy(arr_16k).reshape(1, 1, -1)
        device = next(model.parameters()).device
        wav = wav.to(device)
        with torch.no_grad():
            # AudioSeal detector returns (per-sample probability tensor,
            # decoded message bits tensor).
            result, message_tensor = model.detect_watermark(
                wav, sample_rate=16000,
            )
        # `result` shape: (batch=1, samples). Mean across samples gives
        # the clip-level probability AudioSeal reports as its headline.
        probability = float(result.mean().item())
        if probability >= self.detection_threshold:
            # `message_tensor` shape: (batch=1, 16) of {0, 1}. Pack LSB-first.
            bits = message_tensor.squeeze(0).cpu().to(torch.int32).tolist()
            message = 0
            for i, b in enumerate(bits[:16]):
                if int(b) & 1:
                    message |= 1 << i
        else:
            message = None
        return WatermarkDetectionResult(
            probability=probability,
            message=message,
            sample_rate_used=16000,
            duration_seconds=float(duration),
            detail={
                "model_name": self.model_name,
                "threshold": self.detection_threshold,
            },
        )


# Module-level singletons — lazy-loaded on first use. Callers reuse
# the same instance across requests to keep the model in memory.
# Worker boot warmup can call `_singleton_applier()._load()` to front-
# load the cost; ADR-13 leaves that wiring to the worker module.
_APPLIER: WatermarkApplier | None = None
_DETECTOR: WatermarkDetector | None = None


def get_applier() -> WatermarkApplier:
    global _APPLIER
    if _APPLIER is None:
        _APPLIER = WatermarkApplier()
    return _APPLIER


def get_detector() -> WatermarkDetector:
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = WatermarkDetector()
    return _DETECTOR
