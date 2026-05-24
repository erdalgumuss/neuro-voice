"""Minimal end-to-end test: gateway POST → worker consumes → GET complete.

This is the canonical "async omurga gerçekten çalışıyor mu" test.
No real GPU, no real Redis, no real R2 — but the gateway is the actual
FastAPI app, the worker is the actual WorkerConsumer, and the queue
is the actual TtsJobQueue (over fakeredis). The contract being
verified is the wire boundary: a POST that returns 202 must, given a
running worker, result in a GET that returns complete + audio_url.

If this regresses, the worker split is broken. Faster signal than
manual `docker compose up` smoke testing.
"""

from __future__ import annotations

import asyncio
import io
import sys
import types as _types
import uuid
from dataclasses import dataclass

import fakeredis.aioredis
import numpy as np
import pytest
import soundfile as sf

# Stub voxcpm before any server.* / worker.* import.
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


@dataclass
class _FakeChunk:
    pcm_int16: bytes
    sample_rate: int = 48000
    sentence_index: int = 0
    sentence_text: str = ""
    elapsed_ms: float = 1.0


class _StubEngine:
    sample_rate = 48000

    def warmup(self) -> None:
        pass

    def synthesize_stream(self, *, text, voice, reference_path, language_id="tr"):
        # 2 sentences of silence.
        for i, s in enumerate(("İlk cümle.", "İkinci cümle.")):
            yield _FakeChunk(
                pcm_int16=b"\x00\x00" * 1024,
                sample_rate=self.sample_rate,
                sentence_index=i,
                sentence_text=s,
            )


def _make_wav_bytes(duration_s: float = 1.0, sr: int = 16000) -> bytes:
    audio = (np.random.randn(int(duration_s * sr)) * 0.1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    """Boot a fresh gateway + tenant + API key. Returns the auth bearer
    plus everything needed to spawn a worker bound to the same
    fakeredis."""
    db_file = tmp_path / "e2e.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")
    monkeypatch.setenv("NQAI_REQUIRE_AUTH", "true")
    monkeypatch.setenv("NQAI_REFERENCE_DIR", str(ref_dir))
    monkeypatch.setenv("NQAI_COOKIE_SECURE", "false")

    for mod in list(sys.modules):
        if mod.startswith(("server", "worker", "db", "repos", "frontend",
                            "registry", "storage")):
            del sys.modules[mod]

    from db import AsyncSessionLocal, init_models_for_tests
    from repos import ApiKeyRepo, TenantRepo
    from server.security import generate_api_key

    await init_models_for_tests(db_url)

    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tenant = await TenantRepo(s).create(slug="e2e-tenant", display_name="E2E")
        await ApiKeyRepo(s).create(
            tenant_id=tenant.id, prefix=prefix, secret_hash=secret_hash,
            scopes=["tts:read", "tts:write", "voice:read", "voice:write"],
            rate_limit_per_minute=1000,
        )
        await s.commit()

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    async def archive_local(rid: uuid.UUID, pcm: bytes, sr: int) -> str:
        from audio.wav import pcm16_to_wav_bytes
        path = artifact_dir / f"{rid}.wav"
        path.write_bytes(pcm16_to_wav_bytes(pcm, sample_rate=sr))
        return f"file://{path}"

    return {
        "bearer": full_key,
        "tenant_id": tenant.id,
        "ref_dir": ref_dir,
        "artifact_dir": artifact_dir,
        "archive": archive_local,
    }


def _enroll_voice(client, voice_id: str = "demo-01") -> None:
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={"voice_id": voice_id, "display_name": "Demo"},
        files={"reference_audio": ("d.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# E2E: POST → worker → GET complete
# --------------------------------------------------------------------------- #
async def test_async_job_completes_when_worker_is_running(setup):
    """The full async omurga: POST a job, worker consumes it, GET
    returns complete + audio_url (presigned for s3:// / file:// for dev).

    The worker runs as an asyncio.Task in this event loop — same loop
    as the FastAPI app — so we can drive everything from one coroutine."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue
    from worker.consumer import WorkerConsumer

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    # Start the worker as a background task. block_ms=10 + tiny xautoclaim
    # period keep ticks fast; stop_event lets us shut it down at teardown.
    stop = asyncio.Event()
    consumer = WorkerConsumer(
        redis=fake_redis, engine=_StubEngine(),
        archive_to_r2=setup["archive"],
        stop_event=stop, block_ms=10,
        xautoclaim_period_s=1.0,
    )
    worker_task = asyncio.create_task(consumer.run())

    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {setup['bearer']}"
            _enroll_voice(client)

            rid = str(uuid.uuid4())
            r = client.post(
                "/v1/tts/jobs",
                headers={"Idempotency-Key": rid, "X-NQAI-App": "e2e-test"},
                json={"text": "Merhaba dünya.", "voice_id": "demo-01"},
            )
            assert r.status_code == 202, r.text
            assert r.json()["status"] == "queued"

            # Poll until complete (or 5s timeout — generous for fakeredis).
            deadline = asyncio.get_event_loop().time() + 5.0
            body = None
            while asyncio.get_event_loop().time() < deadline:
                r = client.get(f"/v1/tts/jobs/{rid}")
                assert r.status_code == 200, r.text
                body = r.json()
                if body["status"] == "complete":
                    break
                await asyncio.sleep(0.05)
            assert body is not None
            assert body["status"] == "complete", f"never completed: {body}"
            assert body["output"]["audio_url"].startswith(
                ("file://", "https://", "s3://"),
            ), body
            assert body["output"]["content_type"] == "audio/wav"
            # Usage row should have the X-NQAI-App label.
            assert body["metrics"]["rtf"] is not None

        # Verify the artifact actually exists on disk (file:// fallback).
        artifacts = list(setup["artifact_dir"].iterdir())
        assert len(artifacts) == 1
        assert artifacts[0].read_bytes()[:4] == b"RIFF"  # valid WAV
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_gateway_idempotency_complete_status_after_worker(setup):
    """Same key replay AFTER worker completed must return deduplicated +
    status=complete (no double-processing)."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue
    from worker.consumer import WorkerConsumer

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    stop = asyncio.Event()
    consumer = WorkerConsumer(
        redis=fake_redis, engine=_StubEngine(),
        archive_to_r2=setup["archive"],
        stop_event=stop, block_ms=10,
    )
    worker_task = asyncio.create_task(consumer.run())

    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {setup['bearer']}"
            _enroll_voice(client)
            rid = str(uuid.uuid4())

            r1 = client.post(
                "/v1/tts/jobs",
                headers={"Idempotency-Key": rid},
                json={"text": "Replay testi.", "voice_id": "demo-01"},
            )
            assert r1.status_code == 202

            # Wait for worker to complete.
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                r = client.get(f"/v1/tts/jobs/{rid}")
                if r.json()["status"] == "complete":
                    break
                await asyncio.sleep(0.05)

            # Now replay POST with the same key + same body.
            r2 = client.post(
                "/v1/tts/jobs",
                headers={"Idempotency-Key": rid},
                json={"text": "Replay testi.", "voice_id": "demo-01"},
            )
            assert r2.status_code == 202
            body = r2.json()
            assert body["job_id"] == rid
            assert body["deduplicated"] is True
            assert body["status"] == "complete"  # already done — gateway echoes it
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()
