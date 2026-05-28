"""Admin web + JSON routes.

Auth model:
    POST /admin/auth/login           email + password → set HttpOnly cookies
    POST /admin/auth/logout          clear cookies
    POST /admin/auth/refresh         refresh access token (TODO  family)

    GET  /admin                      dashboard (HTMX-rendered)
    GET  /admin/tenants              list + create form (HTML)
    POST /admin/tenants              create tenant (JSON or form)
    GET  /admin/tenants/{id}         tenant detail (keys, usage)
    POST /admin/tenants/{id}/keys    generate new API key (returns full_key ONCE)
    POST /admin/tenants/{id}/keys/{key_id}/revoke
    GET  /admin/usage                last 30 days summary across all tenants
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    Cookie,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ApiKey, Voice
from db.session import get_session
from repos import (
    ApiKeyRepo,
    AuditRepo,
    DataDeletionRequestRepo,
    OperatorRepo,
    TalentContractRepo,
    TenantRepo,
    UsageRepo,
    VoiceConsentRecordRepo,
    VoiceRepo,
    WatermarkKeyRepo,
)
from sqlalchemy import select
from storage.r2 import get_r2_storage
from server.schemas import (
    EvalPinRequest,
    WatermarkDetectionResultPublic,
    WatermarkKeyAllocateRequest,
    WatermarkKeyPublic,
    WatermarkKeyRetireRequest,
)
from server.security import (
    decode_operator_jwt,
    generate_api_key,
    issue_operator_jwt,
)
from server.security.jwt_tokens import JWTError
from server.security.passwords import SecretMismatchError, verify_secret

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

admin_router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)

ACCESS_COOKIE = "nv_admin_access"
REFRESH_COOKIE = "nv_admin_refresh"
# NEUROVOICE_COOKIE_SECURE=false only in tests / local HTTP dev. Production stays on.
COOKIE_KWARGS = dict(
    httponly=True,
    secure=os.environ.get("NEUROVOICE_COOKIE_SECURE", "true").lower() == "true",
    samesite="strict",
)


# --------------------------------------------------------------------------- #
# Operator auth dependency
# --------------------------------------------------------------------------- #
async def _current_operator(
    nv_admin_access: Annotated[str | None, Cookie()] = None,
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    if not nv_admin_access:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "operator login required")
    try:
        claims = decode_operator_jwt(nv_admin_access, expected_type="access")
    except JWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session invalid") from e
    op = await OperatorRepo(session).get(claims.operator_id)
    if op is None or op.disabled_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "operator disabled")
    return op


# --------------------------------------------------------------------------- #
# Auth routes
# --------------------------------------------------------------------------- #
@admin_router.post("/auth/login")
async def login(
    response: Response,
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    opr = OperatorRepo(session)
    op = await opr.get_by_email(email)
    if op is None or op.disabled_at is not None:
        # Generic message — no information leakage
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    try:
        verify_secret(op.password_hash, password)
    except SecretMismatchError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials") from e

    access, refresh, family = issue_operator_jwt(op.id, op.roles)
    await opr.touch_login(op.id)
    await AuditRepo(session).record(
        actor_type="operator", actor_id=op.id, actor_label=op.email,
        action="operator.login", result="success",
        ip_addr=request.client.host if request.client else None,
    )
    await session.commit()

    response.set_cookie(ACCESS_COOKIE, access, max_age=3600, **COOKIE_KWARGS)
    response.set_cookie(
        REFRESH_COOKIE, refresh, max_age=7 * 24 * 3600,
        path="/admin/auth/refresh", **COOKIE_KWARGS,
    )
    return {"operator_id": str(op.id), "email": op.email, "roles": op.roles}


@admin_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE, path="/admin/auth/refresh")
    return {"status": "logged_out"}


# --------------------------------------------------------------------------- #
# Tenant routes
# --------------------------------------------------------------------------- #
@admin_router.get("/tenants", response_model=None)
async def list_tenants(
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    tenants = await TenantRepo(session).list_active()
    return {"tenants": [
        {
            "id": str(t.id),
            "slug": t.slug,
            "display_name": t.display_name,
            "status": t.status,
            "created_at": t.created_at.isoformat(),
        }
        for t in tenants
    ]}


@admin_router.post("/tenants", status_code=201)
async def create_tenant(
    slug: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    tr = TenantRepo(session)
    if await tr.get_by_slug(slug) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"tenant '{slug}' already exists")
    t = await tr.create(slug=slug, display_name=display_name)
    await AuditRepo(session).record(
        actor_type="operator", actor_id=op.id, actor_label=op.email,
        action="tenant.create", result="success",
        tenant_id=t.id, target_type="tenant", target_id=str(t.id),
        payload={"slug": slug, "display_name": display_name},
    )
    await session.commit()
    return {"id": str(t.id), "slug": t.slug, "display_name": t.display_name}


@admin_router.get("/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: uuid.UUID,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    tr = TenantRepo(session)
    t = await tr.get(tenant_id)
    if t is None or t.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
    kr = ApiKeyRepo(session)
    keys = await kr.list_for_tenant(t.id, include_revoked=True)
    ur = UsageRepo(session, t.id)
    summary = await ur.summary_last_n_days(30)
    return {
        "tenant": {
            "id": str(t.id), "slug": t.slug,
            "display_name": t.display_name, "status": t.status,
            "created_at": t.created_at.isoformat(),
        },
        "api_keys": [
            {
                "id": str(k.id),
                "prefix": k.prefix,
                "scopes": k.scopes,
                "rate_limit_per_minute": k.rate_limit_per_minute,
                "label": k.label,
                "active": k.is_active,
                "created_at": k.created_at.isoformat(),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            }
            for k in keys
        ],
        "usage_30d": summary,
    }


# --------------------------------------------------------------------------- #
# API key routes — key generation returns full_key EXACTLY ONCE
# --------------------------------------------------------------------------- #
@admin_router.post("/tenants/{tenant_id}/keys", status_code=201)
async def create_api_key(
    tenant_id: uuid.UUID,
    request: Request,
    label: Annotated[str | None, Form()] = None,
    rate_limit_per_minute: Annotated[int, Form()] = 60,
    environment: Annotated[str, Form()] = "prod",
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    tr = TenantRepo(session)
    t = await tr.get(tenant_id)
    if t is None or t.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")

    full_key, prefix, secret_hash = generate_api_key(environment)  # type: ignore[arg-type]
    key = await ApiKeyRepo(session).create(
        tenant_id=t.id,
        prefix=prefix,
        secret_hash=secret_hash,
        rate_limit_per_minute=rate_limit_per_minute,
        label=label,
        created_by_operator_id=op.id,
    )
    await AuditRepo(session).record(
        actor_type="operator", actor_id=op.id, actor_label=op.email,
        action="key.create", result="success",
        tenant_id=t.id, target_type="api_key", target_id=str(key.id),
        ip_addr=request.client.host if request.client else None,
        payload={"prefix": prefix, "rate_limit_per_minute": rate_limit_per_minute,
                 "label": label, "environment": environment},
    )
    await session.commit()
    return {
        "id": str(key.id),
        "prefix": prefix,
        "full_key": full_key,    # SHOWN ONCE — caller must save
        "scopes": key.scopes,
        "rate_limit_per_minute": rate_limit_per_minute,
        "warning": "Store full_key now; it cannot be retrieved later.",
    }


@admin_router.post("/tenants/{tenant_id}/keys/{key_id}/revoke")
async def revoke_api_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    reason: Annotated[str | None, Form()] = None,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    kr = ApiKeyRepo(session)
    key = await session.get(ApiKey, key_id)
    if key is None or key.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    if key.revoked_at is not None:
        return {"id": str(key.id), "status": "already_revoked"}
    await kr.revoke(key.id, reason=reason)
    await AuditRepo(session).record(
        actor_type="operator", actor_id=op.id, actor_label=op.email,
        action="key.revoke", result="success",
        tenant_id=tenant_id, target_type="api_key", target_id=str(key.id),
        payload={"reason": reason},
    )
    await session.commit()
    return {"id": str(key.id), "status": "revoked"}


# --------------------------------------------------------------------------- #
# Dashboard (HTMX-rendered)
# --------------------------------------------------------------------------- #
@admin_router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    nv_admin_access: Annotated[str | None, Cookie()] = None,
):
    template_name = "dashboard.html"
    if not nv_admin_access:
        template_name = "login.html"
    else:
        try:
            decode_operator_jwt(nv_admin_access, expected_type="access")
        except JWTError:
            template_name = "login.html"
    return templates.TemplateResponse(request, template_name)


@admin_router.get("/usage")
async def usage_summary(
    days: int = 30,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Per-tenant aggregate. Inexpensive at the current scale;
    materialized view if needed."""
    tenants = await TenantRepo(session).list_active()
    out = {}
    for t in tenants:
        ur = UsageRepo(session, t.id)
        out[t.slug] = await ur.summary_last_n_days(days)
    return {"days": days, "tenants": out}


