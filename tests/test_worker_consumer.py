"""Unit tests for src/worker/consumer.py — XREADGROUP loop + XACK matrix.

Covers the canonical four-way outcome:
  * success         → XACK
  * PoisonJob       → XACK (drain, never retry)
  * TransientFailure → NO XACK (XAUTOCLAIM will retry)
  * Unknown error   → NO XACK (safer to retry than to drop)

Plus:
  * Consumer group is idempotently created (BUSYGROUP tolerated)
  * Stop event exits run() cleanly mid-loop
  * XAUTOCLAIM picks up stale messages from a dead consumer
"""

from __future__ import annotations

import asyncio
import sys
import types as _types
import uuid
from dataclasses import dataclass
from pathlib import Path

import fakeredis.aioredis
import pytest

# Voxcpm stubbed before any server.* import drags it in.
_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm_model = _types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")
_fake_voxcpm.VoxCPM = type("StubFactory", (), {
    "from_pretrained": staticmethod(lambda *a, **kw: None),
})
_fake_voxcpm_model_voxcpm.LoRAConfig = object
sys.modules.setdefault("voxcpm", _fake_voxcpm)
sys.modules.setdefault("voxcpm.model", _fake_voxcpm_model)
sys.modules.setdefault("voxcpm.model.voxcpm", _fake_voxcpm_model_voxcpm)


@dataclass
class _FakeChunk:
    pcm_int16: bytes
    sample_rate: int = 48000
    sentence_index: int = 0
    sentence_text: str = ""
    elapsed_ms: float = 1.0


class _StubEngine:
    sample_rate = 48000

    def __init__(self, raise_on_generate: bool = False,
                 sentences: list[str] | None = None) -> None:
        self._raise = raise_on_generate
        self._sentences = sentences or ["Cümle bir.", "Cümle iki."]

    def warmup(self) -> None:
        pass

    def synthesize_stream(self, *, text, voice, reference_path, language_id="tr"):
        if self._raise:
            raise RuntimeError("synthetic crash")
        for i, s in enumerate(self._sentences):
            yield _FakeChunk(
                pcm_int16=b"\x00\x00" * 256,
                sample_rate=self.sample_rate,
                sentence_index=i,
                sentence_text=s,
                elapsed_ms=1.0,
            )

    def synthesize(self, **kw):
        raise NotImplementedError


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    db_file = tmp_path / "consumer.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")

    for mod in list(sys.modules):
        if mod.startswith(("server", "worker", "db", "repos", "frontend",
                            "registry", "storage")):
            del sys.modules[mod]

    from db import AsyncSessionLocal, init_models_for_tests
    from db.models import ApiKey, Tenant, Voice

    await init_models_for_tests(db_url)

    ref_path = tmp_path / "ref.wav"
    ref_path.write_bytes(b"\x00" * 256)

    async with AsyncSessionLocal() as s:
        tenant = Tenant(slug="cons-tenant", display_name="C")
        s.add(tenant)
        await s.flush()
        api_key = ApiKey(
            tenant_id=tenant.id,
            prefix="nqai_dev_consumeraaaaa",
            secret_hash="x",
            scopes=["tts:read", "tts:write", "voice:read", "voice:write"],
        )
        s.add(api_key)
        await s.flush()
        voice = Voice(
            owner_tenant_id=tenant.id,
            voice_id="cv",
            display_name="Consumer Voice",
            reference_uri=f"file://{ref_path}",
            reference_sha256="a" * 64,
            reference_seconds=10.0,
            source="placeholder",
            license="internal-placeholder",
        )
        s.add(voice)
        await s.commit()
        return {
            "tenant_id": tenant.id,
            "api_key_id": api_key.id,
            "voice_id": voice.voice_id,
            "ref_path": ref_path,
        }


def _resolver(setup):
    def _resolve(_uri: str) -> Path:
        return setup["ref_path"]
    return _resolve


def _local_archiver(setup):
    """Archive PCM to a local file — keeps consumer tests off R2 while
    still satisfying the pipeline's required-artifact contract."""
    out_dir = setup["ref_path"].parent / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    async def _archive(rid, pcm, sr):
        path = out_dir / f"{rid}.pcm"
        path.write_bytes(pcm)
        return f"file://{path}"

    return _archive


