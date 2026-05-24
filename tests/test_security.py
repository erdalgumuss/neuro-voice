"""Security primitive contract tests — argon2, API key gen/parse, JWT."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from server.security import (
    KEY_PREFIX_REGEX,
    APIKeyFormatError,
    decode_operator_jwt,
    generate_api_key,
    hash_secret,
    issue_operator_jwt,
    needs_rehash,
    parse_api_key,
    verify_secret,
)
from server.security.jwt_tokens import (
    JWTExpiredError,
    JWTTamperedError,
    JWTTypeMismatchError,
)
from server.security.passwords import SecretMismatchError


# --------------------------------------------------------------------------- #
# Argon2id
# --------------------------------------------------------------------------- #
def test_hash_verify_round_trip():
    h = hash_secret("hunter2-and-more")
    verify_secret(h, "hunter2-and-more")  # no exception


def test_verify_wrong_password_raises():
    h = hash_secret("right")
    with pytest.raises(SecretMismatchError):
        verify_secret(h, "wrong")


def test_hash_is_argon2id_format():
    h = hash_secret("anything")
    assert h.startswith("$argon2id$")


def test_two_hashes_of_same_input_differ():
    """Argon2 uses random salt — same input → different hashes."""
    a = hash_secret("identical")
    b = hash_secret("identical")
    assert a != b
    verify_secret(a, "identical")
    verify_secret(b, "identical")


def test_empty_secret_rejected():
    with pytest.raises(ValueError):
        hash_secret("")


def test_needs_rehash_false_for_fresh_hash():
    h = hash_secret("fresh")
    assert needs_rehash(h) is False


# --------------------------------------------------------------------------- #
# API key generation + parsing
# --------------------------------------------------------------------------- #
def test_generate_api_key_default_environment():
    full, prefix, secret_hash = generate_api_key()
    assert full.startswith("nqai_prod_")
    assert prefix.startswith("nqai_prod_")
    assert KEY_PREFIX_REGEX.match(prefix)
    assert secret_hash.startswith("$argon2id$")


def test_generate_api_key_dev_environment():
    full, prefix, _ = generate_api_key("dev")
    assert full.startswith("nqai_dev_")
    assert prefix.startswith("nqai_dev_")


def test_generated_keys_are_unique():
    fulls = {generate_api_key()[0] for _ in range(50)}
    assert len(fulls) == 50


def test_parse_round_trip():
    full, prefix, secret_hash = generate_api_key("staging")
    parsed = parse_api_key(full)
    assert parsed.environment == "staging"
    assert parsed.prefix == prefix
    verify_secret(secret_hash, parsed.secret)


def test_parse_rejects_garbage():
    with pytest.raises(APIKeyFormatError):
        parse_api_key("")
    with pytest.raises(APIKeyFormatError):
        parse_api_key("not-a-key")
    with pytest.raises(APIKeyFormatError):
        parse_api_key("nqai_prod_short_secret")  # wrong lengths
    with pytest.raises(APIKeyFormatError):
        parse_api_key("nqai_test_aaaaaaaaaaaaaa_" + "x" * 40)  # bad env


def test_parse_rejects_special_chars():
    bad = "nqai_prod_aaaaaaaaaaaaaa_" + "/" * 40
    with pytest.raises(APIKeyFormatError):
        parse_api_key(bad)


def test_generate_invalid_env_raises():
    with pytest.raises(ValueError):
        generate_api_key("nope")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")


def test_jwt_round_trip_access():
    op_id = uuid.uuid4()
    access, refresh, family = issue_operator_jwt(op_id, ["admin"])
    claims = decode_operator_jwt(access, expected_type="access")
    assert claims.operator_id == op_id
    assert claims.roles == ["admin"]
    assert claims.token_type == "access"
    assert claims.expires_at > claims.issued_at


def test_jwt_refresh_carries_family():
    op_id = uuid.uuid4()
    _, refresh, family = issue_operator_jwt(op_id, ["admin"])
    claims = decode_operator_jwt(refresh, expected_type="refresh")
    assert claims.family == family


def test_jwt_type_mismatch():
    op_id = uuid.uuid4()
    access, refresh, _ = issue_operator_jwt(op_id, ["admin"])
    # Try to use refresh as access
    with pytest.raises(JWTTypeMismatchError):
        decode_operator_jwt(refresh, expected_type="access")
    with pytest.raises(JWTTypeMismatchError):
        decode_operator_jwt(access, expected_type="refresh")


def test_jwt_tampered_signature_rejected():
    op_id = uuid.uuid4()
    access, _, _ = issue_operator_jwt(op_id, ["admin"])
    # Flip last char to invalidate signature
    tampered = access[:-1] + ("a" if access[-1] != "a" else "b")
    with pytest.raises(JWTTamperedError):
        decode_operator_jwt(tampered)


def test_jwt_requires_secret_env(monkeypatch):
    monkeypatch.delenv("NQAI_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="NQAI_JWT_SECRET"):
        issue_operator_jwt(uuid.uuid4(), ["admin"])


def test_jwt_rejects_too_short_secret(monkeypatch):
    monkeypatch.setenv("NQAI_JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="32 chars"):
        issue_operator_jwt(uuid.uuid4(), ["admin"])


def test_jwt_expired_rejected():
    """Forge an already-expired token and decode it through our public path."""
    import jwt as pyjwt

    op_id = uuid.uuid4()
    now = int(time.time())
    expired = pyjwt.encode(
        {
            "iss": "nqai-voice",
            "sub": str(op_id),
            "iat": now - 7200,
            "exp": now - 3600,  # expired one hour ago
            "type": "access",
            "roles": ["admin"],
        },
        os.environ["NQAI_JWT_SECRET"],
        algorithm="HS256",
    )
    with pytest.raises(JWTExpiredError):
        decode_operator_jwt(expired)


def test_jwt_wrong_issuer_rejected(monkeypatch):
    """A JWT signed by us but with a wrong `iss` claim should fail decode."""
    import jwt as pyjwt

    bogus = pyjwt.encode(
        {
            "iss": "evil-co",
            "sub": str(uuid.uuid4()),
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "type": "access",
        },
        "test-secret-must-be-at-least-32-chars-long",
        algorithm="HS256",
    )
    with pytest.raises(JWTTamperedError):
        decode_operator_jwt(bogus)
