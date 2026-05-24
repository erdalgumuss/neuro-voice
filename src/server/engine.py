"""Synth engine — VoxCPM2 backend with per-voice reference audio.

VoxCPM2 (Apache 2.0, OpenBMB) is the canonical base model for NQAI Voice v0.1.
The engine is shaped as a `BaseSynthEngine` protocol so future swaps (e.g. a
Türkçe-SFT'd checkpoint or a tenant-specific LoRA) drop in without touching
the API layer.

Key VoxCPM2 traits we lean on:
    * Native chunk streaming via `model.generate_streaming(...)` — no need
      to glue per-sentence pieces ourselves, the model already paces it.
    * Voice cloning via `reference_wav_path` — 16 kHz mono WAV preferred.
    * Built-in TN (`normalize=True`) we **disable**, because our Türkçe
      frontend (`src/frontend/`) handles abbreviations, numerals, code-mix
      and apostrophe suffixes more reliably for our dar domain.
    * 48 kHz output via AudioVAE V2.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from frontend import normalize_text, segment_sentences
from registry import Voice

logger = logging.getLogger("nqai_voice.engine")


@dataclass
class SynthChunk:
    """One synthesized sentence (PCM int16, mono, target_sr)."""
    pcm_int16: bytes
    sample_rate: int
    sentence_index: int
    sentence_text: str
    elapsed_ms: float


@dataclass
class SynthResult:
    pcm_int16: bytes
    sample_rate: int
    duration_seconds: float
    elapsed_seconds: float
    sentence_count: int


class BaseSynthEngine(Protocol):
    sample_rate: int

    def warmup(self) -> None: ...
    def synthesize_stream(
        self,
        *,
        text: str,
        voice: Voice,
        reference_path: Path,
        language_id: str = "tr",
    ) -> Iterator[SynthChunk]: ...
    def synthesize(
        self,
        *,
        text: str,
        voice: Voice,
        reference_path: Path,
        language_id: str = "tr",
    ) -> SynthResult: ...


def _resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _float_to_pcm16(wav: np.ndarray) -> bytes:
    wav = np.clip(wav, -1.0, 1.0)
    return (wav * 32767.0).astype(np.int16).tobytes()


def pcm16_to_wav_bytes(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# Engine-level default knobs. Tune per voice in the manifest later.
DEFAULT_CFG_VALUE = 2.0
DEFAULT_INFERENCE_TIMESTEPS = 10
DEFAULT_SAMPLE_RATE = 48000


class VoxCPM2Engine:
    """VoxCPM2 adapter.

    A single model instance lives in memory; concurrent `generate()` calls
    are serialized through `_inference_lock` because the underlying diffusion
    AR backbone is not safe for parallel forward passes. Production
    concurrency is added later via uvicorn workers or Triton — each worker
    owning its own model instance.
    """

    sample_rate: int

    def __init__(
        self,
        model_id: str,
        device: str = "auto",
        *,
        cfg_value: float = DEFAULT_CFG_VALUE,
        inference_timesteps: int = DEFAULT_INFERENCE_TIMESTEPS,
        load_denoiser: bool = False,
    ) -> None:
        self._model_id = model_id
        self._device = _resolve_device(device)
        self._cfg_value = cfg_value
        self._inference_timesteps = inference_timesteps
        self._load_denoiser = load_denoiser
        self._model = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self.sample_rate = DEFAULT_SAMPLE_RATE

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            from voxcpm import VoxCPM

            logger.info("loading %s on %s", self._model_id, self._device)
            t0 = time.time()
            self._model = VoxCPM.from_pretrained(
                self._model_id,
                load_denoiser=self._load_denoiser,
            )
            # VoxCPM2 advertises sample rate on the inner tts_model
            inner_sr = getattr(getattr(self._model, "tts_model", None), "sample_rate", None)
            if inner_sr:
                self.sample_rate = int(inner_sr)
            logger.info(
                "model ready in %.1fs (sr=%d Hz, device=%s, cfg=%.2f, steps=%d)",
                time.time() - t0,
                self.sample_rate,
                self._device,
                self._cfg_value,
                self._inference_timesteps,
            )

    def warmup(self) -> None:
        self._load()

    # ----- internals ----------------------------------------------------

    def _generate_one(self, text: str, reference_path: Path) -> np.ndarray:
        """Single synthesis call. Returns float32 mono numpy at self.sample_rate."""
        with self._inference_lock:
            wav = self._model.generate(
                text=text,
                reference_wav_path=str(reference_path),
                cfg_value=self._cfg_value,
                inference_timesteps=self._inference_timesteps,
                # Our Turkish frontend already normalized the text — keep VoxCPM2's
                # built-in TN off so it doesn't double-rewrite numerals/abbrs.
                normalize=False,
                denoise=False,
                retry_badcase=True,
            )
        # VoxCPM2 returns a 1D numpy array; defensive in case it's torch
        if hasattr(wav, "detach"):
            wav = wav.detach().cpu().numpy()
        wav = np.asarray(wav).reshape(-1).astype(np.float32, copy=False)
        return wav

    # ----- public API ---------------------------------------------------

    def synthesize_stream(
        self,
        *,
        text: str,
        voice: Voice,
        reference_path: Path,
        language_id: str = "tr",
    ) -> Iterator[SynthChunk]:
        """Yield one `SynthChunk` per logical sentence.

        We segment the text ourselves so the client gets one chunk per
        sentence (useful for UI captions / token alignment), and so we can
        inject 200 ms of silence at the segment boundary.

        VoxCPM2's own `generate_streaming` is great for real-time playback
        of a single utterance, but for multi-sentence responses we still
        prefer sentence-level boundaries to keep prosody coherent.
        """
        self._load()
        if not reference_path.is_file():
            raise FileNotFoundError(
                f"reference audio for {voice.voice_id} missing: {reference_path}"
            )

        normalized = normalize_text(text)
        segments = segment_sentences(normalized)
        if not segments:
            return

        for idx, segment in enumerate(segments):
            t0 = time.time()
            wav_np = self._generate_one(segment, reference_path)
            elapsed_ms = (time.time() - t0) * 1000.0
            yield SynthChunk(
                pcm_int16=_float_to_pcm16(wav_np),
                sample_rate=self.sample_rate,
                sentence_index=idx,
                sentence_text=segment,
                elapsed_ms=elapsed_ms,
            )

    def synthesize(
        self,
        *,
        text: str,
        voice: Voice,
        reference_path: Path,
        language_id: str = "tr",
    ) -> SynthResult:
        t0 = time.time()
        pcm_parts: list[bytes] = []
        sr = self.sample_rate
        silence = b"\x00\x00" * int(0.2 * sr)  # 200 ms inter-segment pad
        count = 0
        for i, chunk in enumerate(
            self.synthesize_stream(
                text=text, voice=voice, reference_path=reference_path, language_id=language_id
            )
        ):
            if i > 0:
                pcm_parts.append(silence)
            pcm_parts.append(chunk.pcm_int16)
            sr = chunk.sample_rate
            count += 1
        pcm_all = b"".join(pcm_parts)
        duration = len(pcm_all) / (2 * sr) if sr else 0.0
        return SynthResult(
            pcm_int16=pcm_all,
            sample_rate=sr,
            duration_seconds=duration,
            elapsed_seconds=time.time() - t0,
            sentence_count=count,
        )


_engine_singleton: BaseSynthEngine | None = None
_engine_singleton_lock = threading.Lock()


def get_engine(model_id: str, device: str = "auto") -> BaseSynthEngine:
    global _engine_singleton
    if _engine_singleton is not None:
        return _engine_singleton
    with _engine_singleton_lock:
        if _engine_singleton is None:
            _engine_singleton = VoxCPM2Engine(model_id=model_id, device=device)
        return _engine_singleton
