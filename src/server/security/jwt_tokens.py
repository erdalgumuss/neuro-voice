"""Operator JWT (HS256 v1, RS256 + rotation ).

Per docs/architecture/auth-multi-tenant.md §2:
    - access token TTL: 1 hour
    - refresh token TTL: 7 days
    - refresh tokens carry a `family` UUID for replay-attack detection
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import jwt

ACCESS_TTL_SECONDS = 60 * 60          # 1h
REFRESH_TTL_SECONDS = 60 * 60 * 24 * 7  # 7d
ISSUER = "neurovoice"
ALGORITHM = "HS256"


class JWTError(Exception):
    """Generic JWT failure — sub-classed by tampered / expired / wrong type."""


class JWTExpiredError(JWTError): ...
class JWTTamperedError(JWTError): ...
class JWTTypeMismatchError(JWTError): ...


@dataclass(frozen=True)
class OperatorClaims:
    operator_id: uuid.UUID
    roles: list[str]
    issued_at: int
    expires_at: int
    token_type: Literal["access", "refresh"]
    family: uuid.UUID | None = None  # only on refresh tokens


def _signing_key() -> str:
    key = os.environ.get("NEUROVOICE_JWT_SECRET")
    if not key or len(key) < 32:
        raise RuntimeError(
            "NEUROVOICE_JWT_SECRET must be set and at least 32 chars. "
            "Generate via: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return key


def issue_operator_jwt(
    operator_id: uuid.UUID,
    roles: list[str],
    *,
    family: uuid.UUID | None = None,
) -> tuple[str, str, uuid.UUID]:
    """Returns (access_token, refresh_token, family_id).

    `family_id` is a UUID that links refresh tokens — when a refresh is
    rotated, the new refresh keeps the same family, and the old jti is
    blacklisted (Redis SET + 7d TTL). Replay of an old refresh
    invalidates the entire family.
    """
    now = int(time.time())
    family_id = family or uuid.uuid4()
    secret = _signing_key()

    access_claims = {
        "iss": ISSUER,
        "sub": str(operator_id),
        "iat": now,
        "exp": now + ACCESS_TTL_SECONDS,
        "type": "access",
        "scope": "admin",
        "roles": roles,
    }
    refresh_claims = {
        "iss": ISSUER,
        "sub": str(operator_id),
        "iat": now,
        "exp": now + REFRESH_TTL_SECONDS,
        "type": "refresh",
        "family": str(family_id),
        "jti": uuid.uuid4().hex,
    }
    access = jwt.encode(access_claims, secret, algorithm=ALGORITHM)
    refresh = jwt.encode(refresh_claims, secret, algorithm=ALGORITHM)
    return access, refresh, family_id


def decode_operator_jwt(
    token: str,
    *,
    expected_type: Literal["access", "refresh"] = "access",
) -> OperatorClaims:
    secret = _signing_key()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["iss", "sub", "iat", "exp", "type"]},
            issuer=ISSUER,
        )
    except jwt.ExpiredSignatureError as e:
        raise JWTExpiredError("token expired") from e
    except jwt.InvalidTokenError as e:
        raise JWTTamperedError(f"invalid token: {e}") from e

    if payload.get("type") != expected_type:
        raise JWTTypeMismatchError(
            f"expected {expected_type}, got {payload.get('type')!r}"
        )

    return OperatorClaims(
        operator_id=uuid.UUID(payload["sub"]),
        roles=payload.get("roles", []),
        issued_at=int(payload["iat"]),
        expires_at=int(payload["exp"]),
        token_type=expected_type,
        family=uuid.UUID(payload["family"]) if "family" in payload else None,
    )
