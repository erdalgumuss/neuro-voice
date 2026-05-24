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

        # Faz C step 1 — the full latency waterfall must be persisted on
        # the usage row at job completion. Worker captured
        # worker_pickup_ms from payload.enqueued_at_ms, pipeline filled
        # reference_resolve_ms / first_pcm_ms / first_audio_ms.
        from db import AsyncSessionLocal
        from repos import UsageRepo
        async with AsyncSessionLocal() as s:
            usage_rows = await UsageRepo(s, setup["tenant_id"]).recent(limit=5)
        assert usage_rows, "worker never wrote a usage row"
        row = usage_rows[0]
        assert row.worker_pickup_ms is not None
        assert row.worker_pickup_ms >= 0
        assert row.reference_resolve_ms is not None
        assert row.reference_resolve_ms >= 0
        assert row.first_pcm_ms is not None
        assert row.first_pcm_ms >= 0
        assert row.first_audio_ms is not None
        assert row.first_audio_ms >= row.first_pcm_ms
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_sync_tts_proxy_returns_wav_via_worker(setup):
    """Faz B.1 step 3: sync /v1/tts is a queue proxy. Gateway XADD's
    the job, worker consumes it, gateway concatenates result-stream
    chunks into a WAV body. Same client-facing contract as the old
    in-process path.

    Uses httpx.AsyncClient (ASGI transport) so the worker task running
    in the same event loop actually gets scheduling time during the
    sync POST — TestClient's sync API blocks the loop and starves the
    worker, which would manifest as a 504."""
    import io
    import wave

    import httpx

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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
            headers={"Authorization": f"Bearer {setup['bearer']}"},
            timeout=10.0,
        ) as client:
            # Enroll a voice (multipart upload).
            wav = _make_wav_bytes()
            r_enroll = await client.post(
                "/v1/voices",
                data={"voice_id": "sync-proxy-voice", "display_name": "P"},
                files={"reference_audio": ("d.wav", wav, "audio/wav")},
            )
            assert r_enroll.status_code == 200, r_enroll.text

            r = await client.post(
                "/v1/tts",
                headers={"X-NQAI-App": "sync-proxy-test"},
                json={
                    "text": "Merhaba dünya.",
                    "voice_id": "sync-proxy-voice",
                },
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith("audio/wav")
            # Deprecation contract (RFC 8594) — clients see the sunset
            # signal so they know to migrate to /v1/tts/jobs.
            assert r.headers["deprecation"] == "true"
            assert "sunset" in r.headers
            assert "/v1/tts/jobs" in r.headers["link"]
            # WAV body is a valid RIFF.
            with wave.open(io.BytesIO(r.content), "rb") as w:
                assert w.getnchannels() == 1
                assert w.getsampwidth() == 2
                assert w.getframerate() == 48000
                assert w.getnframes() > 0
            assert int(r.headers["x-nqai-sentences"]) >= 1
            assert int(r.headers["x-nqai-sample-rate"]) == 48000
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_sync_tts_proxy_504_when_no_worker(setup, monkeypatch):
    """If no worker consumes the job, the gateway proxy times out and
    returns 504 — never hangs the client forever."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue

    monkeypatch.setenv("NQAI_SYNC_TIMEOUT_S", "0.3")

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {setup['bearer']}"
            _enroll_voice(client, voice_id="lonely-voice")

            r = client.post(
                "/v1/tts",
                json={"text": "Hiç worker yok.", "voice_id": "lonely-voice"},
            )
            assert r.status_code == 504
            assert "/v1/tts/jobs" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


async def test_first_chunk_in_result_stream_before_engine_finishes(setup):
    """B.1.5 contract — measured at the worker→gateway result stream
    boundary, not the HTTP wire (httpx ASGITransport buffers the
    response body until the generator completes, which would mask the
    bridge under test).

    Engine yields sentence 1 at t=0, sleeps 300ms, yields sentence 2,
    sleeps 300ms, yields sentence 3, sleeps 300ms — total ~900ms.
    Bridge must XADD sentence 1 to the result stream BEFORE the worker
    finishes (i.e. before XACK fires). Drain-then-emit would publish
    all three after ~900ms in one burst, after XACK is imminent.
    """
    import time as _time

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue, result_stream_name
    from worker.consumer import WorkerConsumer

    class _SlowEngine(_StubEngine):
        def synthesize_stream(self, **kw):
            for i, s in enumerate(("Bir.", "İki.", "Üç.")):
                yield _FakeChunk(
                    pcm_int16=b"\x00\x00" * 480,
                    sample_rate=self.sample_rate,
                    sentence_index=i,
                    sentence_text=s,
                )
                _time.sleep(0.3)

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    stop = asyncio.Event()
    consumer = WorkerConsumer(
        redis=fake_redis, engine=_SlowEngine(),
        archive_to_r2=setup["archive"],
        stop_event=stop, block_ms=10,
    )
    worker_task = asyncio.create_task(consumer.run())

    try:
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {setup['bearer']}"
            _enroll_voice(client, voice_id="slow-voice")

            rid = str(uuid.uuid4())
            stream_name = result_stream_name(rid)
            r = client.post(
                "/v1/tts/jobs",
                headers={"Idempotency-Key": rid},
                json={"text": "Bir. İki. Üç.", "voice_id": "slow-voice"},
            )
            assert r.status_code == 202

            # Poll the result stream WHILE the engine is still working.
            # We want: stream_len > 0 BEFORE consumer.acked > 0.
            saw_chunk_before_finish = False
            saw_chunk_at_ms: float | None = None
            t0 = _time.monotonic()
            for _ in range(60):  # up to 1.2s — safely under the 1.5s+ ack
                xlen = int(await fake_redis.xlen(stream_name))
                if xlen > 0 and consumer.acked == 0:
                    saw_chunk_before_finish = True
                    saw_chunk_at_ms = (_time.monotonic() - t0) * 1000
                    break
                if consumer.acked > 0:
                    break  # job already done — too slow to observe
                await asyncio.sleep(0.02)

            assert saw_chunk_before_finish, (
                "no result-stream chunk visible before worker XACK — "
                "pipeline regressed to drain-then-emit"
            )
            # And the chunk landed in the first sentence's window
            # (~0-400ms after request started, well under 900ms total
            # inference).
            assert saw_chunk_at_ms is not None and saw_chunk_at_ms < 600, (
                f"first chunk at {saw_chunk_at_ms:.1f}ms — bridge present "
                "but too slow"
            )
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_sync_tts_stream_yields_opus_ogg(setup):
    """Faz B.5 Dalga 1 — codec layer E2E. Request audio_format=opus,
    assert the response body is a valid OGG/opus stream end-to-end:
    starts with `OggS` capture pattern, contains an `OpusHead` packet,
    and finishes with the OGG end-of-stream page (ffmpeg flushes on
    stdin close inside encoder.close())."""
    import shutil

    import httpx

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH — codec E2E requires it")

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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
            headers={"Authorization": f"Bearer {setup['bearer']}"},
            timeout=15.0,
        ) as client:
            wav = _make_wav_bytes()
            await client.post(
                "/v1/voices",
                data={"voice_id": "opus-voice", "display_name": "Opus"},
                files={"reference_audio": ("o.wav", wav, "audio/wav")},
            )
            async with client.stream(
                "POST",
                "/v1/tts/stream",
                json={
                    "text": "Birinci. İkinci.",
                    "voice_id": "opus-voice",
                    "audio_format": "opus",
                },
            ) as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("audio/ogg")
                body = b""
                async for piece in r.aiter_bytes():
                    body += piece
            assert body[:4] == b"OggS", (
                f"expected OGG capture pattern, got {body[:8]!r}"
            )
            assert b"OpusHead" in body[:128], (
                "OpusHead identification packet missing from first OGG page"
            )
            # Sanity: a 2-sentence stub produces multiple OGG pages.
            assert body.count(b"OggS") >= 2, (
                "expected multiple OGG pages — encoder may have closed early"
            )
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_sync_tts_stream_yields_mp3(setup):
    """Faz B.5 Dalga 1 — mp3 codec via /v1/tts/stream. Body must start
    with either an ID3v2 tag (default ffmpeg writes one) or an mp3
    frame sync word."""
    import shutil

    import httpx

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH — codec E2E requires it")

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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
            headers={"Authorization": f"Bearer {setup['bearer']}"},
            timeout=15.0,
        ) as client:
            wav = _make_wav_bytes()
            await client.post(
                "/v1/voices",
                data={"voice_id": "mp3-voice", "display_name": "Mp3"},
                files={"reference_audio": ("m.wav", wav, "audio/wav")},
            )
            async with client.stream(
                "POST",
                "/v1/tts/stream",
                json={
                    "text": "Bir.",
                    "voice_id": "mp3-voice",
                    "audio_format": "mp3",
                },
            ) as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("audio/mpeg")
                body = b""
                async for piece in r.aiter_bytes():
                    body += piece
            head = body[:256]
            assert head.startswith(b"ID3") or any(
                head[i] == 0xFF and (head[i + 1] & 0xE0) == 0xE0
                for i in range(len(head) - 1)
            ), f"no ID3 / mp3 sync in head: {head[:32]!r}"
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_sync_tts_stream_proxy_yields_riff_wav(setup):
    """The /v1/tts/stream variant — same proxy path but chunks
    forwarded as HTTP chunked transfer. Client sees a valid RIFF/WAVE
    header up front, then PCM bytes. AsyncClient for the same
    event-loop-sharing reason as the sync proxy test above."""
    import httpx

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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
            headers={"Authorization": f"Bearer {setup['bearer']}"},
            timeout=10.0,
        ) as client:
            wav = _make_wav_bytes()
            await client.post(
                "/v1/voices",
                data={"voice_id": "stream-voice", "display_name": "S"},
                files={"reference_audio": ("s.wav", wav, "audio/wav")},
            )
            async with client.stream(
                "POST",
                "/v1/tts/stream",
                json={
                    "text": "Birinci cümle. İkinci cümle.",
                    "voice_id": "stream-voice",
                },
            ) as r:
                chunks = b""
                async for piece in r.aiter_bytes():
                    chunks += piece
            assert chunks.startswith(b"RIFF")
            assert b"WAVE" in chunks[:20]
            assert len(chunks) > 44  # header + at least one PCM chunk
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_stream_persists_gateway_first_byte_ms_on_usage_row(setup):
    """Faz C v1 item 1 — gateway-side TTFB makes it into usage_records.

    The worker writes the usage row when its pipeline commits; the
    gateway then UPDATEs the `gateway_first_byte_ms` column with the
    time between request-received and first-chunk-yielded. After the
    stream ends, the column must be populated and non-negative.
    """
    import httpx
    from sqlalchemy import select

    from db import AsyncSessionLocal
    from db.models import UsageRecord
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
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test",
            headers={"Authorization": f"Bearer {setup['bearer']}"},
            timeout=10.0,
        ) as client:
            wav = _make_wav_bytes()
            await client.post(
                "/v1/voices",
                data={"voice_id": "tfb-voice", "display_name": "TFB"},
                files={"reference_audio": ("s.wav", wav, "audio/wav")},
            )
            async with client.stream(
                "POST",
                "/v1/tts/stream",
                json={"text": "Merhaba.", "voice_id": "tfb-voice"},
            ) as r:
                request_id = r.headers["X-NQAI-Request-Id"]
                async for _ in r.aiter_bytes():
                    pass

        # Streaming generator's finally block runs the UPDATE before the
        # response truly closes, but the ASGI shutdown ordering is loose
        # enough that we poll briefly for the column to appear.
        gateway_ms = None
        for _ in range(20):
            async with AsyncSessionLocal() as s:
                row = (await s.execute(
                    select(UsageRecord).where(
                        UsageRecord.request_id == uuid.UUID(request_id)
                    )
                )).scalar_one_or_none()
            if row is not None and row.gateway_first_byte_ms is not None:
                gateway_ms = row.gateway_first_byte_ms
                break
            await asyncio.sleep(0.05)

        assert gateway_ms is not None, (
            "gateway_first_byte_ms never persisted on usage row "
            f"(request_id={request_id})"
        )
        assert gateway_ms >= 0
        # Sanity ceiling: 10 s is way above any conceivable test latency.
        assert gateway_ms < 10_000
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_xautoclaim_recovers_job_after_transient_failure(setup):
    """D-06 at-least-once chaos path: engine crashes on the first
    attempt (TransientFailure → no XACK), then the consumer's periodic
    XAUTOCLAIM re-claims the stale PEL entry, the engine has recovered,
    job completes, client sees status=complete.

    We use a SINGLE worker with a flaky engine (crash once, succeed
    after) instead of two workers because aiosqlite's single-connection
    model makes two concurrent worker sessions race on the same DB
    cursor (real Postgres would be fine; production deployment is
    inherently multi-worker)."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue
    from worker.consumer import WorkerConsumer

    class _FlakyEngine(_StubEngine):
        """Crash on first synthesize_stream call, succeed thereafter."""

        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        def synthesize_stream(self, **kw):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("simulated transient GPU OOM")
            yield from super().synthesize_stream(**kw)

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    stop = asyncio.Event()
    # Short xautoclaim_min_idle_ms so we don't wait 30s in the test.
    consumer = WorkerConsumer(
        redis=fake_redis, engine=_FlakyEngine(),
        archive_to_r2=setup["archive"],
        stop_event=stop, block_ms=10,
        xautoclaim_min_idle_ms=100,
        xautoclaim_period_s=0.1,
    )
    worker_task = asyncio.create_task(consumer.run())

    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {setup['bearer']}"
            _enroll_voice(client, voice_id="chaos-voice")

            rid = str(uuid.uuid4())
            r = client.post(
                "/v1/tts/jobs",
                headers={"Idempotency-Key": rid},
                json={"text": "Chaos test.", "voice_id": "chaos-voice"},
            )
            assert r.status_code == 202

            deadline = asyncio.get_event_loop().time() + 5.0
            body = None
            while asyncio.get_event_loop().time() < deadline:
                r = client.get(f"/v1/tts/jobs/{rid}")
                body = r.json()
                if body["status"] == "complete":
                    break
                await asyncio.sleep(0.05)

            assert body is not None
            assert body["status"] == "complete", (
                f"XAUTOCLAIM never recovered: {body}\n"
                f"transient={consumer.transient_failures} "
                f"acked={consumer.acked} claimed={consumer.claimed}"
            )
            # Exactly one transient failure (the first call) and one
            # ACK after the retry; XAUTOCLAIM re-handed the same entry.
            assert consumer.transient_failures == 1
            assert consumer.acked >= 1
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()
        app.dependency_overrides.clear()


async def test_job_body_hash_conflict_returns_409_e2e(setup):
    """End-to-end version of the Stripe-style body_hash guard. Issuing
    the same Idempotency-Key with a different text triggers 409 with a
    structured error envelope (audit fix F1 + #4 in pipeline tests).
    Confirms the contract holds with a real worker running."""
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
            _enroll_voice(client, voice_id="conflict-voice")

            rid = str(uuid.uuid4())
            r1 = client.post(
                "/v1/tts/jobs",
                headers={"Idempotency-Key": rid},
                json={"text": "Original text.", "voice_id": "conflict-voice"},
            )
            assert r1.status_code == 202

            # Same key + DIFFERENT body → 409.
            r2 = client.post(
                "/v1/tts/jobs",
                headers={"Idempotency-Key": rid},
                json={"text": "Tampered text.", "voice_id": "conflict-voice"},
            )
            assert r2.status_code == 409
            detail = r2.json()["detail"]
            assert detail["error"] == "idempotency_conflict"
            assert detail["original_status"] in {
                "processing", "complete", "failed",
            }
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
