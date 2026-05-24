"""End-to-end API smoke — DB-backed auth + tenant-scoped voices.

Re-written for Faz A.6 cutover: TTS endpoints now go through the same
multi-tenant auth pipeline as /admin. Tests provision a tenant + API
key in the DB, then exercise the surface with that Bearer token.

Voxcpm + voxcpm.model.voxcpm stubbed at module import so neither torch
nor the real 4 GB model load.
"""

from __future__ import annotations

import io
import sys
import types as _types

import fakeredis.aioredis
import numpy as np
import pytest
import soundfile as sf

# Stub voxcpm before importing the server.
_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm_model = _types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")


class _StubInner:
    sample_rate = 48000


class _StubModel:
    tts_model = _StubInner()

    def generate(
        self,
        text,
        *,
        reference_wav_path,
        cfg_value=2.0,
        inference_timesteps=10,
        normalize=False,
        denoise=False,
        retry_badcase=True,
        **_kwargs,
    ):
        sr = self.tts_model.sample_rate
        duration = 0.5 + 0.05 * len(text)
        return np.zeros(int(duration * sr), dtype=np.float32)


class _StubFactory:
    @staticmethod
    def from_pretrained(model_id, **_kwargs):
        return _StubModel()


class _StubLoRAConfig:
    def __init__(self, **_kwargs):
        self.kwargs = _kwargs


_fake_voxcpm.VoxCPM = _StubFactory
_fake_voxcpm_model_voxcpm.LoRAConfig = _StubLoRAConfig
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
    """Provision a tenant + API key in a per-test SQLite DB.

    Returns ``(full_key, tenant_id, ref_dir)``. The TestClient fixture
    consumes ``setup`` to ensure env + DB are ready before importing
    server.main.
    """
    db_file = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()

    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")
    monkeypatch.setenv("NQAI_REQUIRE_AUTH", "true")
    monkeypatch.setenv("NQAI_REFERENCE_DIR", str(ref_dir))
    monkeypatch.setenv("NQAI_COOKIE_SECURE", "false")

    # Force-reset module state so the new env + DB take effect.
    for mod_name in list(sys.modules):
        if mod_name.startswith(("server", "worker", "db", "repos", "frontend", "registry", "storage")):
            del sys.modules[mod_name]

    from db import AsyncSessionLocal, init_models_for_tests
    from repos import ApiKeyRepo, TenantRepo
    from server.security import generate_api_key

    await init_models_for_tests(db_url)

    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        tenant = await tr.create(slug="smoke-tenant", display_name="Smoke")
        kr = ApiKeyRepo(s)
        await kr.create(
            tenant_id=tenant.id,
            prefix=prefix,
            secret_hash=secret_hash,
            scopes=[
                "tts:read",
                "tts:write",
                "voice:read",
                "voice:write",
                "admin:read",
            ],
            rate_limit_per_minute=1000,  # tests issue lots of requests
        )
        await s.commit()
        tenant_id = tenant.id

    return full_key, tenant_id, ref_dir


@pytest.fixture
def client(setup):
    full_key, _tid, _ref = setup

    # Inject fakeredis as the rate-limit + cache backend.
    from server.auth import get_redis

    fake_redis = fakeredis.aioredis.FakeRedis()

    from fastapi.testclient import TestClient

    from server.main import app

    app.dependency_overrides[get_redis] = lambda: fake_redis
    try:
        with TestClient(app) as c:
            c.headers.update({"Authorization": f"Bearer {full_key}"})
            yield c
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# /health is unauthenticated
# --------------------------------------------------------------------------- #
def test_health_does_not_require_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # Gateway is always "loaded" after Faz B.1 (engine lives in workers).
    assert body["loaded"] is True
    assert body["device"] == "gateway"
    # Version stays declared in src/server/main.py — exact match is too
    # brittle, just check semver shape.
    assert body["version"].count(".") == 2