# --------------------------------------------------------------------------- #
# Voice lifecycle (ADR-11) — operator-only freeze / unfreeze / purge
# --------------------------------------------------------------------------- #
# Operator routes address voices by their internal UUID (voices.id),
# not the tenant-scoped slug — admin context is cross-tenant. The
# tenant-side flow goes through POST /v1/data-deletion-requests.
def _voice_or_404(voice, voice_db_id: uuid.UUID):
    if voice is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"voice id='{voice_db_id}' not found",
        )


@admin_router.post("/voices/{voice_db_id}/freeze")
async def admin_freeze_voice(
    voice_db_id: uuid.UUID,
    reason: Annotated[str, Form(min_length=1, max_length=2048)],
    purge_after_days: Annotated[int | None, Form(ge=0, le=3650)] = None,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Operator manual freeze. Idempotent — re-freezing an already-
    frozen voice updates the reason. `purge_after_days` schedules
    purge eligibility N days out (typically 30); omit to freeze
    indefinitely.

    Use cases: abuse / incident response, talent withdrawal, partner
    revocation. The route does NOT cascade across tenants; operator
    can issue one freeze per voice or script a batch loop.
    """
    # Use an operator-mode VoiceRepo (tenant_id arg required by the
    # repo constructor; operator just passes a sentinel — VoiceRepo
    # only uses the tenant filter on the *accessibility* methods, and
    # the lifecycle methods (get_by_id, freeze, ...) operate on the
    # voice's UUID directly). Pass uuid.uuid4() so the repo invariant
    # holds; the value is unused by the lifecycle methods.
    repo = VoiceRepo(session, uuid.uuid4())
    voice = await repo.freeze(
        voice_db_id, reason=reason, purge_after_days=purge_after_days,
    )
    _voice_or_404(voice, voice_db_id)
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="voice.freeze",
        result="success",
        tenant_id=voice.owner_tenant_id,
        target_type="voice",
        target_id=str(voice.id),
        payload={
            "voice_slug": voice.voice_id,
            "reason": reason,
            "purge_after_days": purge_after_days,
        },
    )
    await session.commit()
    return {
        "voice_id": voice.voice_id,
        "frozen_at": voice.frozen_at.isoformat() if voice.frozen_at else None,
        "purge_after_at": (
            voice.purge_after_at.isoformat() if voice.purge_after_at else None
        ),
        "reason": voice.frozen_reason,
    }


@admin_router.post("/voices/{voice_db_id}/unfreeze")
async def admin_unfreeze_voice(
    voice_db_id: uuid.UUID,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Clear the frozen flag. Operator MUST verify the underlying
    cause is cleared (e.g. a fresh consent record has landed) before
    calling; the repo does not enforce that. Purged voices cannot be
    unfrozen (terminal). `purge_after_at` is NOT cleared by this call
    — once a purge is scheduled, an operator who wants to unschedule
    it must do so via direct SQL (rare; v0 has no API for that)."""
    repo = VoiceRepo(session, uuid.uuid4())
    voice = await repo.unfreeze(voice_db_id)
    _voice_or_404(voice, voice_db_id)
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="voice.unfreeze",
        result="success",
        tenant_id=voice.owner_tenant_id,
        target_type="voice",
        target_id=str(voice.id),
        payload={"voice_slug": voice.voice_id},
    )
    await session.commit()
    return {"voice_id": voice.voice_id, "frozen_at": None}


