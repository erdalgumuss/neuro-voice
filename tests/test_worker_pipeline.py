"""Unit tests for src/worker/pipeline.py.

Pipeline contract (worker.pipeline module docstring):
  * Terminal errors  (voice/ref missing) → PoisonJob + error chunk +
    idem.fail() — consumer XACKs to drain
  * Transient errors (engine/archive/db hiccup) → TransientFailure —
    NO error chunk, NO idem.fail() — consumer skips XACK, XAUTOCLAIM
    retries on another worker
  * Commit-before-final ordering: GET sees `complete + response_uri`
    iff client saw `final=True` chunk
  * Archive REQUIRED — no `audio_url=null` dangling state

Engine is stubbed (no GPU); Redis is fakeredis; DB is aiosqlite. We
assert wire behaviour and side-effect semantics, not byte-exact audio.
"""

from __future__ import annotations

import sys
import time
import types as _types
import uuid
from dataclasses import dataclass
from pathlib import Path

import fakeredis.aioredis
import pytest

# Voxcpm must be stubbed before any server.* import drags it in.
_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm_model = _types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")


class _StubInner:
    sample_rate = 48000


class _StubModel:
    tts_model = _StubInner()


_fake_voxcpm.VoxCPM = type("StubFactory", (), {
    "from_pretrained": staticmethod(lambda *a, **kw: _StubModel()),
})
_fake_voxcpm_model_voxcpm.LoRAConfig = object
sys.modules.setdefault("voxcpm", _fake_voxcpm)
sys.modules.setdefault("voxcpm.model", _fake_voxcpm_model)
sys.modules.setdefault("voxcpm.model.voxcpm", _fake_voxcpm_model_voxcpm)


# --------------------------------------------------------------------------- #
# Lightweight engine + DB setup
# --------------------------------------------------------------------------- #
@dataclass
class _FakeChunk:
    pcm_int16: bytes
    sample_rate: int = 48000
    sentence_index: int = 0
    sentence_text: str = ""
    elapsed_ms: float = 1.0


class _StubEngine:
    """Mimics BaseSynthEngine — deterministic PCM, no GPU."""

    sample_rate = 48000

    def __init__(self, sentences: list[str] | None = None,
                 raise_on_generate: bool = False,
                 empty_output: bool = False) -> None:
        self._sentences = sentences or ["İlk cümle.", "İkinci cümle."]
        self._raise = raise_on_generate
        self._empty = empty_output

    def warmup(self) -> None:
        pass

    def synthesize_stream(self, *, text, voice, reference_path, language_id="tr"):
        if self._raise:
            raise RuntimeError("synthetic engine failure")
        if self._empty:
            return
        for i, s in enumerate(self._sentences):
            yield _FakeChunk(
                pcm_int16=b"\x00\x00" * 1024,
                sample_rate=self.sample_rate,
                sentence_index=i,
                sentence_text=s,
                elapsed_ms=1.0,
            )

    def synthesize(self, *, text, voice, reference_path, language_id="tr"):
        raise NotImplementedError("pipeline uses synthesize_stream only")


@pytest.fixture
async def setup_db(tmp_path, monkeypatch):
    db_file = tmp_path / "pipeline.db"
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

    async with AsyncSessionLocal() as s:
        tenant = Tenant(slug="pipe-tenant", display_name="P")
        s.add(tenant)
        await s.flush()
        api_key = ApiKey(
            tenant_id=tenant.id,
            prefix="nqai_dev_aaaaaaaaaaaaaa",
            secret_hash="x",
            scopes=["tts:read", "tts:write", "voice:read", "voice:write"],
        )
        s.add(api_key)
        await s.flush()
        ref_path = tmp_path / "ref.wav"
        ref_path.write_bytes(b"\x00" * 256)
        voice = Voice(
            owner_tenant_id=tenant.id,
            voice_id="pipe-voice",
            display_name="Pipe Voice",
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
            "artifact_dir": tmp_path / "artifacts",
        }


