"""R2Storage contract tests with the moto S3 mock.

moto stands up an in-process S3 service that speaks the AWS SDK API
verbatim. Since R2 also speaks that API, the only thing we'd add for a
"real R2" test would be an `endpoint_url` and credentials — the boto3
call sites stay identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

from storage.r2 import S3URI, R2Storage, get_r2_storage  # noqa: E402

BUCKET = "nqai-voice-test"


@pytest.fixture
def r2(tmp_path):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield R2Storage(
            default_bucket=BUCKET,
            cache_dir=tmp_path / "cache",
            s3_client=client,
        )


def _write_local(tmp_path: Path, name: str, content: bytes = b"hello world") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


# --------------------------------------------------------------------------- #
# S3URI parse
# --------------------------------------------------------------------------- #
def test_uri_parse_basic():
    parsed = S3URI.parse("s3://my-bucket/some/key.wav")
    assert parsed.bucket == "my-bucket"
    assert parsed.key == "some/key.wav"
    assert parsed.uri == "s3://my-bucket/some/key.wav"


def test_uri_parse_accepts_r2_scheme():
    parsed = S3URI.parse("r2://my-bucket/voices/neeko.wav")
    assert parsed.bucket == "my-bucket"
    assert parsed.key == "voices/neeko.wav"


def test_uri_parse_rejects_other_schemes():
    for bad in ("", "file:///tmp/x.wav", "https://example.com/x", "x", "s3://bucket"):
        with pytest.raises(ValueError):
            S3URI.parse(bad)


# --------------------------------------------------------------------------- #
# upload + exists + delete
# --------------------------------------------------------------------------- #
def test_upload_file_round_trip(r2, tmp_path):
    src = _write_local(tmp_path, "ref.wav", b"RIFF\x00\x00\x00\x00WAVE")
    uri = r2.upload_file(src, "voices/neeko/ref.wav", content_type="audio/wav")
    assert uri.uri == f"s3://{BUCKET}/voices/neeko/ref.wav"
    assert r2.exists(uri.uri)


def test_upload_bytes(r2):
    uri = r2.upload_bytes(b'{"foo":1}', "manifests/x.json", content_type="application/json")
    assert r2.exists(uri.uri)


def test_exists_returns_false_for_missing_key(r2):
    assert r2.exists(f"s3://{BUCKET}/does/not/exist.wav") is False


def test_upload_missing_local_file_raises(r2):
    with pytest.raises(FileNotFoundError):
        r2.upload_file(Path("/tmp/__nope__.wav"), "voices/x.wav")


def test_delete_round_trip(r2, tmp_path):
    src = _write_local(tmp_path, "to-delete.wav")
    uri = r2.upload_file(src, "trash/to-delete.wav")
    assert r2.exists(uri.uri)
    r2.delete(uri.uri)
    assert r2.exists(uri.uri) is False


# --------------------------------------------------------------------------- #
# download_to_cache
# --------------------------------------------------------------------------- #
def test_download_to_cache_pulls_file(r2, tmp_path):
    src = _write_local(tmp_path, "ref.wav", b"RIFF-test-payload")
    uri = r2.upload_file(src, "voices/test.wav")
    cached = r2.download_to_cache(uri.uri)
    assert cached.is_file()
    assert cached.read_bytes() == b"RIFF-test-payload"


def test_download_to_cache_is_idempotent(r2, tmp_path):
    src = _write_local(tmp_path, "ref.wav", b"first")
    uri = r2.upload_file(src, "voices/idem.wav")
    first = r2.download_to_cache(uri.uri)
    first_mtime = first.stat().st_mtime
    src.write_bytes(b"second")
    r2.upload_file(src, "voices/idem.wav")
    second = r2.download_to_cache(uri.uri)
    assert second == first
    assert second.stat().st_mtime == first_mtime
    assert second.read_bytes() == b"first"


def test_download_to_cache_distinct_uris_get_distinct_files(r2, tmp_path):
    a = _write_local(tmp_path, "a.wav", b"AAA")
    b = _write_local(tmp_path, "b.wav", b"BBB")
    uri_a = r2.upload_file(a, "voices/a.wav")
    uri_b = r2.upload_file(b, "voices/b.wav")
    cached_a = r2.download_to_cache(uri_a.uri)
    cached_b = r2.download_to_cache(uri_b.uri)
    assert cached_a != cached_b
    assert cached_a.read_bytes() == b"AAA"
    assert cached_b.read_bytes() == b"BBB"


def test_download_to_cache_preserves_suffix(r2, tmp_path):
    src = _write_local(tmp_path, "voice.mp3", b"id3-blob")
    uri = r2.upload_file(src, "voices/voice.mp3")
    cached = r2.download_to_cache(uri.uri)
    assert cached.suffix == ".mp3"


# --------------------------------------------------------------------------- #
# presigned URLs
# --------------------------------------------------------------------------- #
def test_presigned_url_includes_signature(r2, tmp_path):
    src = _write_local(tmp_path, "p.wav")
    uri = r2.upload_file(src, "voices/p.wav")
    url = r2.presigned_get_url(uri.uri, expires_in=300)
    assert url.startswith("https://") or url.startswith("http://")
    assert "Signature=" in url or "X-Amz-Signature=" in url


def test_presigned_url_rejects_invalid_ttl(r2):
    src_uri = f"s3://{BUCKET}/voices/x.wav"
    for bad in (0, -1, 8 * 24 * 3600):
        with pytest.raises(ValueError):
            r2.presigned_get_url(src_uri, expires_in=bad)


# --------------------------------------------------------------------------- #
# Singleton factory
# --------------------------------------------------------------------------- #
def test_get_r2_storage_requires_env(monkeypatch):
    import storage.r2 as r2_mod

    monkeypatch.setattr(r2_mod, "_singleton", None)
    monkeypatch.delenv("NQAI_R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("NQAI_R2_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="NQAI_R2_ACCOUNT_ID"):
        get_r2_storage()