@admin_router.post("/voices/{voice_db_id}/purge")
async def admin_purge_voice(
    voice_db_id: uuid.UUID,
    confirm: Annotated[bool, Form()] = False,
    notes: Annotated[str | None, Form(max_length=2048)] = None,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Hard-delete reference audio + adapter weights from R2; anonymise
    the row. Terminal state — the voice becomes a tombstone (kept for
    usage_records / audit_log referential integrity). Requires
    `confirm=true` to guard against accidental triggers.

    R2 errors on individual artifact deletes are logged + counted but
    do NOT abort the purge — operator can re-run after fixing storage
    state; the DB row still gets scrubbed."""
    if not confirm:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "purge requires confirm=true; this operation is "
                "irreversible (reference audio + adapter weights "
                "are deleted, row anonymised)"
            ),
        )
    repo = VoiceRepo(session, uuid.uuid4())
    voice = await repo.get_by_id(voice_db_id)
    _voice_or_404(voice, voice_db_id)
    if voice.purged_at is not None:
        # Idempotent — return the tombstone state.
        return {
            "voice_id": voice.voice_id,
            "purged_at": voice.purged_at.isoformat(),
            "already_purged": True,
        }

    # Try artifact deletes (R2 + local file://); record what happened.
    # Don't abort on per-object failure — the DB scrub is the
    # irreversible part and we want to land it deterministically.
    # ADR-11 KVKK/GDPR right-to-erasure requires that EVERY artifact
    # location be addressed, including the local file:// path used by
    # the legacy enrollment flow (main.py writes
    # `file://{tenant_dir}/{voice_id}.wav`). Leaving those files on
    # disk after purge violates the erasure obligation.
    from pathlib import Path

    artifact_deletes: list[dict] = []
    storage = get_r2_storage()
    for label, uri in (
        ("reference", voice.reference_uri),
        ("adapter", voice.adapter_uri),
    ):
        if not uri:
            artifact_deletes.append(
                {"label": label, "uri": uri, "skipped": "no_uri"}
            )
            continue
        if uri.startswith("s3://") or uri.startswith("r2://"):
            try:
                storage.delete(uri)
                artifact_deletes.append(
                    {"label": label, "uri": uri, "ok": True, "kind": "r2"}
                )
            except Exception as e:  # noqa: BLE001 — log + continue
                artifact_deletes.append(
                    {"label": label, "uri": uri, "ok": False,
                     "kind": "r2", "error": str(e)},
                )
        elif uri.startswith("file://"):
            local_path = Path(uri.removeprefix("file://"))
            try:
                local_path.unlink(missing_ok=True)
                artifact_deletes.append(
                    {"label": label, "uri": uri, "ok": True, "kind": "file"}
                )
            except Exception as e:  # noqa: BLE001 — log + continue
                artifact_deletes.append(
                    {"label": label, "uri": uri, "ok": False,
                     "kind": "file", "error": str(e)},
                )
        else:
            artifact_deletes.append(
                {"label": label, "uri": uri, "skipped": "unknown_scheme"}
            )

    purged = await repo.execute_purge(voice_db_id)
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="voice.purge",
        result="success",
        tenant_id=purged.owner_tenant_id,
        target_type="voice",
        target_id=str(purged.id),
        payload={
            "voice_slug": purged.voice_id,
            "notes": notes,
            "artifact_deletes": artifact_deletes,
        },
    )
    await session.commit()
    return {
        "voice_id": purged.voice_id,
        "purged_at": purged.purged_at.isoformat(),
        "artifact_deletes": artifact_deletes,
    }


# --------------------------------------------------------------------------- #
# Data deletion request operator inbox (ADR-11)
# --------------------------------------------------------------------------- #
@admin_router.get("/data-deletion-requests")
async def admin_list_deletion_requests(
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Pending + in-progress deletion requests across all tenants,
    oldest first. Operator inbox for KVKK md. 11 / GDPR art. 17
    fulfillment."""
    tickets = await DataDeletionRequestRepo.as_operator(session).list_pending()
    return {
        "requests": [
            {
                "id": str(r.id),
                "tenant_id": str(r.tenant_id),
                "voice_slugs": list(r.voice_slugs or []),
                "jurisdiction": r.jurisdiction,
                "status": r.status,
                "requested_at": r.requested_at.isoformat(),
                "reason": r.reason,
            }
            for r in tickets
        ],
    }


@admin_router.post("/data-deletion-requests/{request_id}/complete")
async def admin_complete_deletion_request(
    request_id: uuid.UUID,
    notes: Annotated[str | None, Form(max_length=2048)] = None,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Mark the deletion request as completed. Operator MUST have
    already executed purge on the named voices via
    POST /admin/voices/{id}/purge — this endpoint only updates the
    audit ticket. The split keeps the destructive R2 + DB action
    separate from the bookkeeping mutation."""
    ticket = await DataDeletionRequestRepo.as_operator(
        session,
    ).mark_completed(request_id, notes=notes)
    if ticket is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"data deletion request '{request_id}' not found",
        )
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="data_deletion.complete",
        result="success",
        tenant_id=ticket.tenant_id,
        target_type="data_deletion_request",
        target_id=str(ticket.id),
        payload={"notes": notes},
    )
    await session.commit()
    return {
        "id": str(ticket.id),
        "status": ticket.status,
        "completed_at": (
            ticket.completed_at.isoformat() if ticket.completed_at else None
        ),
    }


@admin_router.post("/voices/{voice_db_id}/eval-pin")
async def admin_pin_voice_eval(
    voice_db_id: uuid.UUID,
    body: Annotated[EvalPinRequest, Body()],
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Pin an eval result onto the voice (ADR-12).

    The payload body matches the `voices.eval_metrics` blob shape — see
    docs/decisions/2026-05-28-eval-pin.md §4. Idempotent (overwrites).
    Pinning a purged voice is a no-op; pinning a frozen/deleted voice
    is allowed (operator may want a final pre-purge eval on record).

    The endpoint accepts unknown extra keys (forward-compat with newer
    eval harnesses). Pydantic strict validation only enforces the
    required envelope: schema_version, evaluated_at, test_set, metrics.
    """
    repo = VoiceRepo(session, uuid.uuid4())
    # Convert the validated Pydantic model back to a plain dict for
    # the JSONB column. mode="json" produces stable JSON-serializable
    # primitives (strings for datetimes, etc.) so the blob round-trips
    # identically across re-reads.
    payload = body.model_dump(mode="json", exclude_none=False)
    try:
        voice = await repo.pin_eval(voice_db_id, payload=payload)
    except ValueError as e:
        # Required-key violations from the repo's app-layer guard.
        # Pydantic catches the envelope; the repo catches the
        # `metrics` empty-dict edge case.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=str(e),
        ) from e
    _voice_or_404(voice, voice_db_id)
    if voice.purged_at is not None:
        # repo.pin_eval is a no-op on tombstones; surface the state
        # explicitly so an operator script doesn't silently believe
        # the pin succeeded.
        raise HTTPException(
            status.HTTP_410_GONE,
            detail=f"voice id='{voice_db_id}' is purged; cannot pin eval",
        )
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="voice.eval_pin",
        result="success",
        tenant_id=voice.owner_tenant_id,
        target_type="voice",
        target_id=str(voice.id),
        payload={
            "voice_slug": voice.voice_id,
            "schema_version": body.schema_version,
            "evaluated_at": body.evaluated_at,
            "test_set_slug": body.test_set.slug,
            "metric_names": sorted(body.metrics.keys()),
        },
    )
    await session.commit()
    return {
        "voice_id": voice.voice_id,
        "eval_metrics": voice.eval_metrics,
    }


