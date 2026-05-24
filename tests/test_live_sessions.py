"""Live TTS session API — B.1.5 WebRTC-first control plane."""

from __future__ import annotations

import io
import sys
import types as _types

import fakeredis.aioredis
import jwt
import numpy as np
import pytest
import soundfile as sf

_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm_model = _types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")
_fake_voxcpm.VoxCPM = type("StubFactory", (), {
    "from_pretrained": staticmethod(lambda *a, **kw: object()),
})
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
    db_file = tmp_path / "live.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")
    monkeypatch.setenv("NQAI_REQUIRE_AUTH", "true")
    monkeypatch.setenv("NQAI_REFERENCE_DIR", str(ref_dir))
    monkeypatch.setenv("NQAI_COOKIE_SECURE", "false")
    monkeypatch.setenv("NQAI_LIVEKIT_URL", "ws://livekit:7880")
    monkeypatch.setenv("NQAI_LIVEKIT_PUBLIC_URL", "ws://localhost:7880")
    monkeypatch.setenv("NQAI_LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("NQAI_LIVEKIT_API_SECRET", "test-livekit-secret-at-least-32-bytes")

    for mod_name in list(sys.modules):
        if mod_name.startswith((
            "server", "worker", "db", "repos", "frontend", "registry", "storage", "live"
        )):
            del sys.modules[mod_name]

    from db import AsyncSessionLocal, init_models_for_tests
    from repos import ApiKeyRepo, TenantRepo
    from server.security import generate_api_key

    await init_models_for_tests(db_url)
    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tenant = await TenantRepo(s).create(slug="live-tenant", display_name="Live")
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
    fake_queue = TtsJobQueue(fake_redis, stream="nqai.tts.jobs.live-test")

    from fastapi.testclient import TestClient

    from server.main import app

    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: fake_queue
    try:
        with TestClient(app) as c:
            c.headers.update({"Authorization": f"Bearer {full_key}"})
            c.fake_redis = fake_redis
            yield c
    finally:
        app.dependency_overrides.clear()


def _enroll_voice(client, voice_id: str = "live-voice"):
    r = client.post(
        "/v1/voices",
        data={"voice_id": voice_id, "display_name": "Live Voice"},
        files={"reference_audio": ("live.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert r.status_code == 200, r.text


def test_live_session_requires_warm_worker_capacity(client):
    _enroll_voice(client)
    r = client.post("/v1/tts/live/sessions", json={"voice_id": "live-voice"})
    assert r.status_code == 503


def test_live_session_returns_livekit_token_and_stores_session(client):
    import asyncio

    from live import LiveSessionAssignment, LiveSessionStore, LiveWorkerRegistry
    from live.sessions import live_assignment_stream

    _enroll_voice(client)

    async def _heartbeat():
        await LiveWorkerRegistry(client.fake_redis).heartbeat(
            worker_id="worker-live-1",
            model_id="openbmb/VoxCPM2",
            device="cuda",
            warm=True,
            active_live_sessions=0,
            max_live_sessions=1,
            current_voice_ids=[],
        )

    asyncio.run(_heartbeat())

    r = client.post(
        "/v1/tts/live/sessions",
        json={"voice_id": "live-voice", "client_request_created_ms": 1000},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["livekit_url"] == "ws://localhost:7880"
    assert body["audio_codec"] == "opus"
    assert body["control_protocol"] == "nqai.tts.live.v1"
    assert body["worker_id"] == "worker-live-1"
    assert body["metrics"]["client_request_created_ms"] == 1000
    assert body["metrics"]["session_admitted_ms"] is not None

    claims = jwt.decode(
        body["participant_token"],
        "test-livekit-secret-at-least-32-bytes",
        algorithms=["HS256"],
    )
    assert claims["iss"] == "devkey"
    assert claims["sub"] == f"client-{body['session_id']}"
    assert claims["video"]["room"] == body["room_name"]
    assert claims["video"]["roomJoin"] is True

    async def _read_session():
        return await LiveSessionStore(client.fake_redis).get(body["session_id"])

    stored = asyncio.run(_read_session())
    assert stored is not None
    assert stored.worker_id == "worker-live-1"
    assert stored.voice_id == "live-voice"

    async def _read_assignment():
        entries = await client.fake_redis.xread(
            {live_assignment_stream("worker-live-1"): "0"}, count=1
        )
        return LiveSessionAssignment.decode(entries[0][1][0][1])

    assignment = asyncio.run(_read_assignment())
    assert assignment.session.session_id == body["session_id"]
    assert assignment.livekit_url == "ws://livekit:7880"