# --------------------------------------------------------------------------- #
# Auth gates
# --------------------------------------------------------------------------- #
def test_voices_requires_bearer(client):
    client.headers.pop("Authorization", None)
    r = client.get("/v1/voices")
    assert r.status_code == 401


def test_voices_rejects_bad_key(client):
    r = client.get("/v1/voices", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Voice CRUD on the DB-backed catalog
# --------------------------------------------------------------------------- #
def test_voices_listing_starts_empty(client):
    r = client.get("/v1/voices")
    assert r.status_code == 200, r.text
    assert r.json() == {"voices": [], "count": 0}


def test_enroll_then_list_then_delete(client):
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={
            "voice_id": "alice-warm-01",
            "display_name": "Alice (warm)",
            "language": "tr",
            "gender": "female",
            "style_tags": "warm,clear",
        },
        files={"reference_audio": ("alice.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voice"]["voice_id"] == "alice-warm-01"
    assert body["voice"]["style_tags"] == ["warm", "clear"]
    assert body["voice"]["language"] == "tr"

    listing = client.get("/v1/voices").json()
    assert listing["count"] == 1

    r2 = client.delete("/v1/voices/alice-warm-01")
    assert r2.status_code == 200, r2.text
    assert client.get("/v1/voices").json()["count"] == 0


def test_enroll_rejects_bad_voice_id(client):
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={"voice_id": "Bad Id!", "display_name": "x"},
        files={"reference_audio": ("x.wav", wav, "audio/wav")},
    )
    assert r.status_code == 400, r.text


def test_enroll_rejects_duplicate(client):
    fields = {"voice_id": "dup-01", "display_name": "Dup"}

    def _files():
        return {"reference_audio": ("d.wav", _make_wav_bytes(), "audio/wav")}

    r1 = client.post("/v1/voices", data=fields, files=_files())
    assert r1.status_code == 200, r1.text
    r2 = client.post("/v1/voices", data=fields, files=_files())
    assert r2.status_code == 409, r2.text


def test_enroll_rejects_tiny_audio(client):
    r = client.post(
        "/v1/voices",
        data={"voice_id": "tiny-01", "display_name": "Tiny"},
        files={"reference_audio": ("t.wav", b"\x00\x01", "audio/wav")},
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# TTS — voice resolution (sync-engine smoke tests moved to test_async_e2e
# now that /v1/tts proxies through the queue; only static-failure
# assertions stay here because they don't need a worker)
# --------------------------------------------------------------------------- #
def test_tts_voice_404(client):
    r = client.post(
        "/v1/tts",
        json={"text": "merhaba", "voice_id": "nobody-here"},
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Cross-tenant isolation — second tenant cannot see first tenant's voices
# --------------------------------------------------------------------------- #
@pytest.fixture
async def second_tenant_key(setup):
    """Spin up a second tenant with its own key on the same DB."""
    from db import AsyncSessionLocal
    from repos import ApiKeyRepo, TenantRepo
    from server.security import generate_api_key

    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        tenant = await tr.create(slug="other-tenant", display_name="Other")
        kr = ApiKeyRepo(s)
        await kr.create(
            tenant_id=tenant.id,
            prefix=prefix,
            secret_hash=secret_hash,
            scopes=["tts:read", "tts:write", "voice:read", "voice:write"],
            rate_limit_per_minute=1000,
        )
        await s.commit()
    return full_key


def test_tenant_isolation_voice_invisible_to_other_tenant(client, second_tenant_key):
    # Tenant 1 enrolls
    wav = _make_wav_bytes()
    client.post(
        "/v1/voices",
        data={"voice_id": "private-01", "display_name": "Private"},
        files={"reference_audio": ("p.wav", wav, "audio/wav")},
    )
    # Tenant 2 with different key cannot see it
    r = client.get(
        "/v1/voices/private-01",
        headers={"Authorization": f"Bearer {second_tenant_key}"},
    )
    assert r.status_code == 404  # existence-leak prevention
    listing = client.get(
        "/v1/voices",
        headers={"Authorization": f"Bearer {second_tenant_key}"},
    ).json()
    assert listing["count"] == 0