# --------------------------------------------------------------------------- #
# Watermark key management + voice toggle + forensics (ADR-13)
# --------------------------------------------------------------------------- #
def _watermark_key_to_public(k) -> WatermarkKeyPublic:
    return WatermarkKeyPublic(
        id=str(k.id),
        message_bits=k.message_bits,
        label=k.label,
        allocated_at=k.allocated_at.isoformat(),
        retired_at=k.retired_at.isoformat() if k.retired_at else None,
        retired_reason=k.retired_reason,
        notes=k.notes,
        created_by_operator_id=(
            str(k.created_by_operator_id) if k.created_by_operator_id else None
        ),
    )


@admin_router.post(
    "/watermark-keys", response_model=WatermarkKeyPublic,
    status_code=status.HTTP_201_CREATED,
)
async def admin_allocate_watermark_key(
    body: Annotated[WatermarkKeyAllocateRequest, Body()],
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Allocate a new active watermark key. `message_bits=None` picks a
    random unused slot; explicit `message_bits` allocates that slot or
    409s if it's already active."""
    try:
        key = await WatermarkKeyRepo(session).allocate(
            label=body.label,
            created_by_operator_id=op.id,
            notes=body.notes,
            message_bits=body.message_bits,
        )
    except ValueError as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=str(e),
        ) from e
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="watermark_key.allocate",
        result="success",
        tenant_id=None,
        target_type="watermark_key",
        target_id=str(key.id),
        payload={
            "label": body.label,
            "message_bits": key.message_bits,
            "operator_specified_bits": body.message_bits is not None,
        },
    )
    await session.commit()
    return _watermark_key_to_public(key)


