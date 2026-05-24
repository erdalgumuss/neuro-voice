"""Prometheus metric registry for the NQAI Voice TTS platform.

This module owns a *dedicated* ``CollectorRegistry`` (not the prometheus_client
global default). Reasons:

1.  The global default registry auto-installs process / GC collectors that we
    explicitly opt out of in this round — we want the ``/metrics`` surface to
    be small, hand-curated, and easy to reason about for cardinality.
2.  A dedicated registry keeps unit tests hermetic: importing this module
    twice in the same process (or in pytest's collection phase) does not
    raise ``Duplicated timeseries`` errors against unrelated test fixtures.

All metrics are defined at module import time. Call sites import the bound
metric objects directly (e.g. ``from observability import TTS_REQUESTS``) and
use the standard ``.labels(...).inc()`` / ``.observe()`` / ``.set()`` API.

Cardinality discipline (HARD RULE):
    Labels are limited to bounded slugs / enums. ``request_id`` is FORBIDDEN
    as a label on every metric — it would explode series count and is already
    captured in structured logs and the waterfall persistence layer.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: CollectorRegistry = CollectorRegistry(auto_describe=False)
"""Dedicated registry for NQAI TTS observability.

Do NOT register metrics against the prometheus_client default registry. All
metric constructors below pass ``registry=REGISTRY`` explicitly.
"""

# ---------------------------------------------------------------------------
# Histogram buckets
# ---------------------------------------------------------------------------

# Span ~50 ms .. 30 s with log-ish steps. Covers:
#   * first-PCM / first-audio (50–500 ms target)
#   * inference (200 ms – few s)
#   * queue wait under load (sub-second healthy, multi-second backpressure)
#   * total wall time (single-digit seconds typical, 30 s ceiling for tails)
WATERFALL_BUCKETS: tuple[float, ...] = (
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    30.0,
)


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

TTS_REQUESTS: Counter = Counter(
    "nqai_tts_requests_total",
    "Total TTS requests by tenant, voice and terminal status.",
    labelnames=("tenant", "voice", "status"),
    registry=REGISTRY,
)
"""``status`` enum: ``success`` | ``error`` | ``backpressure`` | ``auth_failed``.

``deprecated`` is NOT a status value — sunset readiness lives on a
dedicated counter (``TTS_DEPRECATED_ENDPOINT_TOTAL`` below) so it
doesn't double-count with ``success`` for the same request.