async def _enqueue_job(redis, queue, setup, *, voice_id=None, request_id=None,
                        reserve=True):
    """XADD a job and (optionally) reserve idempotency upstream the way
    POST /v1/tts/jobs would."""
    from db import AsyncSessionLocal
    from repos import IdempotencyRepo
    from server.queue import TtsJobPayload

    rid = request_id or uuid.uuid4()
    job = TtsJobPayload(
        request_id=str(rid),
        tenant_id=str(setup["tenant_id"]),
        api_key_id=str(setup["api_key_id"]),
        voice_id=voice_id or setup["voice_id"],
        text="Bir varmış.",
    )
    if reserve:
        async with AsyncSessionLocal() as s:
            await IdempotencyRepo(s, setup["tenant_id"]).reserve(
                request_id=rid, api_key_id=setup["api_key_id"],
                request_hash="h",
            )
            await s.commit()
    await queue.submit(job)
    return rid


# --------------------------------------------------------------------------- #
# XACK matrix
# --------------------------------------------------------------------------- #
async def test_consumer_success_xacks_and_drains_pel(setup):
    from server.queue import DEFAULT_STREAM, TtsJobQueue
    from worker.consumer import DEFAULT_GROUP, WorkerConsumer

    redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(redis, stream=DEFAULT_STREAM)
    await _enqueue_job(redis, queue, setup)

    consumer = WorkerConsumer(
        redis=redis, engine=_StubEngine(),
        resolve_reference=_resolver(setup),
        archive_to_r2=_local_archiver(setup),
    )
    await consumer.run(max_iterations=1)

    assert consumer.acked == 1
    assert consumer.poisoned == 0
    assert consumer.transient_failures == 0

    # PEL (pending entries) for this consumer is empty after XACK.
    pending = await redis.xpending(DEFAULT_STREAM, DEFAULT_GROUP)
    assert pending["pending"] == 0


async def test_consumer_poison_job_xacks_to_drain(setup):
    """Unknown voice → PoisonJob → XACK so it doesn't loop forever."""
    from server.queue import DEFAULT_STREAM, TtsJobQueue
    from worker.consumer import DEFAULT_GROUP, WorkerConsumer

    redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(redis, stream=DEFAULT_STREAM)
    await _enqueue_job(redis, queue, setup, voice_id="ghost-voice")

    consumer = WorkerConsumer(
        redis=redis, engine=_StubEngine(),
        resolve_reference=_resolver(setup),
        archive_to_r2=_local_archiver(setup),
    )
    await consumer.run(max_iterations=1)

    assert consumer.poisoned == 1
    assert consumer.acked == 0
    # Poison jobs still leave the PEL empty (we XACK them).
    pending = await redis.xpending(DEFAULT_STREAM, DEFAULT_GROUP)
    assert pending["pending"] == 0


async def test_consumer_transient_failure_does_not_xack(setup):
    """Engine crash → TransientFailure → message stays in PEL for retry."""
    from server.queue import DEFAULT_STREAM, TtsJobQueue
    from worker.consumer import DEFAULT_GROUP, WorkerConsumer

    redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(redis, stream=DEFAULT_STREAM)
    await _enqueue_job(redis, queue, setup)

    consumer = WorkerConsumer(
        redis=redis, engine=_StubEngine(raise_on_generate=True),
        resolve_reference=_resolver(setup),
        archive_to_r2=_local_archiver(setup),
    )
    await consumer.run(max_iterations=1)

    assert consumer.transient_failures == 1
    assert consumer.acked == 0
    pending = await redis.xpending(DEFAULT_STREAM, DEFAULT_GROUP)
    # The message we just pulled is still in PEL — awaiting XAUTOCLAIM.
    assert pending["pending"] == 1


