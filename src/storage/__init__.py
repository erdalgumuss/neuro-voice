"""Object storage layer.

NQAI Voice's canonical artifact store is Cloudflare R2 (S3-compatible
HTTP API, zero egress fee). The URI scheme stays `s3://` everywhere
because that's the cross-vendor convention — every S3-compatible
service (R2, B2, AWS S3, MinIO, Wasabi) consumes the same path and
boto3 doesn't care which one you're talking to as long as the
endpoint_url is set correctly.

wires this into:
  • reference_resolver.py's s3:// branch  → R2Storage.download_to_cache
  • main.py voice enroll                  → R2Storage.upload_reference
  • async job output                      → R2Storage.upload_output
                                              + presigned download URL
"""

from .r2 import R2Storage, get_r2_storage
from .reference_resolver import (
    ReferenceAudioMissing,
    UnsupportedReferenceURI,
    resolve_reference_uri,
    set_remote_fetcher,
)

__all__ = [
    "R2Storage",
    "get_r2_storage",
    "ReferenceAudioMissing",
    "UnsupportedReferenceURI",
    "resolve_reference_uri",
    "set_remote_fetcher",
]
