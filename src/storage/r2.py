"""Cloudflare R2 storage adapter (S3-compatible).

This module is the single touch-point for object storage. Everywhere
else in the codebase consumes `s3://bucket/key` URIs and a local
`Path` returned by `download_to_cache()`; the request path never sees
boto3 directly.

R2 specifics that matter:
  * Endpoint: https://<account_id>.r2.cloudflarestorage.com
  * Region: "auto" (R2 ignores region but boto3 demands one)
  * Signature version: s3v4 (required for presigned URLs)
  * Egress: $0 — so we don't try to be clever about caching outputs
  * Class A op (write) $4.50/M, Class B (read) $0.36/M — both cheap

Test path uses `moto`'s S3 mock; the helper has no R2-specific code
paths besides the constructor arguments.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

try:
    import boto3
    from botocore.client import Config
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover — only the test stub path needs this
    boto3 = None
    Config = None
    ClientError = Exception

logger = logging.getLogger("nqai_voice.storage.r2")

DEFAULT_PRESIGN_TTL = 3600  # 1 hour; tune per call
DEFAULT_CACHE_MAX_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB


# --------------------------------------------------------------------------- #
# URI helpers — s3://bucket/key parser and builder
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class S3URI:
    """`s3://bucket/key` parsed into bucket + key. Both R2 and AWS use this."""

    bucket: str
    key: str

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"

    @classmethod
    def parse(cls, uri: str) -> S3URI:
        if not uri or not isinstance(uri, str):
            raise ValueError("empty URI")
        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        if scheme not in ("s3", "r2"):
            raise ValueError(f"not an s3:// or r2:// URI: {uri!r}")
        if not parsed.netloc:
            raise ValueError(f"URI missing bucket: {uri!r}")
        # urlparse puts the bucket in netloc and "/key/path" in path with leading slash
        key = parsed.path.lstrip("/")
        if not key:
            raise ValueError(f"URI missing object key: {uri!r}")
        return cls(bucket=parsed.netloc, key=key)


# --------------------------------------------------------------------------- #
# R2Storage — thin boto3 wrapper, no business logic
# --------------------------------------------------------------------------- #
class R2Storage:
    """boto3 S3 client targeting R2 (or any S3-compatible backend).

    Construction is explicit so tests can inject a moto-backed S3 client
    without needing a real R2 account.
    """

    def __init__(
        self,
        *,
        default_bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region_name: str = "auto",
        cache_dir: Path | None = None,
        cache_max_bytes: int | None = None,
        s3_client=None,  # for moto-injection in tests
    ) -> None:
        self.default_bucket = default_bucket
        self.cache_dir = cache_dir or Path(
            os.environ.get("NQAI_R2_CACHE_DIR", "/tmp/nqai-r2-cache")
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if cache_max_bytes is None:
            cache_max_bytes = int(
                os.environ.get("NQAI_R2_CACHE_MAX_BYTES", str(DEFAULT_CACHE_MAX_BYTES))
            )
        self.cache_max_bytes = cache_max_bytes
        self._client_lock = threading.Lock()
        # Per-URI download locks — prevents two threads in the same
        # process from racing on the same cache key (audit F2 fix).
        # The defaultdict-of-locks pattern is bounded by the active
        # working set; reference-audio URIs reuse the same Lock object
        # so consecutive `download_to_cache(same_uri)` calls coalesce.
        self._download_locks_guard = threading.Lock()
        self._download_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        if s3_client is not None:
            self._client = s3_client
            return
        if boto3 is None:
            raise RuntimeError(
                "boto3 not installed — `pip install boto3` or inject a test client"
            )
        kwargs = {
            "region_name": region_name,
            "config": Config(signature_version="s3v4"),
        }
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key_id and secret_access_key:
            kwargs["aws_access_key_id"] = access_key_id
            kwargs["aws_secret_access_key"] = secret_access_key
        self._client = boto3.client("s3", **kwargs)

    # ----- writes ---------------------------------------------------------

    def upload_file(
        self,
        local_path: Path,
        key: str,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
    ) -> S3URI:
        """Upload a local file and return its s3:// URI."""
        if not local_path.is_file():
            raise FileNotFoundError(local_path)
        target_bucket = bucket or self.default_bucket
        extra: dict = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.upload_file(
            Filename=str(local_path),
            Bucket=target_bucket,
            Key=key,
            ExtraArgs=extra or None,
        )
        logger.info("r2 upload bucket=%s key=%s size=%d",
                    target_bucket, key, local_path.stat().st_size)
        return S3URI(bucket=target_bucket, key=key)

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
    ) -> S3URI:
        """In-memory variant — handy for small artifacts (manifests, JSON)."""
        target_bucket = bucket or self.default_bucket
        kwargs: dict = {"Bucket": target_bucket, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        self._client.put_object(**kwargs)
        return S3URI(bucket=target_bucket, key=key)

    # ----- reads ----------------------------------------------------------

    def exists(self, uri: str) -> bool:
        parsed = S3URI.parse(uri)
        try:
            self._client.head_object(Bucket=parsed.bucket, Key=parsed.key)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def download_to_cache(self, uri: str) -> Path:
        """Pull an object into local cache. Idempotent + race-safe.

        Concurrency model (audit F2 fix, 2026-05-24):
        ---------------------------------------------
        Two failure modes the original implementation had:
          1. Deterministic `.part` filename → two threads downloading
             the same URI both wrote to `local.with_suffix(".part")`;
             the second `tmp.replace(local)` would atomically swap a
             half-written file in over the first thread's good file,
             then `finally: tmp.unlink(missing_ok=True)` could delete
             the good file (race window between replace and unlink).
          2. No coordination → multiple workers wasted bandwidth
             pulling the same object N times.

        Fix: (a) PID+UUID-suffixed `.part` so no two writers ever share
        a temp path; (b) per-URI threading.Lock so a second caller for
        the same URI blocks on the first download and then short-circuits
        on the cache hit. Cross-process races (multiple worker pods on a
        shared NFS cache) are still possible but rare and recoverable —
        `os.replace` is atomic on POSIX, and the cache key is
        content-deterministic per URI.
        """
        parsed = S3URI.parse(uri)
        # Bucket + key uniquely identifies the cached file; we hash the
        # full URI to avoid filesystem-illegal characters in the key.
        digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:32]
        suffix = Path(parsed.key).suffix or ".bin"
        local = self.cache_dir / f"{digest}{suffix}"

        # Fast path — already cached.
        if local.is_file() and local.stat().st_size > 0:
            return local

        # Coordinate concurrent downloads of the *same* URI.
        with self._download_locks_guard:
            uri_lock = self._download_locks[uri]
        with uri_lock:
            # Re-check after acquiring the lock: another thread may have
            # finished the download while we waited.
            if local.is_file() and local.stat().st_size > 0:
                return local

            # Each writer gets its own .part path so a second concurrent
            # writer (different process, same URI) can't smash ours.
            tmp = local.with_suffix(
                f"{local.suffix}.{os.getpid()}.{uuid.uuid4().hex}.part"
            )
            try:
                self._client.download_file(
                    Bucket=parsed.bucket, Key=parsed.key, Filename=str(tmp)
                )
                # os.replace is atomic on POSIX. If another writer
                # already produced `local`, we lose the race but ours
                # is byte-equivalent — overwriting is safe.
                os.replace(str(tmp), str(local))
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
            self._enforce_cache_limit(protected=local)
        logger.info("r2 download cached bucket=%s key=%s → %s",
                    parsed.bucket, parsed.key, local)
        return local

    def _enforce_cache_limit(self, *, protected: Path) -> None:
        """Evict oldest cached files until total cache size is under cap.

        The file just downloaded is protected even when it alone exceeds
        the limit: deleting it would turn a successful request into a
        cache miss loop. `.part` files are ignored here; download cleanup
        owns them.
        """
        max_bytes = self.cache_max_bytes
        if max_bytes <= 0:
            return

        files: list[tuple[float, Path, int]] = []
        total = 0
        for path in self.cache_dir.iterdir():
            if not path.is_file() or path.name.endswith(".part"):
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            total += stat.st_size
            files.append((stat.st_mtime, path, stat.st_size))

        if total <= max_bytes:
            return

        for _mtime, path, size in sorted(files, key=lambda item: item[0]):
            if path == protected:
                continue
            try:
                path.unlink()
                total -= size
                logger.info("r2 cache evicted %s size=%d", path, size)
            except FileNotFoundError:
                pass
            if total <= max_bytes:
                return

        if total > max_bytes:
            logger.warning(
                "r2 cache remains over limit bytes=%d limit=%d protected=%s",
                total, max_bytes, protected,
            )

    # ----- presigned URLs --------------------------------------------------

    def presigned_get_url(self, uri: str, *, expires_in: int = DEFAULT_PRESIGN_TTL) -> str:
        """Pre-authenticated GET URL the client can fetch directly. The
        client never sees the bucket credentials and the URL stops
        working after `expires_in` seconds (default 1h).
        """
        if expires_in <= 0 or expires_in > 7 * 24 * 3600:
            raise ValueError("expires_in must be in (0, 7 days]")
        parsed = S3URI.parse(uri)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": parsed.bucket, "Key": parsed.key},
            ExpiresIn=expires_in,
        )

    # ----- deletes --------------------------------------------------------

    def delete(self, uri: str) -> None:
        parsed = S3URI.parse(uri)
        self._client.delete_object(Bucket=parsed.bucket, Key=parsed.key)
        logger.info("r2 delete bucket=%s key=%s", parsed.bucket, parsed.key)


# --------------------------------------------------------------------------- #
# Process-wide singleton — built lazily from env
# --------------------------------------------------------------------------- #
_singleton: R2Storage | None = None
_singleton_lock = threading.Lock()


def get_r2_storage() -> R2Storage:
    """Build an R2Storage from env. Tests inject via the constructor or
    via FastAPI dependency_overrides — never via this singleton."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            account_id = os.environ.get("NQAI_R2_ACCOUNT_ID")
            bucket = os.environ.get("NQAI_R2_BUCKET")
            if not (account_id and bucket):
                raise RuntimeError(
                    "R2 storage requires NQAI_R2_ACCOUNT_ID + NQAI_R2_BUCKET env"
                )
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
            _singleton = R2Storage(
                default_bucket=bucket,
                endpoint_url=endpoint,
                access_key_id=os.environ.get("NQAI_R2_ACCESS_KEY_ID"),
                secret_access_key=os.environ.get("NQAI_R2_SECRET_ACCESS_KEY"),
            )
        return _singleton
