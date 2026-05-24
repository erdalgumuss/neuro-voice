"""Synth engine — Chatterbox Multilingual backend with per-voice conditional cache.

The engine is intentionally adapter-shaped so VoxCPM2 (or any future base
model) can be dropped in by implementing `BaseSynthEngine`.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

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


class ChatterboxEngine:
    """Chatterbox Multilingual adapter.

    The engine keeps a single model instance in memory; concurrent requests
    are serialized through `_inference_lock` because the underlying T3 model
    is not thread-safe for parallel generate() calls.
    """

    sample_rate: int

    def __init__(self, model_id: str, device: str = "auto") -> None:
        self._model_id = model_id
        self._device = _resolve_device(device)
        self._model = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self.sample_rate = 24000  # overwritten after load

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS

            logger.info("loading %s on %s", self._model_id, self._device)
            t0 = time.time()
            self._model = ChatterboxMultilingualTTS.from_pretrained(device=self._device)
            self.sample_rate = int(self._model.sr)
            logger.info(
                "model ready in %.1fs (sr=%d Hz, device=%s)",
                time.time() - t0,
                self.sample_rate,
                self._device,
            )

    def warmup(self) -> None:
        self._load()

    def synthesize_stream(
        self,
        *,
        text: str,
        voice: Voice,
        reference_path: Path,
        language_id: str = "tr",
    ) -> Iterator[SynthChunk]:
        self._load()
        if not reference_path.is_file():
            raise FileNotFoundError(f"reference audio for {voice.voice_id} missing: {reference_path}")

        normalized = normalize_text(text)
        segments = segment_sentences(normalized)
        if not segments:
            return

        for idx, segment in enumerate(segments):
            t0 = time.time()
            with self._inference_lock:
                wav_tensor = self._model.generate(
                    segment,
                    language_id=language_id,
                    audio_prompt_path=str(reference_path),
                )
            # Chatterbox returns torch.Tensor shape (1, T) or (T,)
            wav_np = wav_tensor.detach().cpu().numpy()
            if wav_np.ndim == 2:
                wav_np = wav_np[0]
            pcm = _float_to_pcm16(wav_np)
            elapsed_ms = (time.time() - t0) * 1000.0
            yield SynthChunk(
                pcm_int16=pcm,
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
            _engine_singleton = ChatterboxEngine(model_id=model_id, device=device)
        return _engine_singleton