def _job(setup, *, voice_id: str | None = None, app_label: str | None = None,
          text: str = "Bir varmış.", enqueued_at_ms: int | None = None):
    from server.queue import TtsJobPayload

    return TtsJobPayload(
        request_id=str(uuid.uuid4()),
        tenant_id=str(setup["tenant_id"]),
        api_key_id=str(setup["api_key_id"]),
        voice_id=voice_id or setup["voice_id"],
        text=text,
        language="tr",
        audio_format="wav",
        app_label=app_label,
        enqueued_at_ms=enqueued_at_ms,
    )


async def _reserve(setup, job):
    """Mimic gateway POST: reserve idempotency BEFORE worker sees the job."""
    from db import AsyncSessionLocal
    from repos import IdempotencyRepo
    async with AsyncSessionLocal() as s:
        await IdempotencyRepo(s, setup["tenant_id"]).reserve(
            request_id=uuid.UUID(job.request_id),
            api_key_id=setup["api_key_id"],
            request_hash="h",
        )
        await s.commit()


def _stub_resolver(setup):
    def _resolve(_uri: str) -> Path:
        return setup["ref_path"]
    return _resolve


def _local_archiver(setup):
    """E2E-friendly archive callback — writes PCM to a local file and
    returns a `file://` URI. Lets B.1 tests verify the artifact
    contract end-to-end without R2 credentials."""
    setup["artifact_dir"].mkdir(parents=True, exist_ok=True)

    async def _archive(rid: uuid.UUID, pcm: bytes, sample_rate: int) -> str:
        path = setup["artifact_dir"] / f"{rid}.pcm"
        path.write_bytes(pcm)
        return f"file://{path}"

    return _archive


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
async def test_pipeline_happy_path_commits_then_publishes_final(setup_db):
    """3 chunks → archive → commit → final. Final ONLY after commit."""
    setup = setup_db
    from server.queue import result_stream_name
    from worker.pipeline import process_one_job

    redis = fakeredis.aioredis.FakeRedis()
    engine = _StubEngine(sentences=["Cümle bir.", "Cümle iki.", "Cümle üç."])
    job = _job(
        setup,
        app_label="neeko-mobile",
        enqueued_at_ms=int(time.time() * 1000) - 25,
    )
    await _reserve(setup, job)

    await process_one_job(
        job, redis=redis, engine=engine,
        resolve_reference=_stub_resolver(setup),
        archive_to_r2=_local_archiver(setup),
        worker_id="worker-test-1",
    )

    # 3 sentence chunks + 1 final = 4 entries on the result stream.
    stream = result_stream_name(uuid.UUID(job.request_id))
    entries = await redis.xrange(stream)
    assert len(entries) == 4

    # Last entry is final=True, no PCM, no error.
    last_fields = entries[-1][1]
    assert last_fields[b"final"] == b"true"
    assert last_fields[b"seq"] == b"3"
    assert b"error" not in last_fields

    # Idempotency complete + response_uri NEVER NULL (audit fix #4).
    from db import AsyncSessionLocal
    from repos import IdempotencyRepo, UsageRepo
    async with AsyncSessionLocal() as s:
        row = await IdempotencyRepo(s, setup["tenant_id"]).get(
            uuid.UUID(job.request_id)
        )
        assert row is not None
        assert row.status == "complete"
        assert row.response_uri is not None
        assert row.response_uri.startswith("file://")

        usage = await UsageRepo(s, setup["tenant_id"]).recent(limit=10)
        assert len(usage) == 1
        assert usage[0].app_label == "neeko-mobile"
        assert usage[0].worker_id == "worker-test-1"
        assert usage[0].queue_wait_ms is not None
        assert usage[0].queue_wait_ms >= 0
        assert usage[0].inference_ms is not None
        assert usage[0].inference_ms >= 0
        assert usage[0].sentence_count == 3
        assert usage[0].status == "ok"
        # 3 sentences × 1024 samples × int16 = 6144 bytes; duration =
        # 3072 samples / 48000 sr = 64 ms.
        assert usage[0].duration_ms == 64


