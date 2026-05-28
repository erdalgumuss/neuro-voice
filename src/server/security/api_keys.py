"""API key generation + parsing.

Canonical shape:

    nv_<env>_<14-prefix>_<40-secret>
     │     │              └─ base62 random, ~238 bits entropy
     │     └─ base62 random, DB lookup index
     └─ environment: prod | staging | dev
"""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from typing import Literal

from .passwords import hash_secret

Environment = Literal["prod", "staging", "dev"]
_ALPHABET = string.ascii_letters + string.digits  # base62, no padding

PREFIX_LEN = 14
SECRET_LEN = 40

KEY_PREFIX_REGEX = re.compile(r"^nv_(prod|staging|dev)_[a-zA-Z0-9]{14}$")
KEY_FULL_REGEX = re.compile(r"^nv_(prod|staging|dev)_[a-zA-Z0-9]{14}_[a-zA-Z0-9]{40}$")


class APIKeyFormatError(ValueError):
    """Raised when an inbound token doesn't match the canonical shape."""


@dataclass(frozen=True)
class ParsedApiKey:
    environment: Environment
    prefix: str        # "nv_<env>_<14>"
    secret: str        # 40-char random


def _rand_base62(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def generate_api_key(environment: Environment = "prod") -> tuple[str, str, str]:
    """Returns (full_key, prefix, secret_hash).

    The full_key is shown to the user ONCE. The prefix and secret_hash go
    to the DB. The plaintext secret is intentionally not returned
    separately — caller must split it off if needed (rare).
    """
    if environment not in ("prod", "staging", "dev"):
        raise ValueError(f"environment must be prod|staging|dev, got {environment!r}")
    prefix_random = _rand_base62(PREFIX_LEN)
    secret = _rand_base62(SECRET_LEN)
    full_key = f"nv_{environment}_{prefix_random}_{secret}"
    prefix = f"nv_{environment}_{prefix_random}"
    return full_key, prefix, hash_secret(secret)


def parse_api_key(token: str) -> ParsedApiKey:
    """Validate format + split into (env, prefix, secret).

    Raises APIKeyFormatError on any deviation. The caller then looks up
    the prefix in the DB and verifies the secret with argon2id.
    """
    if not token or not isinstance(token, str):
        raise APIKeyFormatError("empty token")
    if not KEY_FULL_REGEX.match(token):
        raise APIKeyFormatError("token does not match nv_<env>_<14>_<40> format")
    # parts[0]="nv", parts[1]=env, parts[2]=prefix_random, parts[3]=secret
    parts = token.split("_")
    if len(parts) != 4:
        raise APIKeyFormatError("token must contain exactly three underscores")
    _, env, prefix_random, secret = parts
    return ParsedApiKey(
        environment=env,  # type: ignore[arg-type]
        prefix=f"nv_{env}_{prefix_random}",
        secret=secret,
    )
