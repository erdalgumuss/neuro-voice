"""Reference URI resolver contract.

The whole test file uses local imports so it survives `sys.modules`
resets from sibling suites (test_api_smoke.py reloads server.*
between tests to pick up env changes — if we held module-level
references here they'd point at the stale module after that reset).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _resolver():
    """Import fresh on every call — beats module-level pollution."""
    from storage.reference_resolver import (
        ReferenceAudioMissing,
        UnsupportedReferenceURI,
        resolve_reference_uri,
        set_remote_fetcher,
    )

    return (
        resolve_reference_uri,
        set_remote_fetcher,
        ReferenceAudioMissing,
        UnsupportedReferenceURI,
    )


def _make_wav(tmp_path: Path, name: str = "ref.wav") -> Path:
    p = tmp_path / name
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    return p


# --------------------------------------------------------------------------- #
# file:// + bare paths
# --------------------------------------------------------------------------- #
def test_file_uri_with_absolute_path(tmp_path):
    resolve, *_ = _resolver()
    f = _make_wav(tmp_path)
    assert resolve(f"file://{f}") == f


def test_bare_absolute_path(tmp_path):
    resolve, *_ = _resolver()
    f = _make_wav(tmp_path)
    assert resolve(str(f)) == f


def test_relative_path_resolved_against_cwd(tmp_path, monkeypatch):
    resolve, *_ = _resolver()
    monkeypatch.chdir(tmp_path)
    f = _make_wav(tmp_path, "local.wav")
    assert resolve("local.wav") == f


def test_missing_file_raises():
    resolve, _set, _missing, _unsupported = _resolver()
    with pytest.raises(_missing):
        resolve("/tmp/__definitely_not_here__.wav")


def test_empty_uri_rejected():
    resolve, _set, _missing, unsupported = _resolver()
    with pytest.raises(unsupported):
        resolve("")


def test_unknown_scheme_rejected():
    resolve, _set, _missing, unsupported = _resolver()
    with pytest.raises(unsupported, match="unknown URI scheme"):
        resolve("ftp://server/path.wav")


def test_file_uri_with_url_escapes(tmp_path):
    resolve, *_ = _resolver()
    f = _make_wav(tmp_path, "bos alan.wav")
    encoded = str(f).replace(" ", "%20")
    assert resolve(f"file://{encoded}") == f


# --------------------------------------------------------------------------- #
# s3:// / r2:// via injected fetcher
# --------------------------------------------------------------------------- #
def test_s3_scheme_routes_through_injected_fetcher(tmp_path):
    resolve, set_fetcher, *_ = _resolver()
    cached = _make_wav(tmp_path, "fetched.wav")
    calls: list[str] = []

    def _fetch(uri: str) -> Path:
        calls.append(uri)
        return cached

    set_fetcher(_fetch)
    try:
        assert resolve("s3://bucket/voices/neeko.wav") == cached
        assert calls == ["s3://bucket/voices/neeko.wav"]
        resolve("r2://bucket/voices/x.wav")
        assert calls[-1] == "r2://bucket/voices/x.wav"
    finally:
        set_fetcher(None)


def test_s3_fetcher_missing_file_raises_reference_audio_missing():
    resolve, set_fetcher, missing, _unsupported = _resolver()

    def _fetch(_uri: str) -> Path:
        raise FileNotFoundError("no such object")

    set_fetcher(_fetch)
    try:
        with pytest.raises(missing):
            resolve("s3://bucket/voices/missing.wav")
    finally:
        set_fetcher(None)


def test_s3_fetcher_missing_env_raises_unsupported():
    resolve, set_fetcher, _missing, unsupported = _resolver()

    def _fetch(_uri: str) -> Path:
        raise RuntimeError("R2 storage requires NQAI_R2_ACCOUNT_ID + NQAI_R2_BUCKET env")

    set_fetcher(_fetch)
    try:
        with pytest.raises(unsupported, match="NQAI_R2"):
            resolve("s3://bucket/voices/x.wav")
    finally:
        set_fetcher(None)