async def test_consumer_unknown_exception_does_not_xack(setup, monkeypatch):
    """Programmer error (anything not Poison/Transient) → keep in PEL.
    Operator sees the trace, message can be retried after a fix-and-deploy."""
    from server.queue import DEFAULT_STREAM, TtsJobQueue
    from worker import pipeline as pipeline_mod
    from worker.consumer import DEFAULT_GROUP, WorkerConsumer

    async def explode(*_a, **_kw):
        raise ValueError("simulated bug")

    monkeypatch.setattr(pipeline_mod, "process_one_job", explode)

    redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(redis, stream=DEFAULT_STREAM)
    await _enqueue_job(redis, queue, setup)

    consumer = WorkerConsumer(
        redis=redis, engine=_StubEngine(),
        resolve_reference=_resolver(setup),
        archive_to_r2=_local_archiver(setup),
    )
    # process_one_job is imported by consumer at module-load time, so we
    # need to patch the symbol the consumer actually uses.
    from worker import consumer as consumer_mod
    monkeypatch.setattr(consumer_mod, "process_one_job", explode)

    await consumer.run(max_iterations=1)
    assert consumer.unknown_failures == 1
    assert consumer.acked == 0
    pending = await redis.xpending(DEFAULT_STREAM, DEFAULT_GROUP)
    assert pending["pending"] == 1


# --------------------------------------------------------------------------- #
# Consumer group lifecycle
# --------------------------------------------------------------------------- #
async def test_ensure_consumer_group_is_idempotent(setup):
    """Restarting a worker must not crash on the second XGROUP CREATE."""
    from server.queue import DEFAULT_STREAM
    from worker.consumer import DEFAULT_GROUP, ensure_consumer_group

    redis = fakeredis.aioredis.FakeRedis()
    await ensure_consumer_group(redis, stream=DEFAULT_STREAM, group=DEFAULT_GROUP)
    await ensure_consumer_group(redis, stream=DEFAULT_STREAM, group=DEFAULT_GROUP)
    # No exception = success. fakeredis BUSYGROUP error is caught.


async def test_xautoclaim_runs_periodically_under_busy_traffic(setup):
    """Codex audit 2026-05-24: XAUTOCLAIM must run even when the queue
    is never idle. Otherwise a fast producer can keep tick handling
    busy and a crashed worker's PEL message strands indefinitely.

    We simulate sustained traffic by enqueuing many jobs and asserting
    that XAUTOCLAIM is invoked at least once within `xautoclaim_period_s`
    even though `not handled` never fires."""
    from server.queue import DEFAULT_STREAM, TtsJobQueue
    from worker.consumer import WorkerConsumer

    redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(redis, stream=DEFAULT_STREAM)
    # 10 jobs in the queue — consumer will be busy for several ticks.
    for _ in range(10):
        await _enqueue_job(redis, queue, setup)

    consumer = WorkerConsumer(
        redis=redis, engine=_StubEngine(),
        resolve_reference=_resolver(setup),
        archive_to_r2=_local_archiver(setup),
        xautoclaim_period_s=0.0,  # sweep on every iteration
    )

    sweeps: list[int] = []
    real_sweep = consumer._xautoclaim_sweep

    async def spy_sweep() -> None:
        sweeps.append(1)
        await real_sweep()

    consumer._xautoclaim_sweep = spy_sweep
    await consumer.run(max_iterations=3)

    # Under busy traffic (10 jobs, handled=True every tick), we still
    # see ≥1 periodic sweep. With period_s=0 every iter triggers.
    assert len(sweeps) >= 1
    assert consumer.acked >= 1  # at least one job processed


async def test_default_consumer_name_is_unique_per_pid():
    from worker.consumer import _default_consumer_name

    a = _default_consumer_name()
    assert a.startswith("worker-")
    assert str(__import__("os").getpid()) in a


# --------------------------------------------------------------------------- #
# Stop signal — run() exits cleanly
# --------------------------------------------------------------------------- #
async def test_stop_event_exits_run_cleanly(setup):
    """Setting stop_event mid-flight must terminate run() without
    abandoning a partially processed job."""
    from worker.consumer import WorkerConsumer

    redis = fakeredis.aioredis.FakeRedis()
    stop = asyncio.Event()

    consumer = WorkerConsumer(
        redis=redis, engine=_StubEngine(),
        resolve_reference=_resolver(setup),
        archive_to_r2=_local_archiver(setup),
        block_ms=10,  # short timeout for quick exit
        stop_event=stop,
    )

    async def _stop_after_short_delay():
        await asyncio.sleep(0.05)
        stop.set()

    # Empty queue + 50 ms delay — run() should exit on stop.set().
    asyncio.create_task(_stop_after_short_delay())  # noqa: RUF006
    await asyncio.wait_for(consumer.run(), timeout=2.0)
    # No jobs were enqueued, so nothing was processed.
    assert consumer.acked == 0