@admin_router.get("/watermark-keys")
async def admin_list_watermark_keys(
    include_retired: Annotated[bool, Query()] = False,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """List watermark keys. Default: active only. Pass
    `include_retired=true` to surface historical allocations for
    forensics review."""
    from db.models import WatermarkKey

    q = select(WatermarkKey)
    if not include_retired:
        q = q.where(WatermarkKey.retired_at.is_(None))
    q = q.order_by(WatermarkKey.allocated_at.desc())
    keys = list((await session.execute(q)).scalars().all())
    return {"keys": [_watermark_key_to_public(k).model_dump() for k in keys]}


@admin_router.post("/watermark-keys/{key_id}/retire")
async def admin_retire_watermark_key(
    key_id: uuid.UUID,
    body: Annotated[WatermarkKeyRetireRequest, Body()],
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Retire a key. Voices that reference it via watermark_key_id
    keep the FK (ON DELETE SET NULL doesn't trigger since the row
    isn't deleted); but the synth path treats the missing active
    lookup as a watermark skip. Operator should re-assign affected
    voices to a new key before retiring or be ready to accept the
    skip.
    """
    key = await WatermarkKeyRepo(session).retire(
        key_id, reason=body.reason,
    )
    if key is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"watermark key '{key_id}' not found",
        )
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="watermark_key.retire",
        result="success",
        tenant_id=None,
        target_type="watermark_key",
        target_id=str(key.id),
        payload={"reason": body.reason, "label": key.label},
    )
    await session.commit()
    return _watermark_key_to_public(key)


# License kinds that legally MUST stay watermarked. Disabling the
# watermark on these voices is refused by the toggle endpoint.
_WATERMARK_REQUIRED_LICENSE_KINDS = {
    "talent-contract",
    "public-figure",
    "partner-licensed",
}


@admin_router.post("/voices/{voice_db_id}/watermark/enable")
async def admin_enable_voice_watermark(
    voice_db_id: uuid.UUID,
    key_id: Annotated[uuid.UUID | None, Form()] = None,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Enable watermark on a voice. Optionally assign / re-assign a
    watermark key. If `key_id` is omitted and the voice has no key,
    the watermark stays inactive (enabled=true + key_id=null is a
    valid "ready but not yet keyed" state — synth path treats it as
    a skip until a key is bound)."""
    voice_repo = VoiceRepo(session, uuid.uuid4())
    voice = await voice_repo.get_by_id(voice_db_id)
    _voice_or_404(voice, voice_db_id)
    if key_id is not None:
        wk = await WatermarkKeyRepo(session).get_active(key_id)
        if wk is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"watermark key '{key_id}' is not active",
            )
        voice.watermark_key_id = wk.id
    voice.watermark_enabled = True
    await session.flush()
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="voice.watermark_enable",
        result="success",
        tenant_id=voice.owner_tenant_id,
        target_type="voice",
        target_id=str(voice.id),
        payload={
            "voice_slug": voice.voice_id,
            "key_id": str(voice.watermark_key_id) if voice.watermark_key_id else None,
        },
    )
    await session.commit()
    return {
        "voice_id": voice.voice_id,
        "watermark_enabled": True,
        "watermark_key_id": (
            str(voice.watermark_key_id) if voice.watermark_key_id else None
        ),
    }


@admin_router.post("/voices/{voice_db_id}/watermark/disable")
async def admin_disable_voice_watermark(
    voice_db_id: uuid.UUID,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Disable watermark on a voice. Refused on license_kinds that
    legally MUST be watermarked (talent-contract / public-figure /
    partner-licensed) — operator must change license_kind first
    (separate audited action)."""
    voice_repo = VoiceRepo(session, uuid.uuid4())
    voice = await voice_repo.get_by_id(voice_db_id)
    _voice_or_404(voice, voice_db_id)
    if voice.license_kind in _WATERMARK_REQUIRED_LICENSE_KINDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"voice license_kind='{voice.license_kind}' requires "
                "watermark; change license_kind first"
            ),
        )
    voice.watermark_enabled = False
    await session.flush()
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="voice.watermark_disable",
        result="success",
        tenant_id=voice.owner_tenant_id,
        target_type="voice",
        target_id=str(voice.id),
        payload={"voice_slug": voice.voice_id},
    )
    await session.commit()
    return {"voice_id": voice.voice_id, "watermark_enabled": False}


@admin_router.post(
    "/forensics/detect-watermark",
    response_model=WatermarkDetectionResultPublic,
)
async def admin_detect_watermark(
    audio: Annotated[UploadFile, File()],
    threshold: Annotated[float, Form(ge=0.0, le=1.0)] = 0.5,
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Operator-only forensics endpoint. Upload an audio file
    (wav/mp3/flac/ogg/m4a — anything `soundfile` can decode) and
    receive the detected AudioSeal watermark probability + decoded
    16-bit payload + the matched key allocation + voices currently
    bound to that key.

    The audio file is NOT persisted. The detection itself is logged
    to audit_log so a forensics audit trail exists (who ran which
    detection when), but the raw audio bytes are forgotten on response.
    """
    from datetime import datetime, timezone

    from audio.watermark import WatermarkDetector
    from server.config import settings

    # Bound the upload BEFORE the read so a 5 GB submission can't OOM
    # the gateway worker. The operator-only gate doesn't mean the
    # endpoint is safe — a compromised operator JWT or a fat-fingered
    # rsync should still get a 413, not a kernel OOM kill.
    max_bytes = settings.forensics_max_upload_mb * 1024 * 1024
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="empty audio upload",
        )
    if len(audio_bytes) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"audio upload exceeds {settings.forensics_max_upload_mb} MB "
                f"(got {len(audio_bytes) // (1024*1024)} MB)"
            ),
        )

    # Decode whatever format the operator uploaded → mono int16 PCM.
    # soundfile handles wav/flac/ogg natively; mp3/m4a need ffmpeg
    # via audioread — but for the forensics use case operators
    # generally have a wav/flac available.
    import io

    try:
        import numpy as np
        import soundfile as sf

        with io.BytesIO(audio_bytes) as buf:
            data, sr = sf.read(buf, dtype="int16", always_2d=False)
        if data.ndim == 2:
            # Mono-mix multichannel for AudioSeal.
            data = data.mean(axis=1).astype(np.int16)
        pcm_bytes = data.tobytes()
        sample_rate = int(sr)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"audio decode failed: {type(e).__name__}: {e}",
        ) from e

    # Run AudioSeal detection. Build a per-request detector so the
    # operator-supplied threshold is honored without mutating the
    # singleton's state.
    detector = WatermarkDetector(detection_threshold=threshold)
    try:
        result = detector.detect(pcm_bytes, sample_rate)
    except RuntimeError as e:
        # audioseal not installed / model load failed — forensics MUST
        # be honest, so we surface 503 rather than degrading silently.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e),
        ) from e

    matched_key_id: str | None = None
    matched_key_label: str | None = None
    matched_key_history: list[dict[str, str | None]] = []
    matched_voice_ids: list[str] = []
    if result.message is not None:
        # Use list_by_bits (active + retired) so audio generated by a
        # since-retired key still matches. A retired allocation may
        # legitimately share its 16-bit slot with a newer active key
        # (partial unique on retired_at IS NULL — see ADR-13 §1); the
        # full history lets the operator pick the right allocation
        # context for the audio's vintage.
        history = await WatermarkKeyRepo(session).list_by_bits(result.message)
        matched_key_history = [
            {
                "id": str(k.id),
                "label": k.label,
                "allocated_at": k.allocated_at.isoformat(),
                "retired_at": k.retired_at.isoformat() if k.retired_at else None,
            }
            for k in history
        ]
        if history:
            # Primary = most-recent allocation (active or retired).
            primary = history[0]
            matched_key_id = str(primary.id)
            matched_key_label = primary.label
            # Voice membership: every voice that has EVER been bound
            # to any of these key allocations. Today voices reference
            # via FK only, but the FK history shows what's currently
            # bound; future "voice unbinds key" events would need to
            # leave a trail to fully restore this set.
            key_ids = [k.id for k in history]
            voices = (await session.execute(
                select(Voice).where(Voice.watermark_key_id.in_(key_ids))
            )).scalars().all()
            matched_voice_ids = [v.voice_id for v in voices]

    # Truncate operator-supplied filename to a short, control-char-free
    # form before logging — defense against log-injection / stored-XSS
    # on a future audit log viewer.
    raw_filename = audio.filename or "(no-filename)"
    safe_filename = "".join(
        c for c in raw_filename if c.isprintable() and c not in "\r\n"
    )[:256]

    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="forensics.detect",
        result="success",
        tenant_id=None,
        target_type="watermark_key" if matched_key_id else "forensics_probe",
        target_id=matched_key_id or str(uuid.uuid4()),
        payload={
            "probability": result.probability,
            "decoded_message": result.message,
            "threshold": threshold,
            "matched_key_id": matched_key_id,
            "matched_key_history_count": len(matched_key_history),
            "matched_voice_ids": matched_voice_ids,
            "uploaded_filename": safe_filename,
            "uploaded_size_bytes": len(audio_bytes),
        },
    )
    await session.commit()

    return WatermarkDetectionResultPublic(
        probability=result.probability,
        message=result.message,
        matched_key_id=matched_key_id,
        matched_key_label=matched_key_label,
        matched_key_history=matched_key_history,
        matched_voice_ids=matched_voice_ids,
        sample_rate_used=result.sample_rate_used,
        duration_seconds=result.duration_seconds,
        detected_at=datetime.now(timezone.utc).isoformat(),
        detail=result.detail,
    )


