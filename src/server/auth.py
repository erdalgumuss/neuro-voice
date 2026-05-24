"""Bearer authentication — DB-backed (Postgres) with scope check + Redis
rate limit + audit log. This replaces the v0.2 env-list auth.

Critical path order (auth-multi-tenant.md §1.3):
    1. parse Bearer token (regex-validate)            ── format error → 401
    2. DB lookup by prefix (active only)              ── miss          → 401
    3. argon2id verify(stored_hash, presented secret) ── mismatch      → 401
    4. scope check                                    ── missing       → 403
    5. rate limit (per-key then per-tenant)           ── over          → 429
    6. fire-and-forget: touch_last_used + audit.record

Returns an `AuthContext` for downstream handlers — they get a clean
(tenant, api_key) tuple ready to drive repos.

Failures always return *generic* messages — no information leakage
about which dimension failed.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ApiKey, Tenant
from db.session import get_session
from observability import TTS_REQUESTS
from repos import ApiKeyRepo, AuditRepo
from server.config import settings
from server.rate_limit import RateLimiter
from server.security import APIKeyFormatError, parse_api_key
from server.security.passwords import SecretMismatchError, verify_secret

GENERIC_AUTH_ERROR = "invalid or revoked api key"


def _emit_auth_failed(tenant_slug: str = "unknown") -> None:
    """Fire the SLO-denominator counter for an auth/rate-limit failure
    (audit L4 H1 2026-05-25). Pre-fix `status=auth_failed` was declared
    in the metric enum but nothing emitted it — alerts on auth-fail
    spikes would have stayed silent.

    `tenant` label stays `"unknown"` for failures BEFORE we identify
    the tenant (steps 1-3 of the pipeline); we don't fabricate an
    identity. `voice` is always "unknown" at auth time — the body
    hasn't been parsed yet. Cardinality stays bounded: small enum +
    legit tenant slugs.
    """
    import contextlib
    # Metric emission MUST NOT break the auth path.
    with contextlib.suppress(Exception):
        TTS_REQUESTS.labels(
            tenant=tenant_slug,
            voice="unknown",
            status="auth_failed",
        ).inc()


# --------------------------------------------------------------------------- #
# AuthContext
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AuthContext:
    """Everything a downstream route needs about the authenticated caller."""
    tenant: Tenant
    api_key: ApiKey

    @property
    def tenant_id(self) -> UUID:
        return self.tenant.id

    @property
    def api_key_id(self) -> UUID:
        return self.api_key.id

    def has_scope(self, scope: str) -> bool:
        return scope in (self.api_key.scopes or [])


# --------------------------------------------------------------------------- #
# Redis dependency
# --------------------------------------------------------------------------- #
_redis_client: Redis | None = None


def get_redis() -> Redis:
    """Process-wide Redis connection.

    Tests override this via ``app.dependency_overrides[get_redis]`` (the
    canonical FastAPI test-injection path) rather than mutating the global,
    so a stale singleton can never leak across test fixtures.
    """
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("NQAI_REDIS_URL", "redis://localhost:6379/0")
        _redis_client = Redis.from_url(url, decode_responses=False)
    return _redis_client


# --------------------------------------------------------------------------- #
# Core authentication
# --------------------------------------------------------------------------- #
async def _audit_async(
    session: AsyncSession,
    *,
    action: str,
    result: str,
    tenant_id: UUID | None = None,
    actor_id: UUID | None = None,
    actor_label: str | None = None,
    request: Request | None = None,
    payload: dict | None = None,
) -> None:
    """Fire-and-forget audit write. Swallows errors — audit must never
    sink the request path."""
    try:
        ar = AuditRepo(session)
        await ar.record(
            actor_type="api_key" if actor_id else "system",
            actor_id=actor_id,
            actor_label=actor_label,
            tenant_id=tenant_id,
            action=action,
            result=result,
            ip_addr=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
            payload=payload or {},
        )
        await session.commit()
    except Exception:
        # Audit failures must not propagate — log via structlog in prod.
        await session.rollback()


async def authenticate_bearer(
    authorization: str | None,
    *,
    session: AsyncSession,
    redis: Redis,
    required_scopes: tuple[str, ...] = (),
    request: Request | None = None,
) -> AuthContext:
    """Run the full auth pipeline. Raise HTTPException on any failure.

    Returns AuthContext on success. Designed to be wrapped in a FastAPI
    Depends — see `require_auth(...)` factory below.
    """
    # ---- 1. Bearer header -------------------------------------------------
    if not authorization or not authorization.lower().startswith("bearer "):
        await _audit_async(session, action="auth.fail", result="denied",
                           payload={"reason": "missing_bearer"}, request=request)
        _emit_auth_failed()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_AUTH_ERROR,
            headers={"WWW-Authenticate": 'Bearer realm="nqai-voice"'},
        )

    token = authorization[len("bearer "):].strip()

    # ---- 2. Parse format --------------------------------------------------
    try:
        parsed = parse_api_key(token)
    except APIKeyFormatError as e:
        await _audit_async(session, action="auth.fail", result="denied",
                           payload={"reason": "bad_format"}, request=request)
        _emit_auth_failed()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_AUTH_ERROR,
            headers={"WWW-Authenticate": 'Bearer realm="nqai-voice"'},
        ) from e

    # ---- 3. DB lookup by prefix -------------------------------------------
    kr = ApiKeyRepo(session)
    found = await kr.lookup_active_by_prefix(parsed.prefix)
    if found is None:
        await _audit_async(session, action="auth.fail", result="denied",
                           actor_label=parsed.prefix,
                           payload={"reason": "unknown_prefix"}, request=request)
        _emit_auth_failed()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_AUTH_ERROR,
            headers={"WWW-Authenticate": 'Bearer realm="nqai-voice"'},
        )
    key, tenant = found

    # ---- 4. argon2id verify (constant-time) -------------------------------
    try:
        verify_secret(key.secret_hash, parsed.secret)
    except SecretMismatchError as e:
        await _audit_async(session, action="auth.fail", result="denied",
                           tenant_id=tenant.id, actor_id=key.id,
                           actor_label=key.prefix,
                           payload={"reason": "secret_mismatch"}, request=request)
        _emit_auth_failed(tenant_slug=tenant.slug)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_AUTH_ERROR,
            headers={"WWW-Authenticate": 'Bearer realm="nqai-voice"'},
        ) from e

    # ---- 5. Tenant status check -------------------------------------------
    if tenant.status != "active":
        await _audit_async(session, action="auth.fail", result="denied",
                           tenant_id=tenant.id, actor_id=key.id,
                           actor_label=key.prefix,
                           payload={"reason": "tenant_status_" + tenant.status},
                           request=request)
        _emit_auth_failed(tenant_slug=tenant.slug)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=GENERIC_AUTH_ERROR,
            headers={"WWW-Authenticate": 'Bearer realm="nqai-voice"'},
        )

    # ---- 6. Scope check ---------------------------------------------------
    if required_scopes:
        missing = [s for s in required_scopes if s not in (key.scopes or [])]
        if missing:
            await _audit_async(session, action="auth.fail", result="denied",
                               tenant_id=tenant.id, actor_id=key.id,
                               actor_label=key.prefix,
                               payload={"reason": "insufficient_scope",
                                        "missing": missing}, request=request)
            _emit_auth_failed(tenant_slug=tenant.slug)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient scope",
                headers={"WWW-Authenticate":
                         f'Bearer error="insufficient_scope", scope="{" ".join(required_scopes)}"'},
            )

    # ---- 7. Rate limit (per-key, then per-tenant) -------------------------
    limiter = RateLimiter(redis)
    key_check = await limiter.check_api_key(
        key.id, per_minute=key.rate_limit_per_minute
    )
    if not key_check.allowed:
        await _audit_async(session, action="auth.rate_limited", result="denied",
                           tenant_id=tenant.id, actor_id=key.id,
                           actor_label=key.prefix,
                           payload={"limit": key.rate_limit_per_minute,
                                    "count": key_check.count}, request=request)
        _emit_auth_failed(tenant_slug=tenant.slug)
        retry_s = max(1, key_check.retry_after_ms // 1000 + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limited",
            headers={"Retry-After": str(retry_s)},
        )

    tenant_limit = settings.tenant_rate_limit_per_minute
    tenant_check = await limiter.check_tenant(tenant.id, per_minute=tenant_limit)
    if not tenant_check.allowed:
        await _audit_async(session, action="auth.rate_limited", result="denied",
                           tenant_id=tenant.id, actor_id=key.id,
                           actor_label=key.prefix,
                           payload={"scope": "tenant", "limit": tenant_limit,
                                    "count": tenant_check.count}, request=request)
        _emit_auth_failed(tenant_slug=tenant.slug)
        retry_s = max(1, tenant_check.retry_after_ms // 1000 + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limited (tenant)",
            headers={"Retry-After": str(retry_s)},
        )

    # ---- 8. Success — fire-and-forget side effects ------------------------
    # last_used touch is intentionally awaited here so it lands in the
    # same transaction; for ultra-low-latency we'd push to a Redis HSET
    # and flush every N seconds (Faz C+).
    await kr.touch_last_used(key.id)
    await _audit_async(session, action="auth.success", result="success",
                       tenant_id=tenant.id, actor_id=key.id,
                       actor_label=key.prefix, request=request)

    return AuthContext(tenant=tenant, api_key=key)


# --------------------------------------------------------------------------- #
# FastAPI dependency factory
# --------------------------------------------------------------------------- #
def require_auth(*required_scopes: str) -> Callable[..., Awaitable[AuthContext]]:
    """Build a FastAPI dependency enforcing the given scopes.

    Usage:
        @app.post("/v1/tts")
        async def synthesize(
            ctx: Annotated[AuthContext, Depends(require_auth("tts:write"))],
            ...,
        ): ...
    """
    async def _dep(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        session: Annotated[AsyncSession, Depends(get_session)] = ...,
        redis: Annotated[Redis, Depends(get_redis)] = ...,
    ) -> AuthContext:
        return await authenticate_bearer(
            authorization,
            session=session,
            redis=redis,
            required_scopes=required_scopes,
            request=request,
        )
    return _dep
