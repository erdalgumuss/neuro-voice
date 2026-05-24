"""Reference audio URI → local Path resolver.

Voice manifests in the DB carry `reference_uri` as one of:

    file:///abs/path/to/audio.wav    explicit local file
    s3://bucket/key/audio.wav        R2/S3 (resolved by R2 helper in Faz B)
    /abs/path or rel/path            bare path (legacy filesystem)

The synth engine expects a `Path` it can hand to `model.generate(
reference_wav_path=...)`. This module is the single place that resolves
remote URIs to local files; the rest of the codebase consumes Path only.

In Faz A.6 we land file:// + bare-path support. The s3:// branch raises
NotImplementedError; the upcoming R2 helper (B audit step 3) plugs in
its `download_to_cache(uri) -> Path` here.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse


class UnsupportedReferenceURI(ValueError):
    """Raised when the reference_uri scheme has no resolver wired in."""


class ReferenceAudioMissing(FileNotFoundError):
    """Raised when the resolved local path does not exist on disk."""


def resolve_reference_uri(uri: str) -> Path:
    """Return a local `Path` for the given reference URI.

    Faz A.6 scope: file:// and bare paths. Faz B will inject an R2 fetcher
    for s3:// via dependency override; until then s3:// raises.
    """
    if not uri:
        raise UnsupportedReferenceURI("empty reference_uri")

    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()

    if scheme in ("", "file"):
        # `file:///abs/path` → path="/abs/path"; bare "rel/path" → path="rel/path"
        local = Path(unquote(parsed.path) if scheme == "file" else uri).expanduser()
        if not local.is_absolute():
            local = local.resolve()
        if not local.is_file():
            raise ReferenceAudioMissing(f"reference audio not on disk: {local}")
        return local

    if scheme in ("s3", "r2"):
        raise UnsupportedReferenceURI(
            f"remote reference URI '{uri}' — R2 fetcher not wired yet (Faz B step 3)"
        )

    raise UnsupportedReferenceURI(f"unknown URI scheme '{scheme}' in {uri!r}")
