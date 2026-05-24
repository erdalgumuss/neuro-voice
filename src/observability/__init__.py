"""NQAI Voice observability package.

Public surface re-exported from :mod:`observability.metrics`. Import sites
should target the package root, not the submodule, so we can later swap the
backing implementation (e.g. OpenTelemetry bridge) without churning call
sites.
"""

from __future__ import annotations

from observability.metrics import (
    CONTENT_TYPE_LATEST,
    QUEUE_DEPTH,
    REGISTRY,
    TTS_ERRORS,
    TTS_FIRST_AUDIO_SECONDS,
    TTS_FIRST_PCM_SECONDS,
    TTS_GATEWAY_FIRST_BYTE_SECONDS,
    TTS_INFERENCE_SECONDS,
    TTS_QUEUE_WAIT_SECONDS,
    TTS_REFERENCE_RESOLVE_SECONDS,
    TTS_REQUESTS,
    TTS_TOTAL_SECONDS,
    TTS_WORKER_PICKUP_SECONDS,
    WATERFALL_BUCKETS,
    WORKER_CAPACITY,
    WORKER_COUNT,
    WORKER_DLQ,
    WORKER_INFLIGHT,
    record_waterfall,
    render_metrics,
)

__all__ = [
    "CONTENT_TYPE_LATEST",
    "QUEUE_DEPTH",
    "REGISTRY",
    "TTS_ERRORS",
    "TTS_FIRST_AUDIO_SECONDS",
    "TTS_FIRST_PCM_SECONDS",
    "TTS_GATEWAY_FIRST_BYTE_SECONDS",
    "TTS_INFERENCE_SECONDS",
    "TTS_QUEUE_WAIT_SECONDS",
    "TTS_REFERENCE_RESOLVE_SECONDS",
    "TTS_REQUESTS",
    "TTS_TOTAL_SECONDS",
    "TTS_WORKER_PICKUP_SECONDS",
    "WATERFALL_BUCKETS",
    "WORKER_CAPACITY",
    "WORKER_COUNT",
    "WORKER_DLQ",
    "WORKER_INFLIGHT",
    "record_waterfall",
    "render_metrics",
]
