"""Faz B.5 Dalga 3.1 — WebSocket input-streaming TTS protocol tests.

The endpoint accepts partial text over a long-lived WebSocket and
forwards base64-encoded PCM frames back as the worker produces them.
We exercise the protocol surface against the real FastAPI app + a
fakeredis-backed queue + a real worker consumer driven by a stub
engine. The contract being verified is the WIRE shape — handshake,
buffer/flush semantics, error frames — not byte-exact audio.

If this regresses, SDKs that target the ElevenLabs ``/stream-input``
shape will break against NQAI when they swap base URLs.
"""

from __future__ import annotations

import asyncio
import base64
import io
import sys
import types as _types
from dataclasses import dataclass

import fakeredis.aioredis
import numpy as np
import pytest
import soundfile as sf

# Stub voxcpm before server.* / worker.* imports.
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

    def synthesize_stream(
        self, *, text, voice, reference_path, language_id="tr",
        engine_overrides=None, request_meta=None,
    ):
        # Emit ONE chunk per call so we can count segments precisely
        # by counting (audio, sentence_end) frame pairs on the client.
        yield _FakeChunk(
            pcm_int16=b"\x00\x00" * 1024,
            sample_rate=self.sample_rate,
            sentence_index=0,
            sentence_text=text.strip(),
        )


def _make_wav_bytes(duration_s: float = 1.0, sr: int = 16000) -> bytes:
    audio = (np.random.randn(int(duration_s * sr)) * 0.1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
async def ws_setup(tmp_path, monkeypatch):
    """Boot gateway + tenant + key + voice + worker on fakeredis. Returns
    an httpx-friendly bearer plus everything the WS test needs."""
    db_file = tmp_path / "ws.db"
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
        tenant = await TenantRepo(s).create(slug="ws-tenant", display_name="WS")
        await ApiKeyRepo(s).create(
            tenant_id=tenant.id, prefix=prefix, secret_hash=secret_hash,
            scopes=["tts:read", "tts:write", "voice:read", "voice:write"],
            rate_limit_per_minute=1000,
        )
        await s.commit()

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    async def archive_local(rid, pcm, sr):
        from audio.wav import pcm16_to_wav_bytes
        path = artifact_dir / f"{rid}.wav"
        path.write_bytes(pcm16_to_wav_bytes(pcm, sample_rate=sr))
        return f"file://{path}"

    return {
        "bearer": full_key,
        "ref_dir": ref_dir,
        "archive": archive_local,
    }


def _enroll_via_http(client, voice_id: str = "ws-voice") -> None:
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={"voice_id": voice_id, "display_name": "WS"},
        files={"reference_audio": ("d.wav", wav, "audio/wav")},
        headers={"Authorization": f"Bearer {client.bearer}"},
    )
    assert r.status_code == 200, r.text


async def _run_with_worker(setup, fake_redis, queue, body):
    """Spin a WorkerConsumer for the duration of `body` (a coroutine)."""
    from worker.consumer import WorkerConsumer
    stop = asyncio.Event()
    consumer = WorkerConsumer(
        redis=fake_redis, engine=_StubEngine(),
        archive_to_r2=setup["archive"],
        stop_event=stop, block_ms=10,
    )
    worker_task = asyncio.create_task(consumer.run())
    try:
        return await body()
    finally:
        stop.set()
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()


async def test_ws_auth_via_query_param(ws_setup):
    """No Authorization header, api_key in query param → 1008 vs 200
    depending on whether the bearer matches."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    try:
        with TestClient(app) as client:
            client.bearer = ws_setup["bearer"]
            _enroll_via_http(client, "ws-voice")

            # Bad key → close 1008.
            with pytest.raises(Exception), client.websocket_connect(  # noqa: PT011, B017
                "/v1/text-to-speech/ws-voice/stream-input?api_key=not-a-real-key",
            ) as ws:
                ws.send_json({"close": True})

            # Good key → accept handshake.
            with client.websocket_connect(
                f"/v1/text-to-speech/ws-voice/stream-input?api_key={client.bearer}",
            ) as ws:
                ws.send_json({"close": True})
                # Server sends `done` and closes; receive_json drains.
                msg = ws.receive_json()
                assert msg.get("event") == "done"
    finally:
        app.dependency_overrides.clear()


async def test_ws_unknown_voice_closes_with_1008(ws_setup):
    """Voice that doesn't exist → close 1008 'voice not found'; we
    DON'T leak existence with a 4xx (D-08 existence-leak rule)."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    try:
        with TestClient(app) as client:
            client.bearer = ws_setup["bearer"]
            # Note: NO enroll — the voice does not exist.
            with pytest.raises(Exception), client.websocket_connect(  # noqa: PT011, B017
                f"/v1/text-to-speech/nonexistent/stream-input?api_key={client.bearer}",
            ) as ws:
                ws.receive_json()  # connection is being closed
    finally:
        app.dependency_overrides.clear()


