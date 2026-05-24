"""Async TTS jobs — POST /v1/tts/jobs + GET /v1/tts/jobs/{id}.

Covers:
  * Job creation with Idempotency-Key
  * Stripe-style replay: identical Idempotency-Key returns the same
    job_id without re-enqueueing
  * Status polling: queued → complete (via direct IdempotencyRepo
    completion, simulating a worker)
  * Validation errors (missing key, malformed key, missing voice,
    oversize text)
  * Cross-tenant isolation (404 on another tenant's job)
  * Backpressure: queue depth threshold returns 503

Voxcpm is stubbed at module load. The Redis Streams queue is fakeredis-
backed; the queue singleton is replaced via dependency_overrides.
"""

from __future__ import annotations

import io
import sys
import types as _types
import uuid

import fakeredis.aioredis
import numpy as np
import pytest
import soundfile as sf

# Stub voxcpm before server import.
_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm_model = _types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")


class _StubInner:
    sample_rate = 48000


class _StubModel:
    tts_model = _StubInner()

    def generate(self, *a, **kw):
        return np.zeros(self.tts_model.sample_rate, dtype=np.float32)


class _StubFactory:
    @staticmethod
    def from_pretrained(*a, **kw):
        return _StubModel()


_fake_voxcpm.VoxCPM = _StubFactory
_fake_voxcpm_model_voxcpm.LoRAConfig = object
sys.modules.setdefault("voxcpm", _fake_voxcpm)
sys.modules.setdefault("voxcpm.model", _fake_voxcpm_model)
sys.modules.setdefault("voxcpm.model.voxcpm", _fake_voxcpm_model_voxcpm)


def _make_wav_bytes(duration_s: float = 2.0, sr: int = 16000) -> bytes:
    audio = (np.random.randn(int(duration_s * sr)) * 0.1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
async def setup(monkeypatch, tmp_path):
    db_file = tmp_path / "jobs.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")
    monkeypatch.setenv("NQAI_REQUIRE_AUTH", "true")
    monkeypatch.setenv("NQAI_REFERENCE_DIR", str(ref_dir))
    monkeypatch.setenv("NQAI_COOKIE_SECURE", "false")
    monkeypatch.setenv("NQAI_QUEUE_DEPTH_LIMIT", "100")

    for mod_name in list(sys.modules):
        if mod_name.startswith(("server", "db", "repos", "frontend", "registry")):
            del sys.modules[mod_name]

    from db import AsyncSessionLocal, init_models_for_tests
    from repos import ApiKeyRepo, TenantRepo
    from server.security import generate_api_key

    await init_models_for_tests(db_url)

    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tenant = await TenantRepo(s).create(slug="jobs-tenant", display_name="J")
        await ApiKeyRepo(s).create(
            tenant_id=tenant.id,
            prefix=prefix,
            secret_hash=secret_hash,
            scopes=["tts:read", "tts:write", "voice:read", "voice:write"],
            rate_limit_per_minute=1000,
        )
        await s.commit()
        return full_key, tenant.id


@pytest.fixture
def client(setup):
    full_key, _tid = setup

    from server.auth import get_redis
    from server.queue import TtsJobQueue, get_queue

    fake_redis = fakeredis.aioredis.FakeRedis()
    fake_queue = TtsJobQueue(fake_redis, stream="nqai.tts.jobs.test")

    from fastapi.testclient import TestClient

    from server.main import app

    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: fake_queue
    try:
        with TestClient(app) as c:
            c.headers.update({"Authorization": f"Bearer {full_key}"})
            c.fake_queue = fake_queue  # tests reach in for depth/replay assertions
            c.fake_redis = fake_redis
            yield c
    finally:
        app.dependency_overrides.clear()


