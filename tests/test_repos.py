"""Repository contract tests. Crucial: cross-tenant isolation (D-08)."""

from __future__ import annotations

import uuid

import pytest

from db import AsyncSessionLocal, init_models_for_tests
from repos import (
    ApiKeyRepo,
    AuditRepo,
    IdempotencyRepo,
    OperatorRepo,
    TenantRepo,
    UsageRepo,
    VoiceRepo,
)


@pytest.fixture(autouse=True)
async def _fresh_db():
    await init_models_for_tests()
    yield


async def _bootstrap_two_tenants():
    """Helper: returns (session-context-manager-factory, tenant_a, tenant_b)."""
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        a = await tr.create(slug="tenant-a", display_name="Tenant A")
        b = await tr.create(slug="tenant-b", display_name="Tenant B")
        await s.commit()
        return a.id, b.id


# --------------------------------------------------------------------------- #
# TenantRepo
# --------------------------------------------------------------------------- #
async def test_tenant_create_get_by_slug():
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        t = await tr.create(slug="ten-x", display_name="Ten X")
        await s.commit()
        got = await tr.get_by_slug("ten-x")
        assert got is not None and got.id == t.id


async def test_tenant_suspend_reactivate():
    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        t = await tr.create(slug="sus", display_name="Sus")
        await s.commit()
        suspended = await tr.suspend(t.id)
        await s.commit()
        assert suspended is not None and suspended.status == "suspended"
        reactivated = await tr.reactivate(t.id)
        await s.commit()
        assert reactivated.status == "active"


async def test_tenant_list_active_excludes_deleted():
    from datetime import datetime, timezone

    async with AsyncSessionLocal() as s:
        tr = TenantRepo(s)
        await tr.create(slug="alive", display_name="A")
        deleted = await tr.create(slug="dead", display_name="D")
        deleted.deleted_at = datetime.now(timezone.utc)
        await s.commit()
        rows = await tr.list_active()
        slugs = {t.slug for t in rows}
        assert "alive" in slugs
        assert "dead" not in slugs


# --------------------------------------------------------------------------- #
# ApiKeyRepo
# --------------------------------------------------------------------------- #
async def test_apikey_lookup_by_prefix_returns_key_and_tenant():
    tid_a, _ = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        kr = ApiKeyRepo(s)
        await kr.create(
            tenant_id=tid_a,
            prefix="nqai_dev_aaaaaaaaaaaaaa",
            secret_hash="$argon2id$mock",
        )
        await s.commit()
        found = await kr.lookup_active_by_prefix("nqai_dev_aaaaaaaaaaaaaa")
        assert found is not None
        key, tenant = found
        assert tenant.id == tid_a


async def test_apikey_revoked_excluded_from_active_lookup():
    tid, _ = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        kr = ApiKeyRepo(s)
        k = await kr.create(
            tenant_id=tid,
            prefix="nqai_dev_bbbbbbbbbbbbbb",
            secret_hash="$argon2id$mock",
        )
        await s.commit()
        await kr.revoke(k.id, reason="test")
        await s.commit()
        assert await kr.lookup_active_by_prefix("nqai_dev_bbbbbbbbbbbbbb") is None


async def test_apikey_list_for_tenant_excludes_revoked_by_default():
    tid, _ = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        kr = ApiKeyRepo(s)
        await kr.create(tenant_id=tid, prefix="nqai_dev_cccccccccccccc",
                        secret_hash="$argon2id$mock")
        revoked = await kr.create(tenant_id=tid, prefix="nqai_dev_dddddddddddddd",
                                  secret_hash="$argon2id$mock")
        await s.commit()
        await kr.revoke(revoked.id)
        await s.commit()
        active = await kr.list_for_tenant(tid)
        assert len(active) == 1
        full = await kr.list_for_tenant(tid, include_revoked=True)
        assert len(full) == 2


# --------------------------------------------------------------------------- #
# VoiceRepo — D-08 isolation
# --------------------------------------------------------------------------- #
async def test_voice_repo_isolates_tenants():
    tid_a, tid_b = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        vra = VoiceRepo(s, tid_a)
        vrb = VoiceRepo(s, tid_b)
        await vra.create(
            voice_id="shared", display_name="A's shared",
            reference_uri="s3://r2/a/shared.wav",
            reference_sha256="a" * 64, reference_seconds=10.0,
            source="placeholder", license="internal-placeholder",
        )
        await vrb.create(
            voice_id="shared", display_name="B's shared",
            reference_uri="s3://r2/b/shared.wav",
            reference_sha256="b" * 64, reference_seconds=10.0,
            source="placeholder", license="internal-placeholder",
        )
        await s.commit()
        a_listing = await vra.list()
        b_listing = await vrb.list()
        assert len(a_listing) == 1 and a_listing[0].display_name == "A's shared"
        assert len(b_listing) == 1 and b_listing[0].display_name == "B's shared"


async def test_voice_repo_get_returns_none_for_other_tenants_voice():
    tid_a, tid_b = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        vrb = VoiceRepo(s, tid_b)
        await vrb.create(
            voice_id="b-only", display_name="B's voice",
            reference_uri="s3://r2/b/v.wav", reference_sha256="c" * 64,
            reference_seconds=10.0, source="placeholder",
            license="internal-placeholder",
        )
        await s.commit()
        vra = VoiceRepo(s, tid_a)
        assert await vra.get("b-only") is None  # existence leak yok


