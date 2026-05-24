"""ORM model contract tests — round-trip, relationships, uniqueness.

Run on aiosqlite in-memory (fast). Postgres-only CHECK constraints are
stripped by `init_models_for_tests` for this layer — they're enforced
in real deployments via Alembic migrations and covered separately by
`tests/test_migrations.py` (Faz A.2 follow-up, runs testcontainers).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db import AsyncSessionLocal, init_models_for_tests, models


@pytest.fixture(autouse=True)
async def _fresh_db():
    await init_models_for_tests()
    yield


async def _make_tenant(session, slug="tenant-a", **kw) -> models.Tenant:
    t = models.Tenant(slug=slug, display_name=kw.get("display_name", slug.upper()))
    session.add(t)
    await session.flush()
    return t


async def _make_key(session, tenant: models.Tenant, prefix=None) -> models.ApiKey:
    k = models.ApiKey(
        tenant_id=tenant.id,
        prefix=prefix or f"nqai_dev_{uuid.uuid4().hex[:14]}",
        secret_hash="$argon2id$v=19$m=65536,t=3,p=4$abc$def",
        scopes=["tts:read", "tts:write"],
    )
    session.add(k)
    await session.flush()
    return k


async def _make_voice(session, tenant: models.Tenant, voice_id="vox-01") -> models.Voice:
    v = models.Voice(
        tenant_id=tenant.id,
        voice_id=voice_id,
        display_name=f"voice {voice_id}",
        reference_uri=f"s3://r2/voices/{tenant.slug}/{voice_id}.wav",
        reference_sha256="a" * 64,
        reference_seconds=15.0,
        source="placeholder",
        license="internal-placeholder",
    )
    session.add(v)
    await session.flush()
    return v


async def test_tenant_round_trip():
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, slug="neeko-prod", display_name="NEEKO prod")
        await s.commit()
        fetched = (
            await s.execute(select(models.Tenant).where(models.Tenant.slug == "neeko-prod"))
        ).scalar_one()
        assert fetched.id == t.id
        assert fetched.status == "active"
        assert fetched.metadata_ == {}
        assert fetched.created_at is not None
        assert fetched.updated_at is not None


async def test_tenant_slug_unique():
    async with AsyncSessionLocal() as s:
        await _make_tenant(s, slug="dup")
        s.add(models.Tenant(slug="dup", display_name="dup2"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_api_key_belongs_to_tenant_via_relationship():
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, slug="rel-test")
        await _make_key(s, t)
        await _make_key(s, t)
        await s.commit()
        # Reload tenant and walk relationship
        t2 = (await s.execute(
            select(models.Tenant).where(models.Tenant.id == t.id)
        )).scalar_one()
        await s.refresh(t2, attribute_names=["api_keys"])
        assert len(t2.api_keys) == 2
        for k in t2.api_keys:
            assert k.is_active
            assert "tts:write" in k.scopes


async def test_api_key_cascade_delete_with_tenant():
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, slug="casc")
        await _make_key(s, t)
        await s.commit()
        await s.delete(t)
        await s.commit()
        n = (await s.execute(select(models.ApiKey))).scalars().all()
        assert n == []


async def test_voice_unique_per_tenant_pair():
    """Same voice_id allowed across tenants, blocked within tenant."""
    async with AsyncSessionLocal() as s:
        t_a = await _make_tenant(s, slug="ten-a")
        t_b = await _make_tenant(s, slug="ten-b")
        await _make_voice(s, t_a, voice_id="shared")
        await _make_voice(s, t_b, voice_id="shared")  # OK — different tenants
        await s.commit()
        # Within tenant: violation
        s.add(models.Voice(
            tenant_id=t_a.id, voice_id="shared",
            display_name="dup", reference_uri="x", reference_sha256="a"*64,
            reference_seconds=10.0, source="placeholder",
            license="internal-placeholder",
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_voice_defaults():
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, slug="defaults")
        v = await _make_voice(s, t)
        await s.commit()
        await s.refresh(v)
        assert v.language == "tr"
        assert v.gender == "neutral"
        assert v.reference_sample_rate == 16000
        assert v.release_status == "draft"
        assert v.engine_params == {}
        assert v.style_tags == []
        # Faz 3 alanları NULL ile başlar
        assert v.adapter_uri is None
        assert v.eval_metrics is None


async def test_usage_record_request_id_unique():
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, slug="usage")
        k = await _make_key(s, t)
        rid = uuid.uuid4()
        s.add(models.UsageRecord(
            tenant_id=t.id, api_key_id=k.id, voice_id="v",
            request_id=rid, text_char_count=10, sentence_count=1,
            duration_ms=1000, elapsed_ms=500, status="ok",
        ))
        await s.commit()
        s.add(models.UsageRecord(
            tenant_id=t.id, api_key_id=k.id, voice_id="v",
            request_id=rid, text_char_count=10, sentence_count=1,
            duration_ms=1000, elapsed_ms=500, status="ok",
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_audit_log_insert_only():
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, slug="audit")
        s.add(models.AuditLog(
            actor_type="system", action="bootstrap.complete",
            result="success", tenant_id=t.id,
        ))
        s.add(models.AuditLog(
            actor_type="api_key", actor_label="nqai_dev_aaa", action="auth.fail",
            result="denied", tenant_id=t.id, payload={"reason": "invalid_secret"},
        ))
        await s.commit()
        rows = (await s.execute(select(models.AuditLog))).scalars().all()
        assert len(rows) == 2
        actions = sorted(r.action for r in rows)
        assert actions == ["auth.fail", "bootstrap.complete"]


async def test_job_idempotency_round_trip():
    from datetime import datetime, timedelta, timezone
    async with AsyncSessionLocal() as s:
        t = await _make_tenant(s, slug="idem")
        k = await _make_key(s, t)
        rid = uuid.uuid4()
        s.add(models.JobIdempotency(
            request_id=rid, tenant_id=t.id, api_key_id=k.id,
            request_hash="b" * 64, status="processing",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
        await s.commit()
        row = (await s.execute(
            select(models.JobIdempotency).where(models.JobIdempotency.request_id == rid)
        )).scalar_one()
        assert row.status == "processing"