def _enroll_voice(client, voice_id: str = "demo-01"):
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={"voice_id": voice_id, "display_name": "Demo"},
        files={"reference_audio": ("d.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text


def _create_job(client, *, idempotency_key: str | None = None,
                voice_id: str = "demo-01", text: str = "Merhaba dünya."):
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return client.post(
        "/v1/tts/jobs",
        headers=headers,
        json={"text": text, "voice_id": voice_id},
    )


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_create_job_returns_queued_with_job_id(client):
    _enroll_voice(client)
    rid = str(uuid.uuid4())
    r = _create_job(client, idempotency_key=rid)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] == rid
    assert body["status"] == "queued"
    assert body["deduplicated"] is False
    assert "created_at" in body


def test_create_job_writes_to_redis_stream(client):
    _enroll_voice(client)
    rid = str(uuid.uuid4())
    _create_job(client, idempotency_key=rid)

    # Inspect the stream — exactly one entry should have been added.
    import asyncio

    async def _xlen():
        return int(await client.fake_redis.xlen("nqai.tts.jobs.test"))

    assert asyncio.run(_xlen()) == 1


# --------------------------------------------------------------------------- #
# Idempotency replay
# --------------------------------------------------------------------------- #
def test_duplicate_idempotency_key_returns_same_job_without_re_enqueue(client):
    _enroll_voice(client)
    rid = str(uuid.uuid4())

    r1 = _create_job(client, idempotency_key=rid)
    r2 = _create_job(client, idempotency_key=rid)
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["job_id"] == r2.json()["job_id"]
    assert r1.json()["deduplicated"] is False
    assert r2.json()["deduplicated"] is True

    # Critical: queue stayed at depth 1, no double work.
    import asyncio

    async def _xlen():
        return int(await client.fake_redis.xlen("nqai.tts.jobs.test"))

    assert asyncio.run(_xlen()) == 1


def test_same_idempotency_key_different_body_returns_409(client):
    """Stripe-style guard (D-05 + Ö4): reusing an Idempotency-Key with
    a different request body must surface 409 Conflict — silent replay
    would mask a typo-fix POST under the same key."""
    _enroll_voice(client)
    rid = str(uuid.uuid4())

    r1 = _create_job(client, idempotency_key=rid, text="Merhaba dünya.")
    assert r1.status_code == 202, r1.text

    r2 = _create_job(client, idempotency_key=rid, text="Tamamen farklı içerik.")
    assert r2.status_code == 409, r2.text
    body = r2.json()["detail"]
    assert body["error"] == "idempotency_conflict"
    assert "original_created_at" in body
    assert body["original_status"] in {"processing", "complete", "failed"}

    # Critical: the second request did NOT enqueue.
    import asyncio

    async def _xlen():
        return int(await client.fake_redis.xlen("nqai.tts.jobs.test"))

    assert asyncio.run(_xlen()) == 1


def test_same_idempotency_key_different_voice_returns_409(client):
    """Voice swap under the same Idempotency-Key counts as body mismatch."""
    _enroll_voice(client, voice_id="demo-01")
    _enroll_voice(client, voice_id="demo-02")
    rid = str(uuid.uuid4())

    r1 = _create_job(client, idempotency_key=rid, voice_id="demo-01")
    assert r1.status_code == 202
    r2 = _create_job(client, idempotency_key=rid, voice_id="demo-02")
    assert r2.status_code == 409


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_create_job_requires_idempotency_key(client):
    _enroll_voice(client)
    r = _create_job(client, idempotency_key=None)
    assert r.status_code == 400
    assert "Idempotency-Key" in r.json()["detail"]


def test_create_job_rejects_malformed_idempotency_key(client):
    _enroll_voice(client)
    r = _create_job(client, idempotency_key="not-a-uuid")
    assert r.status_code == 400


def test_create_job_rejects_oversize_text(client):
    _enroll_voice(client)
    huge = "x" * 30000  # NQAI_MAX_CHARS default is 4000
    r = _create_job(client, idempotency_key=str(uuid.uuid4()), text=huge)
    assert r.status_code in {400, 422}


def test_create_job_unknown_voice_404(client):
    r = _create_job(
        client, idempotency_key=str(uuid.uuid4()), voice_id="ghost-voice"
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Status polling
# --------------------------------------------------------------------------- #
def test_status_queued_immediately_after_create(client):
    _enroll_voice(client)
    rid = str(uuid.uuid4())
    _create_job(client, idempotency_key=rid)
    r = client.get(f"/v1/tts/jobs/{rid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == rid
    assert body["status"] == "queued"
    assert body.get("output") is None


def test_status_after_worker_completion(client, setup):
    """Simulate the worker side by directly stamping the
    IdempotencyRepo row to complete; then the status endpoint should
    return the audio URL."""
    _, tenant_id = setup
    _enroll_voice(client)
    rid_uuid = uuid.uuid4()
    _create_job(client, idempotency_key=str(rid_uuid))

    # Worker side completion + usage record (would happen in src/worker/).
    import asyncio

    from sqlalchemy import select

    from db import AsyncSessionLocal
    from db.models import ApiKey
    from repos import IdempotencyRepo, UsageRepo

    async def _finish():
        async with AsyncSessionLocal() as s:
            # The worker would receive api_key_id on the stream payload; here
            # we look it up from the tenant's only key to satisfy the FK.
            key_row = (
                await s.execute(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
            ).scalar_one()
            await IdempotencyRepo(s, tenant_id).complete(
                rid_uuid, response_uri="s3://outputs/demo-01/abc.wav"
            )
            await UsageRepo(s, tenant_id).record(
                api_key_id=key_row.id,
                voice_id="demo-01",
                request_id=rid_uuid,
                text_char_count=14,
                sentence_count=1,
                duration_ms=2000,
                elapsed_ms=900,
                rtf=0.45,
                status="ok",
            )
            await s.commit()

    asyncio.run(_finish())

    r = client.get(f"/v1/tts/jobs/{rid_uuid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "complete"
    assert body["output"]["audio_url"] == "s3://outputs/demo-01/abc.wav"
    assert body["output"]["content_type"] == "audio/wav"
    assert body["metrics"]["inference_ms"] == 900
    assert body["metrics"]["generated_audio_ms"] == 2000
    assert body["metrics"]["rtf"] == pytest.approx(0.45)


def test_status_unknown_job_404(client):
    r = client.get(f"/v1/tts/jobs/{uuid.uuid4()}")
    assert r.status_code == 404


def test_status_malformed_job_id_400(client):
    r = client.get("/v1/tts/jobs/not-a-uuid")
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Backpressure
# --------------------------------------------------------------------------- #
def test_backpressure_when_queue_saturated(client, monkeypatch):
    """Push the stream past the limit by stuffing it externally, then
    confirm POST /v1/tts/jobs returns 503 with Retry-After."""
    _enroll_voice(client)
    import asyncio

    async def _fill_queue():
        for i in range(110):  # well past the 100 limit set in the env fixture
            await client.fake_redis.xadd(
                "nqai.tts.jobs.test", {"payload": f"filler-{i}"}
            )

    asyncio.run(_fill_queue())

    r = _create_job(client, idempotency_key=str(uuid.uuid4()))
    assert r.status_code == 503
    assert "Retry-After" in r.headers


# --------------------------------------------------------------------------- #
# Cross-tenant isolation
# --------------------------------------------------------------------------- #
def test_status_of_another_tenants_job_returns_404(client, setup):
    """Mint a second tenant's API key, create a job under tenant A, then
    confirm tenant B cannot read it via /v1/tts/jobs/{id}."""
    _enroll_voice(client)
    rid = str(uuid.uuid4())
    _create_job(client, idempotency_key=rid)

    # Build tenant B + key.
    import asyncio

    from db import AsyncSessionLocal
    from repos import ApiKeyRepo, TenantRepo
    from server.security import generate_api_key

    async def _spawn_tenant_b():
        async with AsyncSessionLocal() as s:
            t = await TenantRepo(s).create(slug="tenant-b", display_name="B")
            full_key, prefix, secret_hash = generate_api_key("dev")
            await ApiKeyRepo(s).create(
                tenant_id=t.id,
                prefix=prefix,
                secret_hash=secret_hash,
                scopes=["tts:read", "tts:write", "voice:read", "voice:write"],
                rate_limit_per_minute=1000,
            )
            await s.commit()
            return full_key

    other_key = asyncio.run(_spawn_tenant_b())

    r = client.get(
        f"/v1/tts/jobs/{rid}", headers={"Authorization": f"Bearer {other_key}"}
    )
    assert r.status_code == 404  # existence-leak prevention


# --------------------------------------------------------------------------- #
# Auth gates (regression — TTS jobs share the require_auth pipeline)
# --------------------------------------------------------------------------- #
def test_create_job_requires_auth(client):
    client.headers.pop("Authorization", None)
    r = _create_job(client, idempotency_key=str(uuid.uuid4()))
    assert r.status_code == 401


def test_status_requires_auth(client):
    client.headers.pop("Authorization", None)
    r = client.get(f"/v1/tts/jobs/{uuid.uuid4()}")
    assert r.status_code == 401