async def test_pipeline_sets_expire_on_result_stream(setup_db):
    setup = setup_db
    from worker.pipeline import process_one_job

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)
    await process_one_job(
        job, redis=redis, engine=_StubEngine(),
        resolve_reference=_stub_resolver(setup),
        archive_to_r2=_local_archiver(setup),
    )
    keys = [k async for k in redis.scan_iter(match="nqai.tts.results.*")]
    assert keys, "expected at least one result stream key"
    ttl = await redis.ttl(keys[0])
    assert 0 < ttl <= 600


# --------------------------------------------------------------------------- #
# Terminal errors (PoisonJob) — XACK to drain
# --------------------------------------------------------------------------- #
async def test_pipeline_unknown_voice_raises_poison_with_error_chunk(setup_db):
    setup = setup_db
    from server.queue import result_stream_name
    from worker.pipeline import PoisonJob, process_one_job

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup, voice_id="ghost-voice")
    await _reserve(setup, job)

    with pytest.raises(PoisonJob):
        await process_one_job(
            job, redis=redis, engine=_StubEngine(),
            resolve_reference=_stub_resolver(setup),
            archive_to_r2=_local_archiver(setup),
        )

    entries = await redis.xrange(result_stream_name(uuid.UUID(job.request_id)))
    assert len(entries) == 1
    assert entries[0][1][b"error"] == b"voice_not_found"

    from db import AsyncSessionLocal
    from repos import IdempotencyRepo
    async with AsyncSessionLocal() as s:
        row = await IdempotencyRepo(s, setup["tenant_id"]).get(
            uuid.UUID(job.request_id)
        )
        assert row is not None and row.status == "failed"


async def test_pipeline_reference_missing_raises_poison_with_error_chunk(setup_db):
    setup = setup_db
    from server.queue import result_stream_name
    from worker.pipeline import PoisonJob, process_one_job

    def broken_resolver(_uri: str) -> Path:
        raise FileNotFoundError("ref.wav missing")

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)

    with pytest.raises(PoisonJob):
        await process_one_job(
            job, redis=redis, engine=_StubEngine(),
            resolve_reference=broken_resolver,
            archive_to_r2=_local_archiver(setup),
        )

    entries = await redis.xrange(result_stream_name(uuid.UUID(job.request_id)))
    assert entries
    assert entries[0][1][b"error"].startswith(b"reference_missing")

    from db import AsyncSessionLocal
    from repos import IdempotencyRepo
    async with AsyncSessionLocal() as s:
        row = await IdempotencyRepo(s, setup["tenant_id"]).get(
            uuid.UUID(job.request_id)
        )
        assert row is not None and row.status == "failed"


async def test_pipeline_empty_engine_output_is_poison(setup_db):
    """Engine returned without yielding → unrecoverable input/model bug.
    Treated as poison (XACK + no retry) and now surfaced terminally so
    clients do not poll a forever-processing idempotency row."""
    setup = setup_db
    from worker.pipeline import PoisonJob, process_one_job

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)

    with pytest.raises(PoisonJob):
        await process_one_job(
            job, redis=redis, engine=_StubEngine(empty_output=True),
            resolve_reference=_stub_resolver(setup),
            archive_to_r2=_local_archiver(setup),
            worker_id="worker-empty",
        )

    from db import AsyncSessionLocal
    from repos import IdempotencyRepo, UsageRepo

    async with AsyncSessionLocal() as s:
        row = await IdempotencyRepo(s, setup["tenant_id"]).get(
            uuid.UUID(job.request_id)
        )
        assert row is not None and row.status == "failed"
        usage = await UsageRepo(s, setup["tenant_id"]).recent(limit=10)
        assert len(usage) == 1
        assert usage[0].status == "error"
        assert usage[0].error_code == "empty_pcm"
        assert usage[0].worker_id == "worker-empty"


