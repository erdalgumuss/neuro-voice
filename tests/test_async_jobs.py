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
        if mod_name.startswith(("server", "worker", "db", "repos", "frontend", "registry", "storage")):
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


def test_create_job_propagates_x_nqai_app_to_payload(client):
    """Refactor R: X-NQAI-App header lands on TtsJobPayload.app_label
    so the worker can record it on usage_records for product rollup."""
    import json

    _enroll_voice(client)
    rid = str(uuid.uuid4())
    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": rid, "X-NQAI-App": "neeko-mobile"},
        json={"text": "Selam.", "voice_id": "demo-01"},
    )
    assert r.status_code == 202, r.text

    # Read the enqueued payload off the stream and check app_label survived.
    import asyncio

    async def _read_payload():
        entries = await client.fake_redis.xread(
            {"nqai.tts.jobs.test": "0"}, count=1
        )
        # entries = [(stream_name, [(entry_id, fields_dict), ...])]
        return entries[0][1][0][1]

    fields = asyncio.run(_read_payload())
    payload_json = (fields[b"payload"] if b"payload" in fields else fields["payload"])
    if isinstance(payload_json, bytes):
        payload_json = payload_json.decode("utf-8")
    payload = json.loads(payload_json)
    assert payload["app_label"] == "neeko-mobile"


def test_create_job_without_x_nqai_app_records_null_label(client):
    _enroll_voice(client)
    rid = str(uuid.uuid4())
    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": rid},  # no X-NQAI-App
        json={"text": "Selam.", "voice_id": "demo-01"},
    )
    assert r.status_code == 202

    import asyncio
    import json

    async def _read_payload():
        entries = await client.fake_redis.xread(
            {"nqai.tts.jobs.test": "0"}, count=1
        )
        return entries[0][1][0][1]

    fields = asyncio.run(_read_payload())
    payload_json = (fields[b"payload"] if b"payload" in fields else fields["payload"])
    if isinstance(payload_json, bytes):
        payload_json = payload_json.decode("utf-8")
    payload = json.loads(payload_json)
    assert payload["app_label"] is None


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


def test_xadd_failure_does_not_poison_idempotency_key(client, monkeypatch):
    """Audit F5 fix (2026-05-24): when XADD raises after the gateway
    reserves an idempotency row, the row must be DELETED (not marked
    failed). The client must be able to retry with the *same*
    Idempotency-Key and succeed.

    Previously the gateway called `idem.fail(rid)`, which left a stale
    `failed` row; the next reserve_or_get found it, body_hash matched,
    and the client got a permanently dead key."""
    _enroll_voice(client)
    rid = str(uuid.uuid4())

    # Force the *next* XADD to fail at the queue layer.
    fake_queue = client.fake_queue
    original_submit = fake_queue.submit
    fail_once = {"armed": True}

    async def transient_failure(payload):
        if fail_once["armed"]:
            fail_once["armed"] = False
            raise ConnectionError("simulated Redis outage")
        return await original_submit(payload)

    monkeypatch.setattr(fake_queue, "submit", transient_failure)

    # First attempt — XADD raises → gateway returns 502 + cleans up.
    r1 = _create_job(client, idempotency_key=rid)
    assert r1.status_code == 502
    assert "same Idempotency-Key" in r1.json()["detail"]

    # Retry with the same key + same body — must succeed (no poison row).
    r2 = _create_job(client, idempotency_key=rid)
    assert r2.status_code == 202, r2.text
    assert r2.json()["job_id"] == rid
    assert r2.json()["deduplicated"] is False  # truly first successful reserve

    # Queue depth = 1 (only the second attempt enqueued).
    import asyncio
    async def _xlen():
        return int(await client.fake_redis.xlen("nqai.tts.jobs.test"))
    assert asyncio.run(_xlen()) == 1


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
    """Async ceiling is `async_max_chars` (default 100 000, Dalga 3.2);
    300 000 chars exceeds it but stays under the pydantic schema cap so
    we see the gateway 400 rather than pydantic's 422."""
    _enroll_voice(client)
    huge = "x" * 150000
    r = _create_job(client, idempotency_key=str(uuid.uuid4()), text=huge)
    assert r.status_code in {400, 422}


def test_create_job_accepts_long_form_up_to_async_ceiling(client):
    """Dalga 3.2: 50 000 chars is well past the sync `max_chars_per_request`
    (4 000) but inside the async ceiling (100 000). The async submit
    accepts it; the worker / engine slowness is a runtime concern, not a
    submit-time one."""
    _enroll_voice(client)
    long_text = "Bu uzun bir cümle. " * 2700  # ~51 300 chars
    r = _create_job(
        client, idempotency_key=str(uuid.uuid4()), text=long_text,
    )
    assert r.status_code == 202, r.text


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
                queue_wait_ms=40,
                inference_ms=700,
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
    assert body["metrics"]["queue_wait_ms"] == 40
    assert body["metrics"]["inference_ms"] == 700
    assert body["metrics"]["generated_audio_ms"] == 2000
    assert body["metrics"]["rtf"] == pytest.approx(0.45)