async def test_voice_repo_rejects_non_uuid_tenant_id():
    async with AsyncSessionLocal() as s:
        with pytest.raises(TypeError):
            VoiceRepo(s, "not-a-uuid")


async def test_voice_repo_soft_delete():
    tid, _ = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        vr = VoiceRepo(s, tid)
        await vr.create(
            voice_id="to-delete", display_name="X",
            reference_uri="s3://r2/x.wav", reference_sha256="d" * 64,
            reference_seconds=10.0, source="placeholder",
            license="internal-placeholder",
        )
        await s.commit()
        deleted = await vr.soft_delete("to-delete")
        await s.commit()
        assert deleted is not None and deleted.deleted_at is not None
        assert await vr.get("to-delete") is None
        full = await vr.list(include_deleted=True)
        assert len(full) == 1


# --------------------------------------------------------------------------- #
# UsageRepo — record + summary
# --------------------------------------------------------------------------- #
async def test_usage_record_and_summary():
    tid, _ = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        kr = ApiKeyRepo(s)
        k = await kr.create(tenant_id=tid, prefix="nqai_dev_eeeeeeeeeeeeee",
                            secret_hash="x")
        await s.commit()
        ur = UsageRepo(s, tid)
        await ur.record(api_key_id=k.id, voice_id="v", request_id=uuid.uuid4(),
                        text_char_count=20, sentence_count=2,
                        duration_ms=3000, elapsed_ms=1500, rtf=0.5)
        await ur.record(api_key_id=k.id, voice_id="v", request_id=uuid.uuid4(),
                        text_char_count=10, sentence_count=1,
                        duration_ms=1000, elapsed_ms=400, rtf=0.4,
                        status="error", error_code="inference_error")
        await s.commit()
        summary = await ur.summary_last_n_days(days=30)
        assert summary["ok"]["count"] == 1
        assert summary["error"]["count"] == 1
        assert summary["ok"]["chars"] == 20


# --------------------------------------------------------------------------- #
# AuditRepo
# --------------------------------------------------------------------------- #
async def test_audit_record_and_filter_by_tenant():
    tid_a, tid_b = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        ar = AuditRepo(s)
        await ar.record(actor_type="system", action="auth.success",
                        result="success", tenant_id=tid_a)
        await ar.record(actor_type="system", action="auth.fail",
                        result="denied", tenant_id=tid_b)
        await s.commit()
        rows_a = await ar.for_tenant(tid_a)
        rows_b = await ar.for_tenant(tid_b)
        assert len(rows_a) == 1 and rows_a[0].action == "auth.success"
        assert len(rows_b) == 1 and rows_b[0].action == "auth.fail"


# --------------------------------------------------------------------------- #
# OperatorRepo
# --------------------------------------------------------------------------- #
async def test_operator_email_normalized_to_lowercase():
    async with AsyncSessionLocal() as s:
        opr = OperatorRepo(s)
        op = await opr.create(email="Erdal@NQAI.com", password_hash="x")
        await s.commit()
        assert op.email == "erdal@nqai.com"
        # case-insensitive lookup
        assert (await opr.get_by_email("ERDAL@nqai.com")).id == op.id


# --------------------------------------------------------------------------- #
# IdempotencyRepo — D-05
# --------------------------------------------------------------------------- #
async def test_idempotency_reserve_and_complete():
    tid, _ = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        kr = ApiKeyRepo(s)
        k = await kr.create(tenant_id=tid, prefix="nqai_dev_ffffffffffffff",
                            secret_hash="x")
        await s.commit()
        idr = IdempotencyRepo(s, tid)
        rid = uuid.uuid4()
        await idr.reserve(request_id=rid, api_key_id=k.id, request_hash="h")
        await s.commit()
        row = await idr.get(rid)
        assert row is not None and row.status == "processing"
        await idr.complete(rid, response_uri="s3://r2/snapshots/foo.wav")
        await s.commit()
        row2 = await idr.get(rid)
        assert row2.status == "complete"
        assert row2.response_uri.startswith("s3://")


async def test_idempotency_expired_not_returned():
    tid, _ = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        from datetime import timedelta

        kr = ApiKeyRepo(s)
        k = await kr.create(tenant_id=tid, prefix="nqai_dev_gggggggggggggg",
                            secret_hash="x")
        await s.commit()
        idr = IdempotencyRepo(s, tid)
        rid = uuid.uuid4()
        await idr.reserve(request_id=rid, api_key_id=k.id, request_hash="h",
                          ttl=timedelta(seconds=-1))  # already expired
        await s.commit()
        assert await idr.get(rid) is None
        purged = await idr.purge_expired()
        await s.commit()
        assert purged == 1


async def test_idempotency_cross_tenant_isolation():
    tid_a, tid_b = await _bootstrap_two_tenants()
    async with AsyncSessionLocal() as s:
        kr = ApiKeyRepo(s)
        ka = await kr.create(tenant_id=tid_a, prefix="nqai_dev_hhhhhhhhhhhhhh",
                             secret_hash="x")
        await s.commit()
        idr_a = IdempotencyRepo(s, tid_a)
        idr_b = IdempotencyRepo(s, tid_b)
        rid = uuid.uuid4()
        await idr_a.reserve(request_id=rid, api_key_id=ka.id, request_hash="h")
        await s.commit()
        # Tenant B trying to see Tenant A's idempotency record
        assert await idr_b.get(rid) is None
        # Tenant A sees its own
        assert await idr_a.get(rid) is not None
