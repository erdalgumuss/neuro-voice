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

logger = logging.getLogger("neurovoice.worker.runtime")


# MLOps PR #4 (A.4) — worker_id is generated once per process boot. The
# WORKER_MODEL_INFO gauge below uses it as a label so on-call can
# `count by (revision)` across pods during a rollout. Stable for the
# lifetime of the worker; regenerated on restart (which is the right
# semantics — a restarted pod IS a different rollout slot).
_WORKER_ID = uuid.uuid4().hex[:12]


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
        hf_revision=settings.model_hf_revision,
    )


def build_redis() -> Redis:
    """Async Redis client targeting `NEUROVOICE_REDIS_URL`."""
    url = os.environ.get("NEUROVOICE_REDIS_URL", "redis://localhost:6379/0")
    return Redis.from_url(url, decode_responses=False)


def build_archive_to_r2():
    """Construct an async callable matching `pipeline.ArchiveCallable`.

    Wraps `R2Storage.upload_bytes` (boto3 sync) inside `asyncio.to_thread`
    so a multi-second R2 PUT doesn't block the worker's event loop —
    important once we add concurrent job handling .

    Returns None when R2 env is unset (dev mode without R2 creds) —
    pipeline will then raise TransientFailure rather than silently
    completing without an artifact. Operators must wire R2 OR use a
    local archiver injected by tests.
    """
    if not (os.environ.get("NEUROVOICE_R2_ACCOUNT_ID") and os.environ.get("NEUROVOICE_R2_BUCKET")):
        logger.warning(
            "R2 env not set (NEUROVOICE_R2_ACCOUNT_ID / NEUROVOICE_R2_BUCKET); "
            "worker has no archive callable — pipeline will fail-loud"
        )
        return None

    from storage import get_r2_storage

    storage = get_r2_storage()

    async def _archive(rid: uuid.UUID, pcm: bytes, sample_rate: int) -> str:
        # PCM int16 mono → WAV (RIFF header) so the artifact is directly
        # playable. Object key is date-prefixed for cheap retention
        # sweeps later under a lifecycle policy.
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


def _parse_warmup_voice_list(raw: str | None) -> list[str]:
    """Parse NEUROVOICE_WORKER_WARMUP_VOICES — comma-separated voice_id
    slugs. Empty / unset → []. Whitespace trimmed, empties dropped.
    """
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


async def _warmup_voices_from_env(engine: BaseSynthEngine) -> None:
    """eager-load any voice_ids listed in
    NEUROVOICE_WORKER_WARMUP_VOICES so the first inference for each is hot.

    Each voice's LoRA adapter is loaded into the engine's per-voice
    cache (the LRU eviction policy still applies — if the list is
    longer than NEUROVOICE_LORA_CACHE_SIZE the later entries evict the
    earlier ones, defeating the purpose; operators must size the
    cache to fit the list).

    Failures are logged + skipped — one un-resolvable voice MUST NOT
    prevent the worker from starting. The cold-load metric still
    fires from inside _model_for_adapter so an operator can see what
    succeeded vs failed."""
    voice_ids = _parse_warmup_voice_list(
        os.environ.get("NEUROVOICE_WORKER_WARMUP_VOICES"),
    )
    if not voice_ids:
        return

    logger.info(
        "warmup voice list: %s (cache_size=%s)",
        voice_ids,
        getattr(engine, "_cache_size", "n/a"),
    )

    # Best-effort: each voice is loaded against its OWNER tenant's
    # catalog row (the slug is globally unique only within a tenant,
    # but the warmup env list is a single global list — we resolve
    # the first matching row regardless of tenant).
    from sqlalchemy import select

    from db import AsyncSessionLocal
    from db.models import Voice
    from storage.reference_resolver import resolve_reference_uri

    # ADR-11 — skip warming voices that aren't in the active lifecycle
    # state. Importing lifecycle_state lazily keeps the worker boot
    # path independent of the repo layer's other dependencies.
    from repos import lifecycle_state

    for voice_id in voice_ids:
        try:
            async with AsyncSessionLocal() as s:
                row = (await s.execute(
                    select(Voice).where(
                        Voice.voice_id == voice_id,
                        Voice.deleted_at.is_(None),
                    ).limit(1)
                )).scalar_one_or_none()
            if row is None:
                logger.warning("warmup skip: voice_id=%s not found", voice_id)
                continue
            if lifecycle_state(row) != "active":
                logger.warning(
                    "warmup skip: voice_id=%s state=%s",
                    voice_id, lifecycle_state(row),
                )
                continue
            # Resolve the reference too so a stub R2 download error
            # surfaces at warmup time, not on the first request.
            await asyncio.to_thread(
                resolve_reference_uri, row.reference_uri,
            )
            # warmup_voice loads the adapter; engine emits the
            # cold-load histogram with voice=voice_id.
            await asyncio.to_thread(engine.warmup_voice, row)
            logger.info("warmup voice ready: %s", voice_id)
        except Exception:
            logger.exception(
                "warmup voice failed: voice_id=%s — continuing",
                voice_id,
            )


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
    cost on the user-visible critical path.

    — additionally pre-loads any voice_id slugs
    listed in NEUROVOICE_WORKER_WARMUP_VOICES so per-voice LoRA adapters
    are hot on the first inference. The cold-load Prometheus metric
    (neurovoice_worker_cold_load_seconds{voice}) fires for each preload."""
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
        # Per-voice warmup runs AFTER base warmup so the per-voice
        # load uses the freshly-warm base. A failure here doesn't
        # abort boot — the worker still starts and serves cold-load.
        await _warmup_voices_from_env(engine)

    # MLOps PR #4 (A.4) — emit WORKER_MODEL_INFO once boot is ready
    # so `count by (revision)` answers "which workers are on which
    # revision" during a rollout. Best-effort: if Prometheus isn't
    # installed for some reason, don't fail boot.
    try:
        from observability import WORKER_MODEL_INFO
        WORKER_MODEL_INFO.labels(
            worker_id=_WORKER_ID,
            model_id=getattr(engine, "model_id", "unknown") or "unknown",
            revision=getattr(engine, "hf_revision", "unknown") or "unknown",
        ).set(1)
    except Exception:
        logger.exception("WORKER_MODEL_INFO emit failed at boot — ignoring")

    # Ping Redis so we fail fast on bad URL / unreachable host instead
    # of hanging on the first XREADGROUP.
    pong = await redis.ping()
    if not pong:
        raise RuntimeError("NEUROVOICE_REDIS_URL unreachable")

    return engine, redis, archive_to_r2
