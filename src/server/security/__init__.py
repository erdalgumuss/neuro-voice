"""Security primitives — argon2id hashing, API key generation/parsing,
JWT for operator sessions. All crypto choices anchored in
docs/architecture/auth-multi-tenant.md.

Public surface:
    hash_secret(plain)             argon2id hash with NQAI parameters
    verify_secret(hash, plain)     constant-time verify, raises on mismatch
    needs_rehash(hash)             parameter drift detection
    generate_api_key(env)          (full_key, prefix, secret_hash)
    parse_api_key(token)           (env, prefix, secret) — raises on bad format
    issue_operator_jwt(...)        sign access + refresh tokens
    decode_operator_jwt(...)       verify + decode access token
"""

from .passwords import hash_secret, needs_rehash, verify_secret
from .api_keys import (
    APIKeyFormatError,
    KEY_PREFIX_REGEX,
    ParsedApiKey,
    generate_api_key,
    parse_api_key,
)
from .jwt_tokens import (
    JWTError,
    OperatorClaims,
    decode_operator_jwt,
    issue_operator_jwt,
)

__all__ = [
    "hash_secret",
    "verify_secret",
    "needs_rehash",
    "generate_api_key",
    "parse_api_key",
    "ParsedApiKey",
    "APIKeyFormatError",
    "KEY_PREFIX_REGEX",
    "issue_operator_jwt",
    "decode_operator_jwt",
    "OperatorClaims",
    "JWTError",
]
