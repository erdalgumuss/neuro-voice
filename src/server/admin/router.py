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
    OperatorRepo,
    TenantRepo,
    UsageRepo,
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
