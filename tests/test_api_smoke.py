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


def test_v1_models_lists_registry(client):
    """Faz B.5 Dalga 1.2 — `GET /v1/models` returns the preset
    registry. Public (no bearer required), mirroring ElevenLabs."""
    client.headers.pop("Authorization", None)
    r = client.get("/v1/models")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 3, body
    ids = {m["model_id"] for m in body["models"]}
    assert "nqai-voxcpm2-tr-turbo" in ids
    assert "nqai-voxcpm2-tr-hd" in ids
    assert "nqai-voxcpm2-tr-character" in ids
    # Default is exactly one — `default_model_id` must point to a
    # registered model.
    assert body["default_model_id"] in ids
    # Each entry exposes the inference knobs so a sophisticated client
    # can show "X steps, cfg=Y" to power users.
    for m in body["models"]:
        assert m["cfg_value"] > 0
        assert m["inference_timesteps"] > 0
        assert m["display_name"]
        assert m["description"]


def test_v1_models_does_not_require_auth(client):
    """Match the vendor mental model: the model catalog is public
    metadata, not a tenant-scoped resource."""
    client.headers.pop("Authorization", None)
    assert client.get("/v1/models").status_code == 200


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
    body = r.json()
    assert body["voices"] == []
    assert body["count"] == 0
    # Faz B.5 Dalga 2.4 — pagination meta on the response.
    assert body["total"] == 0
    assert body["limit"] == 100
    assert body["offset"] == 0


def test_voices_listing_pagination_bounds(client):
    """Faz B.5 Dalga 2.4 — limit must be [1, 200], offset >= 0."""
    assert client.get("/v1/voices?limit=0").status_code == 400
    assert client.get("/v1/voices?limit=201").status_code == 400
    assert client.get("/v1/voices?offset=-1").status_code == 400