NOTE: ``app_label`` is intentionally NOT a Prometheus label — it's a
user-controlled header (``X-NQAI-App``) and Prometheus cardinality must
stay bounded. Per-app breakdowns live in ``usage_records.app_label`` in
Postgres, queried via a SQL exporter / Grafana datasource. See audit
2026-05-24."""


TTS_DEPRECATED_ENDPOINT_TOTAL: Counter = Counter(
    "nqai_tts_deprecated_endpoint_total",
    "Hits on a deprecated endpoint (RFC 8594 sunset clients still "
    "calling). Watch `rate(...[5m])` trend toward 0 before the "
    "sunset date — non-zero close to sunset means the migration "
    "comms didn't land.",
    labelnames=("endpoint",),
    registry=REGISTRY,
)
"""``endpoint`` enum is small + bounded: ``/v1/tts`` for now. New
deprecated endpoints add a label value here when they enter sunset."""


TTS_ERRORS: Counter = Counter(
    "nqai_tts_errors_total",
    "TTS error counter classified by failure type.",
    labelnames=("type",),
    registry=REGISTRY,
)
"""``type`` enum: ``poison`` | ``transient`` | ``unknown`` | ``dlq``."""


WORKER_DLQ: Counter = Counter(
    "nqai_worker_dlq_total",
    "Jobs XADDed to the dead-letter queue by the worker.",
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------

_WATERFALL_LABELS = ("tenant", "voice")


TTS_QUEUE_WAIT_SECONDS: Histogram = Histogram(
    "nqai_tts_queue_wait_seconds",
    "Time a TTS job spent waiting in Redis Streams before a worker picked it up.",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)

TTS_WORKER_PICKUP_SECONDS: Histogram = Histogram(
    "nqai_tts_worker_pickup_seconds",
    "Worker-side pickup latency: XREADGROUP read time + payload parse.",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)

TTS_REFERENCE_RESOLVE_SECONDS: Histogram = Histogram(
    "nqai_tts_reference_resolve_seconds",
    "Latency of reference-audio resolution (R2 fetch / file:// open / cache hit).",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)

TTS_FIRST_PCM_SECONDS: Histogram = Histogram(
    "nqai_tts_first_pcm_seconds",
    "Time from worker pickup to the first PCM frame emitted by the engine.",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)

TTS_FIRST_AUDIO_SECONDS: Histogram = Histogram(
    "nqai_tts_first_audio_seconds",
    "Worker-side latency: inference start to first publish_chunk XADD "
    "(when the gateway can first read a byte off the result stream). "
    "Compare with nqai_tts_gateway_first_byte_seconds to isolate "
    "worker vs. gateway/transport contribution to client TTFB.",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)

WORKER_COLD_LOAD_SECONDS: Histogram = Histogram(
    "nqai_worker_cold_load_seconds",
    "Time spent loading a voice's model + LoRA adapter into VRAM "
    "when the per-voice cache missed. Fires ONCE per voice per "
    "worker process at boot (NQAI_WORKER_WARMUP_VOICES eager path) "
    "OR on the first inference for an un-cached voice. Watching p95 "
    "tells operators whether per-voice sticky routing or a bigger "
    "NQAI_LORA_CACHE_SIZE is worth the engineering.",
    labelnames=("voice",),
    buckets=(
        # Cold-load is dominated by VRAM I/O + safetensors mmap; for
        # VoxCPM2 + a per-voice LoRA on L4 we expect 1-5s, A100 0.5-2s.
        # Buckets span dev-stub (sub-second) through worst-case (60 s
        # = adapter-pull-from-cold-R2 scenario).
        0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0,
    ),
    registry=REGISTRY,
)


TTS_GATEWAY_FIRST_BYTE_SECONDS: Histogram = Histogram(
    "nqai_tts_gateway_first_byte_seconds",
    "Client-facing TTFB on /v1/tts/stream: time from gateway receiving "
    "the HTTP request to writing the first audio byte on the "
    "StreamingResponse. Subtract `nqai_tts_first_audio_seconds` to get "
    "the gateway+transport overhead alone.",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)

TTS_INFERENCE_SECONDS: Histogram = Histogram(
    "nqai_tts_inference_seconds",
    "Pure model inference duration (worker-side, first PCM to last PCM).",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)

TTS_TOTAL_SECONDS: Histogram = Histogram(
    "nqai_tts_total_seconds",
    "Worker-side wall time per TTS job (pipeline start to archive + "
    "DB commit done). Does NOT include gateway HTTP framing or "
    "client transport time.",
    labelnames=_WATERFALL_LABELS,
    buckets=WATERFALL_BUCKETS,
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------

WORKER_CAPACITY: Gauge = Gauge(
    "nqai_worker_capacity_total",
    "Sum of declared per-worker capacity across all healthy workers.",
    registry=REGISTRY,
)

WORKER_INFLIGHT: Gauge = Gauge(
    "nqai_worker_inflight_total",
    "Sum of in-flight TTS jobs across all healthy workers.",
    registry=REGISTRY,
)

WORKER_COUNT: Gauge = Gauge(
    "nqai_worker_count",
    "Number of workers currently considered healthy (heartbeat fresh).",
    registry=REGISTRY,
)

QUEUE_DEPTH: Gauge = Gauge(
    "nqai_queue_depth",
    "Snapshot of XLEN per Redis Streams stream the gateway/worker manages.",
    labelnames=("stream",),
    registry=REGISTRY,
)
"""``stream`` enum: ``jobs`` | ``dlq``."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def render_metrics() -> tuple[bytes, str]:
    """Render the registry into a ``(body, content_type)`` pair.

    The gateway's eventual ``GET /metrics`` handler should return this tuple
    directly. Using the dedicated ``REGISTRY`` means the body contains only
    the metrics defined in this module — no prometheus_client process /
    GC collectors leak in.
    """

    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def record_waterfall(
    *,
    tenant: str,
    voice: str,
    queue_wait_ms: int | None = None,
    worker_pickup_ms: int | None = None,
    reference_resolve_ms: int | None = None,
    first_pcm_ms: int | None = None,
    first_audio_ms: int | None = None,
    gateway_first_byte_ms: int | None = None,
    inference_ms: int | None = None,
    total_ms: int | None = None,
) -> None:
    """Observe every populated waterfall stage in a single call.

    Each ``*_ms`` argument is converted to seconds before being passed to the
    matching histogram. ``None`` values are skipped silently — callers can
    pass only the stages they measured for a given request.

    Cardinality: only ``tenant`` and ``voice`` are used as labels, both
    bounded slugs in the voice catalog. ``request_id`` is intentionally NOT
    a label (see module docstring).
    """

    pairs: tuple[tuple[Histogram, int | None], ...] = (
        (TTS_QUEUE_WAIT_SECONDS, queue_wait_ms),
        (TTS_WORKER_PICKUP_SECONDS, worker_pickup_ms),
        (TTS_REFERENCE_RESOLVE_SECONDS, reference_resolve_ms),
        (TTS_FIRST_PCM_SECONDS, first_pcm_ms),
        (TTS_FIRST_AUDIO_SECONDS, first_audio_ms),
        (TTS_GATEWAY_FIRST_BYTE_SECONDS, gateway_first_byte_ms),
        (TTS_INFERENCE_SECONDS, inference_ms),
        (TTS_TOTAL_SECONDS, total_ms),
    )

    for histogram, value_ms in pairs:
        if value_ms is None:
            continue
        histogram.labels(tenant=tenant, voice=voice).observe(value_ms / 1000.0)


__all__ = [
    "CONTENT_TYPE_LATEST",
    "QUEUE_DEPTH",
    "REGISTRY",
    "TTS_DEPRECATED_ENDPOINT_TOTAL",
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
    "WORKER_COLD_LOAD_SECONDS",
    "WORKER_COUNT",
    "WORKER_DLQ",
    "WORKER_INFLIGHT",
    "record_waterfall",
    "render_metrics",
]
