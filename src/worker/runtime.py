"""Worker process runtime — factories + boot helpers used by main.py.

Separated from main.py so the wiring (engine, redis, R2, archive
callable) is unit-testable without spinning up a real process. Keeps
main.py a thin signal-handling shell.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis

from audio.wav import pcm16_to_wav_bytes
from server.config import settings

from .engine import BaseSynthEngine, get_engine

logger = logging.getLogger("nqai_voice.worker.runtime")


def build_engine() -> BaseSynthEngine:
    """Build the VoxCPM2 engine from the same settings the gateway uses.

    Worker controls its own warmup — eager-loading on boot moves the
    one-time 30-60s VoxCPM2 cold-start out of the request hot path so
    the first job a worker handles isn't slow."""
    return get_engine(
        model_id=settings.model_id,
        device=settings.device,
        lora_path=settings.lora_path,
        lora_config_path=settings.lora_config_path,
        cfg_value=settings.cfg_value,
        inference_timesteps=settings.inference_timesteps,
        optimize=settings.optimize,
    )


def build_redis() -> Redis:
    """Async Redis client targeting `NQAI_REDIS_URL`."""
    url = os.environ.get("NQAI_REDIS_URL", "redis://localhost:6379/0")
    return Redis.from_url(url, decode_responses=False)


def build_archive_to_r2():
    """Construct an async callable matching `pipeline.ArchiveCallable`.

    Wraps `R2Storage.upload_bytes` (boto3 sync) inside `asyncio.to_thread`
    so a multi-second R2 PUT doesn't block the worker's event loop —
    important once we add concurrent job handling (Faz B.1.5).

    Returns None when R2 env is unset (dev mode without R2 creds) —
    pipeline will then raise TransientFailure rather than silently
    completing without an artifact. Operators must wire R2 OR use a
    local archiver injected by tests.
    """
    if not (os.environ.get("NQAI_R2_ACCOUNT_ID") and os.environ.get("NQAI_R2_BUCKET")):
        logger.warning(
            "R2 env not set (NQAI_R2_ACCOUNT_ID / NQAI_R2_BUCKET); "
            "worker has no archive callable — pipeline will fail-loud"
        )
        return None

    from storage import get_r2_storage

    storage = get_r2_storage()

    async def _archive(rid: uuid.UUID, pcm: bytes, sample_rate: int) -> str:
        # PCM int16 mono → WAV (RIFF header) so the artifact is directly
        # playable. Object key is date-prefixed for cheap retention
        # sweeps later (Faz C lifecycle policy).
        wav = pcm16_to_wav_bytes(pcm, sample_rate=sample_rate)
        today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        key = f"tts-outputs/{today}/{rid}.wav"
        uri = await asyncio.to_thread(
            storage.upload_bytes,
            wav, key, content_type="audio/wav",
        )
        # upload_bytes returns S3URI dataclass; pipeline expects str.
        return uri.uri

    return _archive


async def boot_worker(
    *,
    engine: BaseSynthEngine | None = None,
    redis: Redis | None = None,
    archive_to_r2=None,
    warmup: bool = True,
):
    """Construct + warm up the worker dependencies. Returns
    (engine, redis, archive_to_r2) so the caller wires the
    WorkerConsumer with them.

    Warmup (when enabled) loads VoxCPM2 weights into VRAM eagerly so
    the first job a worker handles doesn't pay the 30-60s cold-load
    cost on the user-visible critical path."""
    engine = engine or build_engine()
    redis = redis or build_redis()
    if archive_to_r2 is None:
        archive_to_r2 = build_archive_to_r2()

    if warmup:
        logger.info("worker warmup: loading VoxCPM2 weights")
        # engine.warmup() may take 30-60s — push to thread so we
        # don't block other startup awaits (e.g. Redis ping).
        await asyncio.to_thread(engine.warmup)
        logger.info("worker warmup: done")

    # Ping Redis so we fail fast on bad URL / unreachable host instead
    # of hanging on the first XREADGROUP.
    pong = await redis.ping()
    if not pong:
        raise RuntimeError("NQAI_REDIS_URL unreachable")

    return engine, redis, archive_to_r2
