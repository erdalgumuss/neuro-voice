"""Reference audio URI → local Path resolver.

Voice manifests in the DB carry `reference_uri` as one of:

    file:///abs/path/to/audio.wav    explicit local file
    s3://bucket/key/audio.wav        R2/S3 — fetched via storage.r2.R2Storage
    r2://bucket/key/audio.wav        synonym for s3:// (some tooling emits it)
    /abs/path or rel/path            bare path (legacy filesystem)

The synth engine expects a `Path` it can hand to `model.generate(
reference_wav_path=...)`. This module is the single place that resolves
remote URIs to local files; the rest of the codebase consumes Path only.

The s3:// branch reaches into the R2 storage adapter to download +
cache. We import lazily so this module remains importable even when
boto3 is not installed (e.g. on minimal CI runners).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse


class UnsupportedReferenceURI(ValueError):
    """Raised when the reference_uri scheme has no resolver wired in."""


class ReferenceAudioMissing(FileNotFoundError):
    """Raised when the resolved local path does not exist on disk."""


# Hook for tests to inject a fake R2 fetcher. Production wiring (via
# storage.get_r2_storage().download_to_cache) is lazy in resolve_remote().
_remote_fetcher: Callable[[str], Path] | None = None


def set_remote_fetcher(fn: Callable[[str], Path] | None) -> None:
    """Override the s3:// fetcher. Pass None to revert to the default."""
    global _remote_fetcher
    _remote_fetcher = fn


def _default_remote_fetcher(uri: str) -> Path:
    """Lazy import so reference_resolver stays loadable without boto3."""
    from storage import get_r2_storage

    return get_r2_storage().download_to_cache(uri)


def resolve_reference_uri(uri: str) -> Path:
    """Return a local `Path` for the given reference URI.

    - file:// or bare path → returned as-is after existence check
    - s3:// or r2://       → downloaded into the R2 cache, cached path returned
    """
    if not uri:
        raise UnsupportedReferenceURI("empty reference_uri")

    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()

    if scheme in ("", "file"):
        local = Path(unquote(parsed.path) if scheme == "file" else uri).expanduser()
        if not local.is_absolute():
            local = local.resolve()
        if not local.is_file():
            raise ReferenceAudioMissing(f"reference audio not on disk: {local}")
        return local

    if scheme in ("s3", "r2"):
        fetcher = _remote_fetcher or _default_remote_fetcher
        try:
            local = fetcher(uri)
        except FileNotFoundError as e:
            raise ReferenceAudioMissing(str(e)) from e
        except RuntimeError as e:
            # storage.get_r2_storage() raises RuntimeError when env is missing.
            raise UnsupportedReferenceURI(str(e)) from e
        if not local.is_file():
            raise ReferenceAudioMissing(f"R2 cache miss for {uri}: {local}")
        return local

    raise UnsupportedReferenceURI(f"unknown URI scheme '{scheme}' in {uri!r}")
