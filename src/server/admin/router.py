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
    Cookie,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ApiKey
from db.session import get_session
from repos import (
    ApiKeyRepo,
    AuditRepo,
    DataDeletionRequestRepo,
    OperatorRepo,
    TenantRepo,
    UsageRepo,
    VoiceRepo,
)
from storage.r2 import get_r2_storage
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

    # Try R2 deletes; record what happened. Don't abort on per-object
    # failure — the DB scrub is the irreversible part and we want to
    # land it deterministically.
    r2_deletes: list[dict] = []
    storage = get_r2_storage()
    for label, uri in (
        ("reference", voice.reference_uri),
        ("adapter", voice.adapter_uri),
    ):
        if not uri or not uri.startswith("s3://"):
            r2_deletes.append({"label": label, "uri": uri, "skipped": True})
            continue
        try:
            storage.delete(uri)
            r2_deletes.append({"label": label, "uri": uri, "ok": True})
        except Exception as e:  # noqa: BLE001 — log + continue
            r2_deletes.append(
                {"label": label, "uri": uri, "ok": False, "error": str(e)},
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
            "r2_deletes": r2_deletes,
        },
    )
    await session.commit()
    return {
        "voice_id": purged.voice_id,
        "purged_at": purged.purged_at.isoformat(),
        "r2_deletes": r2_deletes,
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