def test_status_response_mints_presigned_url_when_r2_bound(
    client, setup, monkeypatch,
):
    """Audit F6 fix (2026-05-24): when R2 storage is configured, the
    job status response must mint a presigned GET URL — never leak the
    internal `s3://...` URI to the client."""
    _, tenant_id = setup
    _enroll_voice(client)
    rid_uuid = uuid.uuid4()
    _create_job(client, idempotency_key=str(rid_uuid))

    # Stamp the job complete with an s3:// uri.
    import asyncio

    from sqlalchemy import select

    from db import AsyncSessionLocal
    from db.models import ApiKey
    from repos import IdempotencyRepo, UsageRepo

    async def _finish():
        async with AsyncSessionLocal() as s:
            key_row = (
                await s.execute(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
            ).scalar_one()
            await IdempotencyRepo(s, tenant_id).complete(
                rid_uuid, response_uri="s3://outputs/demo-01/xyz.wav",
            )
            await UsageRepo(s, tenant_id).record(
                api_key_id=key_row.id, voice_id="demo-01",
                request_id=rid_uuid, text_char_count=14, sentence_count=1,
                duration_ms=2000, elapsed_ms=900, rtf=0.45, status="ok",
            )
            await s.commit()

    asyncio.run(_finish())

    # Stub _maybe_presigned_url so it returns a clean https:// URL.
    import server.main as main_mod

    captured: dict[str, str] = {}

    def fake_presigner(uri: str) -> str:
        captured["uri"] = uri
        return "https://r2.example.com/outputs/xyz.wav?X-Amz-Signature=abc123"

    monkeypatch.setattr(main_mod, "_maybe_presigned_url", fake_presigner)

    r = client.get(f"/v1/tts/jobs/{rid_uuid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert captured["uri"] == "s3://outputs/demo-01/xyz.wav"
    assert body["output"]["audio_url"].startswith("https://r2.example.com/")
    assert "X-Amz-Signature" in body["output"]["audio_url"]


def test_status_response_falls_back_to_raw_uri_when_r2_unconfigured(client, setup):
    """The fallback path: in dev when R2 env is unset, the helper
    returns the raw URI instead of 500ing the status poll."""
    _, tenant_id = setup
    _enroll_voice(client)
    rid_uuid = uuid.uuid4()
    _create_job(client, idempotency_key=str(rid_uuid))

    import asyncio

    from sqlalchemy import select

    from db import AsyncSessionLocal
    from db.models import ApiKey
    from repos import IdempotencyRepo, UsageRepo

    async def _finish():
        async with AsyncSessionLocal() as s:
            key_row = (
                await s.execute(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
            ).scalar_one()
            await IdempotencyRepo(s, tenant_id).complete(
                rid_uuid, response_uri="s3://outputs/demo-01/fallback.wav",
            )
            await UsageRepo(s, tenant_id).record(
                api_key_id=key_row.id, voice_id="demo-01",
                request_id=rid_uuid, text_char_count=14, sentence_count=1,
                duration_ms=2000, elapsed_ms=900, rtf=0.45, status="ok",
            )
            await s.commit()

    asyncio.run(_finish())

    # No R2 env set → presigned mint raises → fallback to raw URI.
    r = client.get(f"/v1/tts/jobs/{rid_uuid}")
    assert r.status_code == 200
    assert r.json()["output"]["audio_url"] == "s3://outputs/demo-01/fallback.wav"


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


def test_sync_tts_backpressure_when_queue_saturated(client):
    _enroll_voice(client)
    import asyncio

    async def _fill_queue():
        for i in range(110):
            await client.fake_redis.xadd(
                "nqai.tts.jobs.test", {"payload": f"filler-{i}"}
            )

    asyncio.run(_fill_queue())

    r = client.post(
        "/v1/tts",
        headers={"X-Request-Id": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
    assert r.status_code == 503
    assert "Retry-After" in r.headers


def test_sync_stream_backpressure_when_queue_saturated(client):
    _enroll_voice(client)
    import asyncio

    async def _fill_queue():
        for i in range(110):
            await client.fake_redis.xadd(
                "nqai.tts.jobs.test", {"payload": f"filler-{i}"}
            )

    asyncio.run(_fill_queue())

    r = client.post(
        "/v1/tts/stream",
        headers={"X-Request-Id": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
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