# --------------------------------------------------------------------------- #
# Transient errors (TransientFailure) — NO error chunk, NO idem.fail
# --------------------------------------------------------------------------- #
async def test_pipeline_engine_crash_is_transient_and_silent(setup_db):
    """Audit fix #1: engine crash = transient. NO error chunk, NO
    idem.fail. XAUTOCLAIM retries on another worker — client sees
    queued, not failed."""
    setup = setup_db
    from server.queue import result_stream_name
    from worker.pipeline import TransientFailure, process_one_job

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)

    with pytest.raises(TransientFailure):
        await process_one_job(
            job, redis=redis, engine=_StubEngine(raise_on_generate=True),
            resolve_reference=_stub_resolver(setup),
            archive_to_r2=_local_archiver(setup),
        )

    # Result stream is empty — no error event, no chunks.
    stream = result_stream_name(uuid.UUID(job.request_id))
    assert (await redis.xlen(stream)) == 0

    # Idempotency stays 'processing' — retry path is open.
    from db import AsyncSessionLocal
    from repos import IdempotencyRepo
    async with AsyncSessionLocal() as s:
        row = await IdempotencyRepo(s, setup["tenant_id"]).get(
            uuid.UUID(job.request_id)
        )
        assert row is not None and row.status == "processing"


async def test_pipeline_archive_failure_is_transient_and_silent(setup_db):
    """Audit fix #4: archive failure is transient — retry re-generates
    PCM and tries archive again. No client-visible failure state,
    no orphan complete-with-null-uri row."""
    setup = setup_db
    from server.queue import result_stream_name
    from worker.pipeline import TransientFailure, process_one_job

    async def boom_archive(*_a, **_kw):
        raise RuntimeError("R2 down")

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)

    with pytest.raises(TransientFailure):
        await process_one_job(
            job, redis=redis, engine=_StubEngine(),
            resolve_reference=_stub_resolver(setup),
            archive_to_r2=boom_archive,
        )

    # Chunks may have been emitted (engine ran before archive), but
    # no final marker — gateway will wait or timeout, then retry sees
    # a freshly archived response.
    stream = result_stream_name(uuid.UUID(job.request_id))
    entries = await redis.xrange(stream)
    finals = [e for e in entries if e[1].get(b"final") == b"true"]
    assert not finals, "no final marker should be published on archive failure"

    # Idempotency stays 'processing' — never 'failed', never 'complete'.
    from db import AsyncSessionLocal
    from repos import IdempotencyRepo
    async with AsyncSessionLocal() as s:
        row = await IdempotencyRepo(s, setup["tenant_id"]).get(
            uuid.UUID(job.request_id)
        )
        assert row is not None and row.status == "processing"


async def test_pipeline_archive_returns_none_is_transient(setup_db):
    """Archive callable returned None (no exception) — same outcome
    as raise: no artifact, refuse to mark complete."""
    setup = setup_db
    from worker.pipeline import TransientFailure, process_one_job

    async def null_archive(*_a, **_kw):
        return None

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)

    with pytest.raises(TransientFailure):
        await process_one_job(
            job, redis=redis, engine=_StubEngine(),
            resolve_reference=_stub_resolver(setup),
            archive_to_r2=null_archive,
        )


async def test_pipeline_without_archive_callback_is_transient(setup_db):
    """Production safety net (audit fix #4): if the worker is mis-
    wired without an archive callable, refuse to mark complete rather
    than leaving audio_url=null on the status response."""
    setup = setup_db
    from worker.pipeline import TransientFailure, process_one_job

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)

    with pytest.raises(TransientFailure):
        await process_one_job(
            job, redis=redis, engine=_StubEngine(),
            resolve_reference=_stub_resolver(setup),
            # archive_to_r2=None  (default) — should refuse to complete
        )