def test_voice_patch_updates_metadata(client):
    """Faz B.5 Dalga 2.4 — PATCH /v1/voices/{id} updates owner-supplied
    fields without re-enrolling. Reference audio + voice_id stay
    immutable; this is for description / labels / preview_url /
    voice_settings_defaults."""
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={"voice_id": "patch-01", "display_name": "Patch"},
        files={"reference_audio": ("p.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text

    r = client.patch(
        "/v1/voices/patch-01",
        json={
            "display_name": "Patched Voice",
            "description": "Calm Turkish narrator for bedtime stories.",
            "labels": ["calm", "bedtime", "narrator"],
            "preview_url": "https://example.com/preview/patch-01.mp3",
            "voice_settings_defaults": {
                "stability": 0.7,
                "similarity_boost": 0.8,
                "speed": 0.95,
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Patched Voice"
    assert body["description"].startswith("Calm Turkish")
    assert body["labels"] == ["calm", "bedtime", "narrator"]
    assert body["preview_url"].endswith("/preview/patch-01.mp3")
    assert body["voice_settings_defaults"]["stability"] == 0.7
    assert body["voice_settings_defaults"]["speed"] == 0.95

    # GET reflects the same fields.
    g = client.get("/v1/voices/patch-01").json()
    assert g["display_name"] == "Patched Voice"
    assert g["voice_settings_defaults"]["similarity_boost"] == 0.8


def test_voice_patch_rejects_empty_body(client):
    """Empty PATCH is meaningless — 400 with a hint, not a no-op
    success that confuses clients."""
    wav = _make_wav_bytes()
    client.post(
        "/v1/voices",
        data={"voice_id": "empty-patch-01", "display_name": "EP"},
        files={"reference_audio": ("e.wav", wav, "audio/wav")},
    )
    r = client.patch("/v1/voices/empty-patch-01", json={})
    assert r.status_code == 400, r.text
    assert "empty" in r.text.lower()


def test_voice_patch_404_when_not_owned(client):
    """Same existence-leak rule as DELETE: a voice that isn't owned
    by this tenant returns 404, never 403, never the actual row.
    (Single-tenant fixture; we just verify 404 for a missing slug.)"""
    r = client.patch(
        "/v1/voices/does-not-exist-9999",
        json={"display_name": "X"},
    )
    assert r.status_code == 404, r.text


def test_voice_patch_rejects_invalid_speed(client):
    """voice_settings_defaults uses the same VoiceSettings schema as
    per-request voice_settings — out-of-range speed must 422 at the
    pydantic boundary, not silently accept and break inference."""
    wav = _make_wav_bytes()
    client.post(
        "/v1/voices",
        data={"voice_id": "speed-01", "display_name": "S"},
        files={"reference_audio": ("s.wav", wav, "audio/wav")},
    )
    r = client.patch(
        "/v1/voices/speed-01",
        json={"voice_settings_defaults": {"speed": 5.0}},
    )
    # Pydantic validation runs at the body layer → 422.
    assert r.status_code == 422, r.text


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
# Faz B.5 Dalga 2.5 — first-class voice clone API
# --------------------------------------------------------------------------- #
def test_enroll_persists_clone_metadata(client):
    """Dalga 2.5: description / labels / visibility / consent flow
    through POST /v1/voices and surface back on the list response."""
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={
            "voice_id": "clone-meta-01",
            "display_name": "Clone Meta",
            "description": "Warm Turkish narrator, café ambience",
            "labels": "warm,narrator,turkish",
            "visibility": "shared",
            "voice_talent_consent": "true",
        },
        files={"reference_audio": ("clone.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voice"]["voice_id"] == "clone-meta-01"
    assert body["voice"]["description"] == "Warm Turkish narrator, café ambience"
    assert body["voice"]["labels"] == ["warm", "narrator", "turkish"]
    assert body["voice"]["visibility"] == "shared"
    # voice_talent_consent=true → requires_verification flips off
    assert body["requires_verification"] is False


def test_enroll_requires_verification_when_consent_missing(client):
    """Dalga 2.5: omitting voice_talent_consent sets requires_verification=true
    (ElevenLabs IVC parity: caller must acknowledge talent rider)."""
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={"voice_id": "needs-consent", "display_name": "Needs"},
        files={"reference_audio": ("n.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["requires_verification"] is True


def test_enroll_rejects_invalid_visibility(client):
    """Dalga 2.5: visibility outside private/shared/public → 400."""
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        data={
            "voice_id": "bad-vis",
            "display_name": "Bad Vis",
            "visibility": "internal",  # invalid
        },
        files={"reference_audio": ("b.wav", wav, "audio/wav")},
    )
    assert r.status_code == 400, r.text


def test_voices_add_alias_works_like_enroll(client):
    """Dalga 2.5: POST /v1/voices/add (ElevenLabs shape, `name` + `files`)
    enrolls a voice; `voice_id` is server-derived when omitted."""
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices/add",
        data={
            "name": "Ayşe Soft",
            "description": "Soft, contemplative",
            "voice_talent_consent": "true",
        },
        files={"files": ("ayse.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voice"]["display_name"] == "Ayşe Soft"
    # Derived slug should pass voice_id rules
    assert len(body["voice"]["voice_id"]) >= 3
    assert body["requires_verification"] is False


def test_voices_add_alias_accepts_explicit_voice_id(client):
    """Dalga 2.5: alias still honors an explicit voice_id when the
    caller provides one (MiniMax-style)."""
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices/add",
        data={
            "name": "Mert",
            "voice_id": "mert-fixed-01",
            "voice_talent_consent": "true",
        },
        files={"files": ("m.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["voice"]["voice_id"] == "mert-fixed-01"


def test_enroll_rejects_audio_below_min_seconds(client, monkeypatch):
    """Dalga 2.5: enroll_min_seconds enforced — bumping the floor to
    5s and posting 2s audio yields 400. Settings is frozen, so we
    swap the live `settings` reference for a rebuilt instance."""
    monkeypatch.setenv("NQAI_ENROLL_MIN_SECONDS", "5.0")
    from server import config as cfg_mod
    from server import main as main_mod
    new_settings = cfg_mod.Settings()
    monkeypatch.setattr(cfg_mod, "settings", new_settings)
    monkeypatch.setattr(main_mod, "settings", new_settings)

    wav = _make_wav_bytes(duration_s=2.0)
    r = client.post(
        "/v1/voices",
        data={"voice_id": "tooshort", "display_name": "Short"},
        files={"reference_audio": ("s.wav", wav, "audio/wav")},
    )
    assert r.status_code == 400, r.text
    assert "too short" in r.json()["detail"].lower()


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