@admin_router.post("/data-deletion-requests/{request_id}/reject")
async def admin_reject_deletion_request(
    request_id: uuid.UUID,
    reason: Annotated[str, Form(min_length=1, max_length=2048)],
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Reject a deletion request — e.g. legal hold, retention
    obligation, dispute under review. Reason is mandatory and is
    captured in completion_notes for audit."""
    ticket = await DataDeletionRequestRepo.as_operator(
        session,
    ).mark_rejected(request_id, reason=reason)
    if ticket is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"data deletion request '{request_id}' not found",
        )
    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="data_deletion.reject",
        result="success",
        tenant_id=ticket.tenant_id,
        target_type="data_deletion_request",
        target_id=str(ticket.id),
        payload={"reason": reason},
    )
    await session.commit()
    return {"id": str(ticket.id), "status": ticket.status}


# --------------------------------------------------------------------------- #
# Cascade endpoints — consent + talent contract revoke (ADR-11 follow-up)
# --------------------------------------------------------------------------- #
# ADR-11 promised: "consent revoke triggers voice freeze; talent_contract
# revoke triggers freeze on all dependent voices". The repo revoke()
# methods existed but had no call sites — synthesis was blocked by the
# gate (no active consent → 410), but `voices.frozen_at` stayed NULL so
# lifecycle_state reported 'active', misleading operator UI + downstream
# audit/automation. These endpoints close that cascade gap.
@admin_router.post("/voice-consents/{consent_id}/revoke")
async def admin_revoke_voice_consent(
    consent_id: uuid.UUID,
    reason: Annotated[str, Form(min_length=1, max_length=2048)],
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Revoke a consent record AND freeze the affected voice in the
    same transaction. Idempotent: if the consent is already revoked,
    the voice freeze step still runs (defensive — operators relying
    on this endpoint to fix a previously-uncascaded revoke get the
    expected state)."""
    consent_repo = VoiceConsentRecordRepo(session)
    consent = await consent_repo.revoke(consent_id, reason=reason)
    if consent is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"voice consent '{consent_id}' not found",
        )

    voice_repo = VoiceRepo(session, uuid.uuid4())
    voice = await voice_repo.freeze(
        consent.voice_id,
        reason=f"consent revoked: {reason}",
        purge_after_days=None,
    )
    if voice is None:
        # Consent row exists but voice already gone — log + commit
        # the consent revoke; nothing to freeze.
        logger.warning(
            "voice id=%s referenced by consent %s not found at cascade time",
            consent.voice_id, consent_id,
        )

    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="voice_consent.revoke",
        result="success",
        tenant_id=voice.owner_tenant_id if voice is not None else None,
        target_type="voice_consent_record",
        target_id=str(consent.id),
        payload={
            "voice_id": str(consent.voice_id),
            "reason": reason,
            "cascade_frozen_voice": (
                voice.voice_id if voice is not None else None
            ),
        },
    )
    await session.commit()
    return {
        "consent_id": str(consent.id),
        "revoked_at": (
            consent.revoked_at.isoformat() if consent.revoked_at else None
        ),
        "cascade_frozen_voice_id": (
            voice.voice_id if voice is not None else None
        ),
    }


