"""Bearer-token auth. v0 keeps API keys in env (NQAI_API_KEYS, comma-separated).

For Faz-2 swap this out for a DB-backed key store with per-key rate limits,
scope grants, and rotation. The dependency surface here is intentionally small
so the swap is a one-file change.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import settings


def _constant_time_match(presented: str, allowed: list[str]) -> bool:
    presented_bytes = presented.encode("utf-8")
    for key in allowed:
        if hmac.compare_digest(presented_bytes, key.encode("utf-8")):
            return True
    return False


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
