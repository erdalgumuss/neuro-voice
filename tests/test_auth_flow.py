"""End-to-end auth pipeline tests on FakeRedis + in-memory SQLite.

Covers the full path of src/server/auth.py.authenticate_bearer:
    parse → DB lookup → argon2 verify → tenant status → scope → rate limit
plus the audit log invariants (D-04).
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from fastapi import HTTPException

from db import AsyncSessionLocal, init_models_for_tests, models
from repos import ApiKeyRepo, AuditRepo, TenantRepo
from server.auth import authenticate_bearer
from server.security import generate_api_key


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")


@pytest.fixture
async def db():
    await init_models_for_tests()
    yield


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
async def tenant_with_key(db):
    """Provision a tenant + an API key, returning (tenant, full_key)."""
    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        t = await tr.create(slug="auth-test", display_name="Auth")
        kr = ApiKeyRepo(s)
        await kr.create(
            tenant_id=t.id,
            prefix=prefix,
            secret_hash=secret_hash,
            scopes=["tts:read", "tts:write"],
            rate_limit_per_minute=60,
        )
        await s.commit()
        return t.id, full_key


async def test_auth_success(tenant_with_key, redis):
    _, full_key = tenant_with_key
    async with AsyncSessionLocal() as s:
        ctx = await authenticate_bearer(
            authorization=f"Bearer {full_key}",
            session=s, redis=redis,
            required_scopes=("tts:write",),
        )
        assert ctx.tenant.slug == "auth-test"
        assert ctx.has_scope("tts:write")
        assert ctx.api_key.last_used_at is not None


async def test_auth_missing_bearer(redis):
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization=None, session=s, redis=redis,
            )
        assert exc.value.status_code == 401


async def test_auth_bad_format(redis):
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization="Bearer not-a-key", session=s, redis=redis,
            )
        assert exc.value.status_code == 401


async def test_auth_unknown_prefix(db, redis):
    """Token shape is valid but prefix not in DB."""
    full_key, _, _ = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization=f"Bearer {full_key}",
                session=s, redis=redis,
            )
        assert exc.value.status_code == 401


async def test_auth_secret_mismatch(tenant_with_key, redis):
    """Right prefix, wrong secret."""
    _, full_key = tenant_with_key
    parts = full_key.split("_")
    # corrupt the secret portion
    corrupted = "_".join(parts[:3]) + "_" + "z" * 40
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization=f"Bearer {corrupted}",
                session=s, redis=redis,
            )
        assert exc.value.status_code == 401


async def test_auth_revoked_key(tenant_with_key, redis):
    _, full_key = tenant_with_key
    async with AsyncSessionLocal() as s:
        # Find the key + revoke
        from server.security import parse_api_key
        parsed = parse_api_key(full_key)
        kr = ApiKeyRepo(s)
        found = await kr.lookup_active_by_prefix(parsed.prefix)
        await kr.revoke(found[0].id, reason="test")
        await s.commit()
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization=f"Bearer {full_key}",
                session=s, redis=redis,
            )
        assert exc.value.status_code == 401


async def test_auth_suspended_tenant(tenant_with_key, redis):
    tid, full_key = tenant_with_key
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        await tr.suspend(tid)
        await s.commit()
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization=f"Bearer {full_key}",
                session=s, redis=redis,
            )
        assert exc.value.status_code == 403


async def test_auth_insufficient_scope(tenant_with_key, redis):
    _, full_key = tenant_with_key
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization=f"Bearer {full_key}",
                session=s, redis=redis,
                required_scopes=("admin:read",),  # not on this key
            )
        assert exc.value.status_code == 403
        assert "insufficient_scope" in str(exc.value.headers.get("WWW-Authenticate", ""))


async def test_auth_rate_limit_per_key(db, redis):
    """Use a per_minute=2 key to trigger the 429 fast."""
    full_key, prefix, secret_hash = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        t = await tr.create(slug="rate-test", display_name="R")
        kr = ApiKeyRepo(s)
        await kr.create(
            tenant_id=t.id, prefix=prefix, secret_hash=secret_hash,
            rate_limit_per_minute=2,
        )
        await s.commit()

    # First two calls succeed, third 429s
    for _ in range(2):
        async with AsyncSessionLocal() as s:
            ctx = await authenticate_bearer(
                authorization=f"Bearer {full_key}",
                session=s, redis=redis,
            )
            assert ctx is not None

    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException) as exc:
            await authenticate_bearer(
                authorization=f"Bearer {full_key}",
                session=s, redis=redis,
            )
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers


async def test_auth_writes_success_to_audit_log(tenant_with_key, redis):
    tid, full_key = tenant_with_key
    async with AsyncSessionLocal() as s:
        await authenticate_bearer(
            authorization=f"Bearer {full_key}",
            session=s, redis=redis,
        )
    async with AsyncSessionLocal() as s:
        ar = AuditRepo(s)
        rows = await ar.for_tenant(tid)
        actions = [r.action for r in rows]
        assert "auth.success" in actions


async def test_auth_writes_failure_to_audit_log(db, redis):
    """Failed lookups land in audit_log with no tenant_id."""
    full_key, _, _ = generate_api_key("dev")
    async with AsyncSessionLocal() as s:
        with pytest.raises(HTTPException):
            await authenticate_bearer(
                authorization=f"Bearer {full_key}",
                session=s, redis=redis,
            )
    async with AsyncSessionLocal() as s:
        # No tenant_id since the prefix was unknown
        from sqlalchemy import select
        rows = (await s.execute(
            select(models.AuditLog).where(models.AuditLog.action == "auth.fail")
        )).scalars().all()
        assert len(rows) >= 1
        assert rows[0].result == "denied"