# --------------------------------------------------------------------------- #
# Commit-before-final ordering (audit fix #2)
# --------------------------------------------------------------------------- #
async def test_pipeline_publishes_final_only_after_db_commit(setup_db, monkeypatch):
    """If the DB commit raises, NO final marker hits the stream — the
    gateway's invariant `final=True ⇒ complete + response_uri` holds."""
    setup = setup_db
    from server.queue import result_stream_name
    from worker.pipeline import TransientFailure, process_one_job

    redis = fakeredis.aioredis.FakeRedis()
    job = _job(setup)
    await _reserve(setup, job)

    # Force the DB commit at the final step to raise by monkeypatching
    # IdempotencyRepo.complete to blow up.
    import repos.idempotency as idem_mod
    real_complete = idem_mod.IdempotencyRepo.complete

    async def explode(*_a, **_kw):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(idem_mod.IdempotencyRepo, "complete", explode)
    try:
        with pytest.raises(TransientFailure):
            await process_one_job(
                job, redis=redis, engine=_StubEngine(),
                resolve_reference=_stub_resolver(setup),
                archive_to_r2=_local_archiver(setup),
            )
    finally:
        monkeypatch.setattr(idem_mod.IdempotencyRepo, "complete", real_complete)

    # Chunks were published, but NO final marker — that's the invariant.
    entries = await redis.xrange(result_stream_name(uuid.UUID(job.request_id)))
    finals = [e for e in entries if e[1].get(b"final") == b"true"]
    assert not finals, (
        "publish_final must happen AFTER DB commit; otherwise the "
        "gateway would tell a client 'done' for a job that DB still "
        "shows as processing"
    )


# --------------------------------------------------------------------------- #
# Streaming bridge TODO (audit fix #5)
# --------------------------------------------------------------------------- #
async def test_pipeline_publishes_first_chunk_before_engine_finishes(
    setup_db, monkeypatch,
):
    """The B.1.5 streaming bridge contract: the first `publish_chunk`
    call must reach Redis BEFORE the engine has finished yielding all
    sentences. Replaces the old "drain-then-emit" pin with the
    inverted invariant — confirms `iter_live_audio_frames` actually
    plumbs into the pipeline."""
    import time as _time

    setup = setup_db
    import worker.pipeline as pmod
    from worker.pipeline import process_one_job
    real_publish_chunk = pmod.publish_chunk

    # Engine that artificially blocks between sentence yields so the
    # event loop has plenty of time to publish the first sentence
    # before the second one is generated.
    class _SlowSentenceEngine(_StubEngine):
        def __init__(self) -> None:
            super().__init__(sentences=["Birinci.", "İkinci.", "Üçüncü."])
            self.yields_at_ms: list[int] = []

        def synthesize_stream(self, **kw):
            t0 = _time.monotonic()
            for c in super().synthesize_stream(**kw):
                self.yields_at_ms.append(
                    int((_time.monotonic() - t0) * 1000),
                )
                yield c
                _time.sleep(0.05)  # 50ms between sentence yields

    publish_at_ms: list[int] = []
    started = _time.monotonic()

    async def spy_publish_chunk(redis, rid, **kw):
        publish_at_ms.append(
            int((_time.monotonic() - started) * 1000),
        )
        await real_publish_chunk(redis, rid, **kw)

    monkeypatch.setattr(pmod, "publish_chunk", spy_publish_chunk)

    redis = fakeredis.aioredis.FakeRedis()
    engine = _SlowSentenceEngine()
    job = _job(setup)
    await _reserve(setup, job)
    await process_one_job(
        job, redis=redis, engine=engine,
        resolve_reference=_stub_resolver(setup),
        archive_to_r2=_local_archiver(setup),
    )

    # All three sentences eventually publish.
    assert len(publish_at_ms) == 3, publish_at_ms

    # The first publish must happen BEFORE the engine yields the
    # second sentence. Engine sleeps 50ms between yields; we give
    # ourselves a generous 40ms window — the first publish must
    # land inside it.
    second_yield_ms = engine.yields_at_ms[1] if len(engine.yields_at_ms) > 1 else None
    assert second_yield_ms is not None
    assert publish_at_ms[0] < second_yield_ms, (
        f"first publish at {publish_at_ms[0]}ms came AFTER second "
        f"sentence yielded at {second_yield_ms}ms — bridge not active"
    )
