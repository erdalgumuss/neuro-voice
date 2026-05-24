"""Integration tests for Faz C wiring: /metrics endpoint + capacity-aware
backpressure paths in `_check_queue_depth_or_503`.

These tests don't exercise prometheus_client internals (that's
`test_observability_metrics.py`) — they verify the FastAPI plumbing that
the orchestrator added around the registry, plus the heartbeat-aware
admission logic that uses the gateway-side `read_cluster_capacity`.
"""

from __future__ import annotations

import asyncio
import io
import sys
import uuid

import fakeredis.aioredis
import numpy as np
import pytest
import soundfile as sf


def _make_wav_bytes(duration_s: float = 2.0, sr: int = 16000) -> bytes:
    audio = (np.random.randn(int(duration_s * sr)) * 0.1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
async def setup(monkeypatch, tmp_path):
    db_file = tmp_path / "metrics.db"
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
        if mod_name.startswith(
            ("server", "worker", "db", "repos", "frontend", "registry", "storage")
        ):
            del sys.modules[mod_name]

    from db import AsyncSessionLocal, init_models_for_tests
    from repos import ApiKeyRepo, TenantRepo
    from server.security import generate_api_key

    await init_models_for_tests(db_url)

    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tenant = await TenantRepo(s).create(slug="m-tenant", display_name="M")
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
            c.fake_queue = fake_queue
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


def _seed_heartbeat(
    redis,
    *,
    worker_id: str,
    capacity: int,
    in_flight: int,
) -> None:
    """Drop a fake worker heartbeat hash with fresh last_pickup_ms so the
    gateway sees `worker_count >= 1`."""
    import time

    async def _do():
        now = int(time.time() * 1000)
        await redis.hset(
            f"nqai.worker.heartbeat.{worker_id}",
            mapping={
                "capacity": str(capacity),
                "in_flight": str(in_flight),
                "updated_at_ms": str(now),
                "last_pickup_ms": str(now),
                "started_at_ms": str(now - 10_000),
            },
        )
        await redis.pexpire(f"nqai.worker.heartbeat.{worker_id}", 5_000)

    asyncio.run(_do())


# ============================================================================ #
# /metrics endpoint
# ============================================================================ #


def test_metrics_endpoint_returns_200_with_prometheus_text(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    # prometheus_client.exposition.CONTENT_TYPE_LATEST →
    # 'text/plain; version=0.0.4; charset=utf-8'
    assert r.headers["content-type"].startswith("text/plain")
    # At least one of our metrics should appear in the body.
    body = r.text
    assert "nqai_tts_requests_total" in body
    assert "nqai_worker_count" in body
    assert "nqai_queue_depth" in body


def test_metrics_endpoint_refreshes_gauges_from_heartbeats(client):
    """A single scrape must pull worker_count / capacity / inflight from
    the heartbeat hashes we seed into fake_redis. If the refresh path is
    broken (wrong queue.redis, exception, etc.) the gauges stay at zero
    and the test fails loudly."""
    # Seed two healthy workers (cluster_capacity = 8, in_flight = 3).
    _seed_heartbeat(client.fake_redis, worker_id="w1", capacity=5, in_flight=2)
    _seed_heartbeat(client.fake_redis, worker_id="w2", capacity=3, in_flight=1)

    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text

    # Gauges are scraped with `# HELP` line + `# TYPE` line + sample.
    assert "nqai_worker_count 2.0" in body
    assert "nqai_worker_capacity_total 8.0" in body
    assert "nqai_worker_inflight_total 3.0" in body


def test_metrics_endpoint_survives_redis_blip(client, monkeypatch):
    """If `read_cluster_capacity` blows up, /metrics must still return
    200 with the previously-set gauge values rather than 5xx (Prometheus
    would alarm)."""
    # Force read_cluster_capacity to raise on the next call.
    import server.main as server_main

    async def _boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("simulated redis failure")

    monkeypatch.setattr(server_main, "read_cluster_capacity", _boom)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")


# ============================================================================ #
# Capacity-aware backpressure
# ============================================================================ #


def test_backpressure_admits_when_capacity_available(client):
    """One worker, capacity 8, 0 in-flight, queue empty → admit."""
    _enroll_voice(client)
    _seed_heartbeat(client.fake_redis, worker_id="w1", capacity=8, in_flight=0)

    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
    assert r.status_code == 202, r.text


def test_backpressure_denies_when_capacity_exhausted(client):
    """Cluster says capacity=4 / in_flight=4 (0 headroom), and the queue
    already has 10 pending jobs. Even though XLEN (10) < limit (100),
    the capacity-aware path should deny: depth (10) > headroom (0) +
    total_capacity (4) = 4."""
    _enroll_voice(client)
    _seed_heartbeat(client.fake_redis, worker_id="w1", capacity=4, in_flight=4)

    # Stuff the queue so depth = 10 (still well below the XLEN ceiling of 100).
    async def _fill():
        for i in range(10):
            await client.fake_redis.xadd(
                "nqai.tts.jobs.test", {"payload": f"filler-{i}"}
            )

    asyncio.run(_fill())

    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
    assert r.status_code == 503, r.text
    assert r.headers.get("Retry-After") == "5"


def test_backpressure_falls_back_to_xlen_when_no_heartbeats(client):
    """Cold start: no worker has heartbeated yet. The gateway must NOT
    deny everything (worker_count=0 doesn't mean "no capacity" — it
    means "unknown"). Without filling the queue past the XLEN ceiling,
    admission should succeed."""
    _enroll_voice(client)
    # No heartbeats seeded. Queue is empty.
    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
    assert r.status_code == 202, r.text


def test_backpressure_admits_at_capacity_with_one_cluster_pass_backlog(client):
    """Pin the admission policy: even at 100 % cluster utilisation
    (in_flight == capacity, headroom == 0), the gateway admits while
    queue depth ≤ total_capacity — i.e. up to one full cluster-pass
    of backlog. This is a deliberate trade-off for multi-second TTS
    jobs (Codex audit 2026-05-24). A future refactor that tightens
    this to `depth ≤ headroom` would surface here.
    """
    _enroll_voice(client)
    # One worker, capacity=4, fully utilised (in_flight=4, headroom=0).
    _seed_heartbeat(client.fake_redis, worker_id="w1", capacity=4, in_flight=4)

    # 3 jobs queued (< total_capacity=4). depth (3) <= 0 + 4 = 4 → admit.
    async def _fill():
        for i in range(3):
            await client.fake_redis.xadd(
                "nqai.tts.jobs.test", {"payload": f"filler-{i}"}
            )

    asyncio.run(_fill())

    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
    assert r.status_code == 202, r.text


def test_backpressure_denies_at_capacity_when_backlog_exceeds_one_cluster_pass(client):
    """Boundary: same fully-utilised cluster, but depth > total_capacity.
    Pinning the other side of the policy."""
    _enroll_voice(client)
    _seed_heartbeat(client.fake_redis, worker_id="w1", capacity=4, in_flight=4)

    # 5 jobs queued (> total_capacity=4). depth (5) > 0 + 4 = 4 → deny.
    async def _fill():
        for i in range(5):
            await client.fake_redis.xadd(
                "nqai.tts.jobs.test", {"payload": f"filler-{i}"}
            )

    asyncio.run(_fill())

    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
    assert r.status_code == 503, r.text


def test_backpressure_xlen_fallback_still_denies_when_queue_full(client):
    """No heartbeats AND queue past the XLEN ceiling → 503 via the
    fallback path. Confirms the XLEN-only branch isn't dead."""
    _enroll_voice(client)

    async def _fill():
        for i in range(110):
            await client.fake_redis.xadd(
                "nqai.tts.jobs.test", {"payload": f"filler-{i}"}
            )

    asyncio.run(_fill())

    r = client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"text": "Merhaba.", "voice_id": "demo-01"},
    )
    assert r.status_code == 503, r.text
