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

import json
import logging
import os
import threading
import time
from collections import OrderedDict
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
        engine_overrides: dict[str, float | int] | None = None,
        request_meta: dict[str, object] | None = None,
    ) -> Iterator[SynthChunk]: ...
    def synthesize(
        self,
        *,
        text: str,
        voice: Voice,
        reference_path: Path,
        language_id: str = "tr",
        engine_overrides: dict[str, float | int] | None = None,
        request_meta: dict[str, object] | None = None,
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


# PCM helpers come from the shared audio package. The engine itself
# produces PCM int16 bytes; gateway sync proxy and result-stream
# consumers do their own WAV assembly via `audio.wav.pcm16_to_wav_bytes`.
from audio.wav import float_to_pcm16_bytes as _float_to_pcm16  # noqa: E402

# Engine-level default knobs. Tune per voice in the manifest later.
DEFAULT_CFG_VALUE = 2.0
DEFAULT_INFERENCE_TIMESTEPS = 10
DEFAULT_SAMPLE_RATE = 48000

# LoRA adapter cache budget — bounded LRU. Each loaded VoxCPM2 instance
# (base + adapter) consumes ~4 GB VRAM on bfloat16; 3 active adapters fit
# on an L4 (24 GB) with headroom for batched inference. Adapters beyond
# this threshold get evicted in least-recently-used order. Override via
# NQAI_LORA_CACHE_SIZE if you serve more voices on a single worker.
DEFAULT_LORA_CACHE_SIZE = int(os.environ.get("NQAI_LORA_CACHE_SIZE", "3"))


@dataclass(frozen=True)
class LoRAAdapterSpec:
    """Local VoxCPM2 LoRA adapter loaded alongside the base model."""
    path: Path
    config_path: Path | None = None

    @property
    def cache_key(self) -> tuple[str, str | None]:
        return (str(self.path), str(self.config_path) if self.config_path else None)


def _expand_runtime_path(raw: str | Path) -> Path:
    return Path(os.path.expandvars(str(raw))).expanduser().resolve()


def _lora_from_mapping(raw: dict | None) -> LoRAAdapterSpec | None:
    if not raw:
        return None
    adapter_type = str(raw.get("type", "lora")).lower()
    if adapter_type != "lora":
        raise ValueError(f"unsupported adapter type '{adapter_type}'")
    raw_path = raw.get("path") or raw.get("lora_path") or raw.get("uri")
    if not raw_path:
        raise ValueError("lora adapter requires 'path'")
    raw_config_path = raw.get("config_path") or raw.get("lora_config_path")
    return LoRAAdapterSpec(
        path=_expand_runtime_path(raw_path),
        config_path=_expand_runtime_path(raw_config_path) if raw_config_path else None,
    )


def _read_lora_config(adapter: LoRAAdapterSpec):
    config_path = adapter.config_path or adapter.path / "lora_config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"LoRA config missing: {config_path}")

    try:
        from voxcpm.model.voxcpm import LoRAConfig
    except Exception:
        from voxcpm.model.voxcpm2 import LoRAConfig

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    payload = raw.get("lora_config", raw)
    return LoRAConfig(**payload)


class VoxCPM2Engine:
    """VoxCPM2 adapter.

    A single model instance lives in memory; concurrent `generate()` calls
    are serialized through `_inference_lock` because the underlying diffusion
    AR backbone is not safe for parallel forward passes. Production
    concurrency is added later via uvicorn workers or Triton — each worker
    owning its own model instance.
    """

    sample_rate: int

    # Cache key shape: (model_id, (adapter_path, adapter_config_path) | None)
    _CacheKey = tuple[str, tuple[str, str | None] | None]

    def __init__(
        self,
        model_id: str,
        device: str = "auto",
        *,
        cfg_value: float = DEFAULT_CFG_VALUE,
        inference_timesteps: int = DEFAULT_INFERENCE_TIMESTEPS,
        load_denoiser: bool = False,
        lora_path: Path | None = None,
        lora_config_path: Path | None = None,
        optimize: bool = False,
        cache_size: int = DEFAULT_LORA_CACHE_SIZE,
        hf_revision: str = "main",
    ) -> None:
        if cache_size < 1:
            raise ValueError("cache_size must be >= 1 (base model always cached)")
        self._model_id = model_id
        # MLOps PR #1 — pin the HuggingFace revision; loaded base model
        # always reflects THIS specific revision. Unpinned `main` only
        # for local dev; production sets a commit SHA so upstream churn
        # cannot silently change inference output.
        self._hf_revision = hf_revision
        if hf_revision in (None, "", "main"):
            logger.warning(
                "VoxCPM2 model_id=%s loaded WITHOUT a pinned hf_revision "
                "(value=%r). Set NQAI_MODEL_HF_REVISION to a commit SHA "
                "for reproducible production deploys.",
                model_id, hf_revision,
            )
        self._device = _resolve_device(device)
        self._cfg_value = cfg_value
        self._inference_timesteps = inference_timesteps
        self._load_denoiser = load_denoiser
        self._optimize = optimize
        self._cache_size = cache_size
        self._default_adapter = (
            LoRAAdapterSpec(path=lora_path, config_path=lora_config_path)
            if lora_path
            else None
        )
        # LRU: most recent at the end. dict-of-models, bounded by cache_size.
        self._models: OrderedDict[VoxCPM2Engine._CacheKey, object] = OrderedDict()
        self._evictions_total = 0  # exposed for tests + metrics (Faz C)
        self._model = None  # compatibility hook used by /health and old tests
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self.sample_rate = DEFAULT_SAMPLE_RATE

    # MLOps PR #1 — public accessors so the pipeline can record
    # which model + revision produced this row without reaching into
    # `_model_id` / `_hf_revision` directly.
    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def hf_revision(self) -> str:
        return self._hf_revision

    def _load(self) -> None:
        if self._models:
            return
        self._model_for_adapter(self._default_adapter)

    def warmup(self) -> None:
        self._load()

    def warmup_voice(self, voice: Voice) -> None:
        """Faz B.5 Dalga 1.3 — eager-load a specific voice's adapter
        into the cache so the FIRST inference for that voice doesn't
        pay cold-load latency.

        Called by the worker boot path for every voice listed in
        `NQAI_WORKER_WARMUP_VOICES`. The cold-load metric fires from
        inside `_model_for_adapter` so warmups show up in the same
        histogram as in-band cache misses — operators see both."""
        self._load()  # base model first
        self._model_for_adapter(
            self._adapter_for_voice(voice),
            voice_id=voice.voice_id,
        )

    # ----- internals ----------------------------------------------------

    def _adapter_for_voice(self, voice: Voice) -> LoRAAdapterSpec | None:
        return _lora_from_mapping(voice.adapter) or self._default_adapter

    def _evict_oldest_locked(self) -> None:
        """Drop the LRU entry. Caller holds `_load_lock`."""
        if not self._models:
            return
        evicted_key, evicted_model = self._models.popitem(last=False)
        self._evictions_total += 1
        logger.info(
            "LoRA cache eviction (LRU): adapter=%s (cache_size=%d, evictions_total=%d)",
            evicted_key[1] or "base",
            self._cache_size,
            self._evictions_total,
        )
        # Best-effort VRAM release. The model object goes out of scope but
        # CUDA caching allocator holds the freed blocks until empty_cache().
        del evicted_model
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            # torch import or empty_cache failure is not fatal — Python GC
            # will reclaim eventually; we just lose a tick of VRAM headroom.
            pass

    def _model_for_adapter(
        self,
        adapter: LoRAAdapterSpec | None,
        *,
        voice_id: str | None = None,
    ):
        """Return a (possibly cached) base+adapter model.

        `voice_id` is the catalog slug used to label cold-load metrics.
        When omitted (background warmup paths that don't have a voice
        slug) the metric is labelled `_base_` so the cardinality stays
        bounded and operators can still see un-attributed cold loads."""
        key = (self._model_id, adapter.cache_key if adapter else None)
        cached = self._models.get(key)
        if cached is not None:
            # Mark as most-recently-used.
            self._models.move_to_end(key)
            self._model = cached  # keep /health pointer fresh
            return cached
        with self._load_lock:
            cached = self._models.get(key)
            if cached is not None:
                self._models.move_to_end(key)
                self._model = cached
                return cached

            # Make room before loading the new model so peak VRAM stays bounded.
            while len(self._models) >= self._cache_size:
                self._evict_oldest_locked()

            from voxcpm import VoxCPM

            kwargs = {
                "load_denoiser": self._load_denoiser,
                "optimize": self._optimize,
                "device": self._device,
            }
            adapter_label = "base"
            if adapter is not None:
                if not adapter.path.exists():
                    raise FileNotFoundError(f"LoRA adapter path missing: {adapter.path}")
                kwargs["lora_config"] = _read_lora_config(adapter)
                kwargs["lora_weights_path"] = str(adapter.path)
                adapter_label = str(adapter.path)

            logger.info(
                "loading %s on %s (adapter=%s, cache=%d/%d)",
                self._model_id,
                self._device,
                adapter_label,
                len(self._models),
                self._cache_size,
            )
            t0 = time.time()
            # MLOps PR #1 — pin the HuggingFace revision so two workers
            # booting at different times against the same model_id load
            # the EXACT same weights. `revision` accepts a branch name,
            # tag, or commit SHA; we recommend SHA in production envs.
            if self._hf_revision and self._hf_revision != "main":
                kwargs["revision"] = self._hf_revision
            model = VoxCPM.from_pretrained(self._model_id, **kwargs)
            inner_sr = getattr(getattr(model, "tts_model", None), "sample_rate", None)
            if inner_sr:
                self.sample_rate = int(inner_sr)
            self._models[key] = model
            self._model = model
            duration = time.time() - t0
            logger.info(
                "model ready in %.1fs (sr=%d Hz, device=%s, cfg=%.2f, steps=%d, adapter=%s)",
                duration,
                self.sample_rate,
                self._device,
                self._cfg_value,
                self._inference_timesteps,
                adapter_label,
            )
            # Faz B.5 Dalga 1.3 — cold-load metric. Label voice=_base_
            # for the no-voice warmup path so the series stays bounded
            # (catalog voice slugs + "_base_" is the full label domain).
            try:
                from observability import WORKER_COLD_LOAD_SECONDS
                WORKER_COLD_LOAD_SECONDS.labels(
                    voice=voice_id or "_base_",
                ).observe(duration)
            except Exception:
                logger.exception(
                    "cold-load metric emission failed for adapter=%s — ignoring",
                    adapter_label,
                )
            return model

    def _engine_params_for_voice(
        self,
        voice: Voice,
        *,
        overrides: dict[str, float | int] | None = None,
    ) -> tuple[float, int]:
        """Resolve cfg_value + inference_timesteps with precedence:
            explicit `overrides` (request-level: model_id preset OR
                                  explicit params)
            > voice.engine_params (catalog-level default)
            > engine constructor default
        """
        base = voice.engine_params or {}
        if overrides:
            # Merge — request-level wins per-key.
            base = {**base, **overrides}
        cfg_value = float(base.get("cfg_value", self._cfg_value))
        inference_timesteps = int(
            base.get(
                "inference_timesteps",
                base.get("timesteps", self._inference_timesteps),
            )
        )
        return cfg_value, inference_timesteps

    def _generate_one(
        self,
        text: str,
        voice: Voice,
        reference_path: Path,
        *,
        engine_overrides: dict[str, float | int] | None = None,
    ) -> np.ndarray:
        """Single synthesis call. Returns float32 mono numpy at self.sample_rate."""
        model = self._model_for_adapter(
            self._adapter_for_voice(voice),
            voice_id=voice.voice_id,
        )
        cfg_value, inference_timesteps = self._engine_params_for_voice(
            voice, overrides=engine_overrides,
        )
        with self._inference_lock:
            wav = model.generate(
                text=text,
                reference_wav_path=str(reference_path),
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
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
        engine_overrides: dict[str, float | int] | None = None,
        request_meta: dict[str, object] | None = None,
    ) -> Iterator[SynthChunk]:
        """Yield one `SynthChunk` per logical sentence.

        We segment the text ourselves so the client gets one chunk per
        sentence (useful for UI captions / token alignment), and so we can
        inject 200 ms of silence at the segment boundary.

        VoxCPM2's own `generate_streaming` is great for real-time playback
        of a single utterance, but for multi-sentence responses we still
        prefer sentence-level boundaries to keep prosody coherent.

        `engine_overrides` carries per-request `cfg_value` and/or
        `inference_timesteps` overrides (e.g. resolved from a `model_id`
        preset at the worker pipeline). See `server.models` for the
        registry.

        `request_meta` (Faz B.5 Dalga 2.6) bundles vendor-parity per-
        request hints that aren't engine knobs:
          * `seed`              — best-effort torch RNG seed
          * `pronunciation_dict`— per-request Turkish-frontend overrides
          * `previous_text`     — forward-compat prosody hint (no-op today)
          * `next_text`         — forward-compat prosody hint (no-op today)
        Unknown keys are ignored so the wire format can evolve forward.
        """
        self._load()
        if not reference_path.is_file():
            raise FileNotFoundError(
                f"reference audio for {voice.voice_id} missing: {reference_path}"
            )

        meta = request_meta or {}
        pron_dict = meta.get("pronunciation_dict")
        if pron_dict is not None and not isinstance(pron_dict, dict):
            pron_dict = None  # defensive — wire-format drift shouldn't crash
        seed_val = meta.get("seed")
        if seed_val is not None:
            try:
                import torch
                torch.manual_seed(int(seed_val))
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(int(seed_val))
            except Exception:  # noqa: BLE001 — best-effort only
                logger.exception(
                    "torch seed=%s could not be applied; continuing", seed_val,
                )

        normalized = normalize_text(text, pronunciation_dict=pron_dict)
        segments = segment_sentences(normalized)
        if not segments:
            return

        for idx, segment in enumerate(segments):
            t0 = time.time()
            wav_np = self._generate_one(
                segment, voice, reference_path,
                engine_overrides=engine_overrides,
            )
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
        engine_overrides: dict[str, float | int] | None = None,
        request_meta: dict[str, object] | None = None,
    ) -> SynthResult:
        t0 = time.time()
        pcm_parts: list[bytes] = []
        sr = self.sample_rate
        silence = b"\x00\x00" * int(0.2 * sr)  # 200 ms inter-segment pad
        count = 0
        for i, chunk in enumerate(
            self.synthesize_stream(
                text=text, voice=voice, reference_path=reference_path,
                language_id=language_id, engine_overrides=engine_overrides,
                request_meta=request_meta,
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


def get_engine(
    model_id: str,
    device: str = "auto",
    *,
    lora_path: Path | None = None,
    lora_config_path: Path | None = None,
    cfg_value: float = DEFAULT_CFG_VALUE,
    inference_timesteps: int = DEFAULT_INFERENCE_TIMESTEPS,
    optimize: bool = False,
    cache_size: int = DEFAULT_LORA_CACHE_SIZE,
    hf_revision: str = "main",
) -> BaseSynthEngine:
    global _engine_singleton
    if _engine_singleton is not None:
        return _engine_singleton
    with _engine_singleton_lock:
        if _engine_singleton is None:
            _engine_singleton = VoxCPM2Engine(
                model_id=model_id,
                device=device,
                lora_path=lora_path,
                lora_config_path=lora_config_path,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                optimize=optimize,
                cache_size=cache_size,
                hf_revision=hf_revision,
            )
        return _engine_singleton
