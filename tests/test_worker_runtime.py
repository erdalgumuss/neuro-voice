"""Unit tests for src/worker/runtime.py — boot factories.

We don't exercise the real engine (no GPU here) — instead we check
that the factory plumbing is correct: archive callable maps PCM →
WAV → R2 upload → s3:// URI, missing R2 env returns None, warmup
skip is honoured, etc.
"""

from __future__ import annotations

import asyncio
import io
import sys
import types as _types
import uuid
import wave

import pytest

# Voxcpm stub — keep imports cheap.
_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm.VoxCPM = type("SF", (), {
    "from_pretrained": staticmethod(lambda *a, **kw: None),
})
_fake_voxcpm_model = _types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")
_fake_voxcpm_model_voxcpm.LoRAConfig = object
sys.modules.setdefault("voxcpm", _fake_voxcpm)
sys.modules.setdefault("voxcpm.model", _fake_voxcpm_model)
sys.modules.setdefault("voxcpm.model.voxcpm", _fake_voxcpm_model_voxcpm)


@pytest.fixture(autouse=True)
def _reset_modules():
    """Drop cached worker.* / storage.* so env tweaks per test land."""
    for m in list(sys.modules):
        if m.startswith(("worker", "storage")):
            del sys.modules[m]
    yield


def test_build_archive_to_r2_returns_none_when_env_missing(monkeypatch):
    monkeypatch.delenv("NQAI_R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("NQAI_R2_BUCKET", raising=False)

    from worker.runtime import build_archive_to_r2
    assert build_archive_to_r2() is None


def test_build_archive_to_r2_wraps_upload_and_returns_uri(monkeypatch, tmp_path):
    """Archive callable must: WAV-encode the PCM, upload to R2, return
    the s3:// URI string the pipeline expects."""
    monkeypatch.setenv("NQAI_R2_ACCOUNT_ID", "test-acc")
    monkeypatch.setenv("NQAI_R2_BUCKET", "test-bucket")
    monkeypatch.setenv("NQAI_R2_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("NQAI_R2_SECRET_ACCESS_KEY", "s")
    monkeypatch.setenv("NQAI_R2_CACHE_DIR", str(tmp_path / "cache"))

    # Inject a fake R2 client capture so we don't touch boto3 / moto.
    captured = {}

    class _FakeS3URI:
        def __init__(self, uri: str) -> None:
            self.uri = uri

    class _FakeStorage:
        default_bucket = "test-bucket"

        def upload_bytes(self, data, key, *, bucket=None, content_type=None):
            captured["data"] = data
            captured["key"] = key
            captured["content_type"] = content_type
            return _FakeS3URI(f"s3://test-bucket/{key}")

    import storage as storage_pkg
    monkeypatch.setattr(storage_pkg, "get_r2_storage", lambda: _FakeStorage())

    from worker.runtime import build_archive_to_r2
    archive = build_archive_to_r2()
    assert archive is not None

    rid = uuid.uuid4()
    pcm = b"\x00\x00" * 256  # 256 samples of silence
    uri = asyncio.run(archive(rid, pcm, 48000))

    # URI format: s3://bucket/tts-outputs/YYYY/MM/DD/<rid>.wav
    assert uri.startswith("s3://test-bucket/tts-outputs/")
    assert uri.endswith(f"{rid}.wav")
    assert captured["content_type"] == "audio/wav"

    # The uploaded bytes are a valid WAV with the correct sample rate.
    with wave.open(io.BytesIO(captured["data"]), "rb") as r:
        assert r.getframerate() == 48000
        assert r.getnchannels() == 1
        assert r.getsampwidth() == 2
        assert r.readframes(r.getnframes()) == pcm


def test_archive_runs_upload_in_thread(monkeypatch):
    """boto3 is sync; archive callable must wrap it in asyncio.to_thread
    so a multi-second R2 PUT doesn't block the worker event loop."""
    monkeypatch.setenv("NQAI_R2_ACCOUNT_ID", "test-acc")
    monkeypatch.setenv("NQAI_R2_BUCKET", "test-bucket")

    invocations: dict[str, int] = {"to_thread": 0}
    real_to_thread = asyncio.to_thread

    async def spy(func, *args, **kwargs):
        invocations["to_thread"] += 1
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)

    class _FakeS3URI:
        def __init__(self, uri):
            self.uri = uri

    class _FakeStorage:
        def upload_bytes(self, data, key, **kw):
            return _FakeS3URI(f"s3://b/{key}")

    import storage as storage_pkg
    monkeypatch.setattr(storage_pkg, "get_r2_storage", lambda: _FakeStorage())

    from worker.runtime import build_archive_to_r2
    archive = build_archive_to_r2()
    asyncio.run(archive(uuid.uuid4(), b"\x00\x00", 48000))
    assert invocations["to_thread"] >= 1


async def test_boot_worker_calls_engine_warmup_when_enabled(monkeypatch):
    class _StubEngine:
        sample_rate = 48000
        warmed = False

        def warmup(self) -> None:
            type(self).warmed = True

    import fakeredis.aioredis
    fake_redis = fakeredis.aioredis.FakeRedis()

    from worker.runtime import boot_worker
    engine, redis, _archive = await boot_worker(
        engine=_StubEngine(), redis=fake_redis,
        archive_to_r2=None, warmup=True,
    )
    assert _StubEngine.warmed is True
    assert engine is not None
    assert redis is fake_redis


async def test_boot_worker_skips_warmup_when_disabled():
    class _StubEngine:
        sample_rate = 48000
        warmed = False

        def warmup(self) -> None:
            type(self).warmed = True

    import fakeredis.aioredis
    fake_redis = fakeredis.aioredis.FakeRedis()

    from worker.runtime import boot_worker
    await boot_worker(
        engine=_StubEngine(), redis=fake_redis,
        archive_to_r2=None, warmup=False,
    )
    assert _StubEngine.warmed is False


async def test_boot_worker_fails_loud_on_unreachable_redis():
    """A bad NQAI_REDIS_URL must crash boot, not hang on first XREAD."""
    class _StubEngine:
        sample_rate = 48000
        def warmup(self) -> None:
            pass

    class _BrokenRedis:
        async def ping(self) -> bool:
            return False

    from worker.runtime import boot_worker
    with pytest.raises(RuntimeError, match="unreachable"):
        await boot_worker(
            engine=_StubEngine(), redis=_BrokenRedis(),
            archive_to_r2=None, warmup=False,
        )
