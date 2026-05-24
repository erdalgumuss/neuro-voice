"""Reference URI resolver contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.reference_resolver import (
    ReferenceAudioMissing,
    UnsupportedReferenceURI,
    resolve_reference_uri,
)


def _make_wav(tmp_path: Path, name: str = "ref.wav") -> Path:
    p = tmp_path / name
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")  # bogus but file-on-disk
    return p


def test_file_uri_with_absolute_path(tmp_path):
    f = _make_wav(tmp_path)
    resolved = resolve_reference_uri(f"file://{f}")
    assert resolved == f


def test_bare_absolute_path(tmp_path):
    f = _make_wav(tmp_path)
    assert resolve_reference_uri(str(f)) == f


def test_relative_path_resolved_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = _make_wav(tmp_path, "local.wav")
    resolved = resolve_reference_uri("local.wav")
    assert resolved == f


def test_missing_file_raises():
    with pytest.raises(ReferenceAudioMissing):
        resolve_reference_uri("/tmp/__definitely_not_here__.wav")


def test_empty_uri_rejected():
    with pytest.raises(UnsupportedReferenceURI):
        resolve_reference_uri("")


def test_s3_scheme_not_yet_wired():
    with pytest.raises(UnsupportedReferenceURI, match="R2 fetcher not wired"):
        resolve_reference_uri("s3://bucket/voices/neeko.wav")


def test_r2_scheme_also_routes_to_s3_fetcher():
    with pytest.raises(UnsupportedReferenceURI, match="R2 fetcher not wired"):
        resolve_reference_uri("r2://bucket/voices/neeko.wav")


def test_unknown_scheme_rejected():
    with pytest.raises(UnsupportedReferenceURI, match="unknown URI scheme"):
        resolve_reference_uri("ftp://server/path.wav")


def test_file_uri_with_url_escapes(tmp_path):
    f = _make_wav(tmp_path, "bos alan.wav")
    encoded = str(f).replace(" ", "%20")
    assert resolve_reference_uri(f"file://{encoded}") == f
