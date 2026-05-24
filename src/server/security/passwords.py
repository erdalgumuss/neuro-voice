"""Argon2id wrapper.

Parameters per OWASP Password Storage Cheat Sheet (2024):
    memory_cost = 64 MiB
    time_cost   = 3
    parallelism = 4
    hash_len    = 32

Same parameters used for both operator passwords AND API key secrets —
caller decides which.

A wall-clock verify of a single hash is intentionally ~50-100 ms.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_HASHER = PasswordHasher(
    memory_cost=65536,  # 64 MiB
    time_cost=3,
    parallelism=4,
    hash_len=32,
)


class SecretMismatchError(ValueError):
    """Raised by verify_secret when the hash does not match. Generic on
    purpose — calling code maps to opaque 401."""


def hash_secret(plain: str) -> str:
    if not plain:
        raise ValueError("cannot hash an empty secret")
    return _HASHER.hash(plain)


def verify_secret(stored_hash: str, plain: str) -> None:
    """Constant-time verify. Raises SecretMismatchError on mismatch.
    Re-raises argon2 InvalidHashError on malformed stored hash (a bug,
    not a user-facing error)."""
    try:
        _HASHER.verify(stored_hash, plain)
    except VerifyMismatchError as e:
        raise SecretMismatchError("secret does not match") from e
    except InvalidHashError:
        raise


def needs_rehash(stored_hash: str) -> bool:
    """Returns True when the stored hash was produced with parameters that
    differ from our current defaults — caller should re-hash on next
    successful login (Faz D cron also picks up the slack)."""
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