@admin_router.post("/talent-contracts/{contract_id}/revoke")
async def admin_revoke_talent_contract(
    contract_id: uuid.UUID,
    reason: Annotated[str, Form(min_length=1, max_length=2048)],
    op = Depends(_current_operator),
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Revoke a talent contract AND freeze every voice that references
    it via license_ref. Cascade is route-orchestrated (per ADR-11
    "trigger orchestration route'ta, repos thin")."""
    contract_repo = TalentContractRepo(session)
    contract = await contract_repo.revoke(contract_id, revoked_at=None)
    if contract is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"talent contract '{contract_id}' not found",
        )

    # license_ref is TEXT (polymorphic); contract IDs land as the
    # stringified UUID. Match the in-DB representation exactly.
    contract_id_str = str(contract.id)
    dependents = list((await session.execute(
        select(Voice).where(
            Voice.license_kind == "talent-contract",
            Voice.license_ref == contract_id_str,
            Voice.purged_at.is_(None),
        )
    )).scalars().all())

    voice_repo = VoiceRepo(session, uuid.uuid4())
    frozen_voice_slugs: list[str] = []
    for v in dependents:
        result = await voice_repo.freeze(
            v.id,
            reason=f"talent contract revoked: {reason}",
            purge_after_days=None,
        )
        if result is not None:
            frozen_voice_slugs.append(result.voice_id)

    await AuditRepo(session).record(
        actor_type="operator",
        actor_id=op.id,
        actor_label=op.email,
        action="talent_contract.revoke",
        result="success",
        tenant_id=None,
        target_type="talent_contract",
        target_id=str(contract.id),
        payload={
            "reason": reason,
            "talent_full_name": contract.talent_full_name,
            "cascade_frozen_voice_count": len(frozen_voice_slugs),
            "cascade_frozen_voice_slugs": frozen_voice_slugs,
        },
    )
    await session.commit()
    return {
        "contract_id": str(contract.id),
        "revoked_at": (
            contract.revoked_at.isoformat() if contract.revoked_at else None
        ),
        "cascade_frozen_voice_count": len(frozen_voice_slugs),
        "cascade_frozen_voice_slugs": frozen_voice_slugs,
    }