async def test_ws_flushes_on_sentence_boundary_and_streams_audio(ws_setup):
    """Two appended sentences → audio frames + sentence_end events.
    PCM payload is base64 + ≥ 1 byte. Worker actually runs end-to-end
    against fakeredis.

    Synchronous TestClient.websocket_connect blocks the event loop —
    we run the whole WS interaction inside ``asyncio.to_thread`` so
    the worker_task on the main loop keeps draining jobs concurrently."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    try:
        with TestClient(app) as client:
            client.bearer = ws_setup["bearer"]
            _enroll_via_http(client, "ws-voice")

            def _ws_dialog() -> list[dict]:
                received: list[dict] = []
                with client.websocket_connect(
                    f"/v1/text-to-speech/ws-voice/stream-input?api_key={client.bearer}",
                ) as ws:
                    ws.send_json({
                        "audio_format": "pcm16",
                        "text": "Bu birinci cümle olduğu için yeterince uzun.",
                    })
                    ws.send_json({
                        "text": " Bu ikinci cümle de ayrıca uzun bir biçimde."
                                " Tamam.",
                    })
                    ws.send_json({"flush": True})
                    ws.send_json({"close": True})
                    while True:
                        m = ws.receive_json()
                        received.append(m)
                        if m.get("event") == "done":
                            break
                return received

            async def _scenario() -> list[dict]:
                return await asyncio.to_thread(_ws_dialog)

            received = await _run_with_worker(
                ws_setup, fake_redis, queue, _scenario,
            )

        audio_frames = [m for m in received if "audio" in m]
        sentence_ends = [
            m for m in received if m.get("event") == "sentence_end"
        ]
        assert len(audio_frames) >= 1
        assert len(sentence_ends) >= 1
        for af in audio_frames:
            decoded = base64.b64decode(af["audio"])
            assert len(decoded) > 0
            assert af["audio_format"] == "pcm16"
            assert af["sample_rate"] == 48000
            assert "alignment" in af
        assert received[-1] == {"event": "done"}
    finally:
        app.dependency_overrides.clear()


async def test_ws_rejects_invalid_json(ws_setup):
    """Garbage payload → error frame, WS stays open so the client can
    correct + continue."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    try:
        with TestClient(app) as client:
            client.bearer = ws_setup["bearer"]
            _enroll_via_http(client, "ws-voice")

            with client.websocket_connect(
                f"/v1/text-to-speech/ws-voice/stream-input?api_key={client.bearer}",
            ) as ws:
                ws.send_text("{not-json")
                err = ws.receive_json()
                assert err["event"] == "error"
                assert err["code"] == "invalid_json"
                # Close cleanly.
                ws.send_json({"close": True})
                # Drain `done`.
                done = ws.receive_json()
                assert done.get("event") == "done"
    finally:
        app.dependency_overrides.clear()


async def test_ws_rejects_unknown_model_id(ws_setup):
    """Bad model_id in config → error frame, stays open. Vendor-parity
    error code 'unknown_model_id'."""
    from fastapi.testclient import TestClient

    from server.auth import get_redis
    from server.main import app
    from server.queue import DEFAULT_STREAM, TtsJobQueue, get_queue

    fake_redis = fakeredis.aioredis.FakeRedis()
    queue = TtsJobQueue(fake_redis, stream=DEFAULT_STREAM)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_queue] = lambda: queue

    try:
        with TestClient(app) as client:
            client.bearer = ws_setup["bearer"]
            _enroll_via_http(client, "ws-voice")

            with client.websocket_connect(
                f"/v1/text-to-speech/ws-voice/stream-input?api_key={client.bearer}",
            ) as ws:
                ws.send_json({"model_id": "not-a-model"})
                err = ws.receive_json()
                assert err["event"] == "error"
                assert err["code"] == "unknown_model_id"
                ws.send_json({"close": True})
                done = ws.receive_json()
                assert done.get("event") == "done"
    finally:
        app.dependency_overrides.clear()
