"""Admin auth + tenant + API key CRUD end-to-end.

Per-test temp SQLite file (shared across connections), fakeredis,
voxcpm stubbed at module import time.
"""

from __future__ import annotations

import sys
import types as _types
import uuid

import pytest

# Stub voxcpm so importing server.main doesn't pull torch+voxcpm.
_fake_voxcpm = _types.ModuleType("voxcpm")
class _StubFactory:
    @staticmethod
    def from_pretrained(model_id, load_denoiser=False):
        class _M:
            class tts_model:
                sample_rate = 48000
            def generate(self, *a, **kw):
                import numpy as np
                return np.zeros(48000, dtype=np.float32)
        return _M()
_fake_voxcpm.VoxCPM = _StubFactory
sys.modules.setdefault("voxcpm", _fake_voxcpm)


@pytest.fixture
async def setup(monkeypatch, tmp_path):
    """Per-test DB + JWT secret + legacy auth env, returns operator UUID."""
    db_file = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")
    monkeypatch.setenv("NQAI_API_KEYS", "legacy-key")
    monkeypatch.setenv("NQAI_REQUIRE_AUTH", "true")
    monkeypatch.setenv("NQAI_COOKIE_SECURE", "false")  # HTTP TestClient

    # Reload server modules so they pick up the env
    for m in list(sys.modules):
        if m.startswith(("server", "worker", "db", "repos", "frontend", "registry", "storage")):
            del sys.modules[m]

    from db import AsyncSessionLocal, init_models_for_tests
    from repos import OperatorRepo
    from server.security.passwords import hash_secret

    await init_models_for_tests(db_url)
    async with AsyncSessionLocal() as s:
        op = await OperatorRepo(s).create(
            email="ops@nqai.com",
            password_hash=hash_secret("strong-password-12345"),
            roles=["admin"],
        )
        await s.commit()
        return op.id


@pytest.fixture
def client(setup):
    from fastapi.testclient import TestClient

    from server.main import app
    with TestClient(app) as c:
        yield c


def _login(client, email="ops@nqai.com", password="strong-password-12345"):
    r = client.post("/admin/auth/login",
                    data={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


def test_admin_login_sets_cookies(client):
    r = _login(client)
    assert "nqai_admin_access" in r.cookies


def test_admin_login_rejects_bad_credentials(client):
    r = client.post("/admin/auth/login",
                    data={"email": "ops@nqai.com", "password": "wrong"})
    assert r.status_code == 401


def test_admin_login_rejects_unknown_email(client):
    r = client.post("/admin/auth/login",
                    data={"email": "ghost@nqai.com", "password": "x"})
    assert r.status_code == 401


def test_admin_tenants_requires_login(client):
    r = client.get("/admin/tenants")
    assert r.status_code == 401


def test_admin_create_list_tenant(client):
    _login(client)
    r = client.post("/admin/tenants",
                    data={"slug": "neeko-prod", "display_name": "NEEKO"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "neeko-prod"

    r2 = client.get("/admin/tenants")
    assert r2.status_code == 200
    slugs = {t["slug"] for t in r2.json()["tenants"]}
    assert "neeko-prod" in slugs


def test_admin_create_tenant_duplicate_slug_conflict(client):
    _login(client)
    client.post("/admin/tenants", data={"slug": "dup", "display_name": "D"})
    r = client.post("/admin/tenants",
                    data={"slug": "dup", "display_name": "D"})
    assert r.status_code == 409


def test_admin_generate_api_key_returns_full_once(client):
    _login(client)
    r = client.post("/admin/tenants",
                    data={"slug": "keys-test", "display_name": "K"})
    tenant_id = r.json()["id"]
    r2 = client.post(f"/admin/tenants/{tenant_id}/keys",
                     data={"label": "production", "environment": "prod"})
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert "full_key" in body
    assert body["full_key"].startswith("nqai_prod_")
    assert "warning" in body
    r3 = client.get(f"/admin/tenants/{tenant_id}")
    assert r3.status_code == 200
    keys = r3.json()["api_keys"]
    assert len(keys) == 1
    assert "full_key" not in keys[0]
    assert keys[0]["prefix"] == body["prefix"]


def test_admin_revoke_api_key(client):
    _login(client)
    tenant_id = client.post("/admin/tenants",
                            data={"slug": "rev", "display_name": "R"}).json()["id"]
    key_resp = client.post(f"/admin/tenants/{tenant_id}/keys",
                           data={"environment": "dev"}).json()
    key_id = key_resp["id"]
    r = client.post(f"/admin/tenants/{tenant_id}/keys/{key_id}/revoke",
                    data={"reason": "rotated"})
    assert r.status_code == 200
    assert r.json()["status"] == "revoked"
    r2 = client.post(f"/admin/tenants/{tenant_id}/keys/{key_id}/revoke")
    assert r2.json()["status"] == "already_revoked"


def test_admin_get_unknown_tenant_404(client):
    _login(client)
    fake = uuid.uuid4()
    r = client.get(f"/admin/tenants/{fake}")
    assert r.status_code == 404


def test_admin_usage_summary_returns_per_tenant(client):
    _login(client)
    client.post("/admin/tenants", data={"slug": "u1", "display_name": "1"})
    client.post("/admin/tenants", data={"slug": "u2", "display_name": "2"})
    r = client.get("/admin/usage")
    assert r.status_code == 200
    assert "u1" in r.json()["tenants"]
    assert "u2" in r.json()["tenants"]


def test_admin_dashboard_html_when_logged_in(client):
    _login(client)
    r = client.get("/admin/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "NQAI Voice" in r.text


def test_admin_dashboard_shows_login_when_anonymous(client):
    r = client.get("/admin/")
    assert r.status_code == 200
    assert "Operator giriş" in r.text
