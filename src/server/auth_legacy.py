"""Legacy env-list Bearer auth (v0.2 surface).

The v0.2 TTS endpoints in main.py still use `require_api_key` while the
multi-tenant DB-backed pipeline (src/server/auth.py) is being wired into
the new gateway. Faz A.6 migration completes the cutover; this module
goes away then.

Reads `NQAI_API_KEYS` (comma-separated). Constant-time compare. No DB,
no Redis, no audit log. Tests continue to exercise the old surface here.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import settings


def _constant_time_match(presented: str, allowed: list[str]) -> bool:
    presented_bytes = presented.encode("utf-8")
    return any(
        hmac.compare_digest(presented_bytes, key.encode("utf-8")) for key in allowed
    )


async def require_api_key(authorization: str | None = Header(default=None)) -> str:
    """Validate `Authorization: Bearer <key>` against `NQAI_API_KEYS`.

    Returns a short fingerprint of the key (first 8 chars) for audit logging.
    """
    if not settings.require_auth:
        return "anonymous"

    if not settings.api_keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="server has no API keys configured (set NQAI_API_KEYS or NQAI_REQUIRE_AUTH=false)",
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented = authorization[len("bearer "):].strip()
    if not _constant_time_match(presented, settings.api_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return presented[:8] + "…"
