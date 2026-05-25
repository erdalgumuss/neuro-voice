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


# MLOps PR #4 (A.6) — LoRA adapter cache effectiveness. The cold-load
# histogram (WORKER_COLD_LOAD_SECONDS, below) tells us WHEN a load
# happened, not how often it happened vs. cache hits. The counter pair
# here closes that gap so operators can size NQAI_LORA_CACHE_SIZE
# based on cache hit rate rather than a guess.
WORKER_LORA_CACHE_HITS: Counter = Counter(
    "nqai_worker_lora_cache_hits_total",
    "Per-voice LoRA adapter cache hits in _model_for_adapter — request "
    "found the adapter already loaded, no cold-load.",
    labelnames=("voice",),
    registry=REGISTRY,
)
WORKER_LORA_CACHE_MISSES: Counter = Counter(
    "nqai_worker_lora_cache_misses_total",
    "Per-voice LoRA adapter cache misses — request triggered a cold load. "
    "Should match the WORKER_COLD_LOAD_SECONDS observe count.",
    labelnames=("voice",),
    registry=REGISTRY,
)
WORKER_LORA_CACHE_EVICTIONS: Counter = Counter(
    "nqai_worker_lora_cache_evictions_total",
    "LRU evictions from the per-worker LoRA cache (capacity reached). "
    "Frequent evictions indicate NQAI_LORA_CACHE_SIZE is too small.",
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
# MLOps PR #2 — quality observability (output-side audio dimensions)
# ---------------------------------------------------------------------------
# Why these four histograms:
#
# RMS (root-mean-square amplitude) is the cheapest reliable signal of
# "the model produced actual audio". A worker that loops on empty PCM,
# crashes mid-segment, or returns silent floats will surface as
# `nqai_tts_output_rms_normalized` near 0.0 — invisible today.
#
# silence_ratio catches the "audio with periodic dropouts" failure
# mode (a stuck attention head produces partial silence) that RMS
# averages out.
#
# clipping_ratio catches the opposite: a denoiser pass or post-gain
# bug that drives output past ±32767. Clipping > 1 % is audible
# distortion the client will hear before the metric, but the metric
# is the only thing on-call can scroll through after a 2 a.m. page.
#
# duration_per_char_seconds catches "we're producing audio but it's
# the wrong length" — too short = truncation, too long = stuck loop.
# Per-char (not per-segment) so a 20-char request and a 2000-char
# request live in the same histogram.
#
# All four use the same `(tenant, voice)` labels as the latency
# waterfall so a single Grafana row can correlate "latency went up
# AND quality dropped." Cardinality stays bounded by voice catalog
# size + active tenant count (D-15 budget).

# RMS normalised to int16 full-scale (0.0 = silent, 1.0 = clipping).
# Linear-ish buckets in the 0.0–0.3 range because real voice signal
# rarely sits above 0.3 RMS (we peak-normalize references to 0.95);
# above 0.5 is almost certainly distortion territory.
TTS_OUTPUT_RMS: Histogram = Histogram(
    "nqai_tts_output_rms_normalized",
    "Output PCM RMS amplitude (0.0–1.0 of int16 full-scale). Near zero "
    "means the engine produced silence; near one means clipping.",
    labelnames=_WATERFALL_LABELS,
    buckets=(0.0, 0.005, 0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30,
             0.50, 0.75, 1.0),
    registry=REGISTRY,
)

# silence_ratio: fraction of int16 samples whose absolute value is below
# a 1 % full-scale threshold (~328 / 32767). 1.0 = pure silence; mid-
# values indicate periodic dropouts inside otherwise-valid audio.
TTS_OUTPUT_SILENCE_RATIO: Histogram = Histogram(
    "nqai_tts_output_silence_ratio",
    "Fraction of int16 PCM samples below 1 % full-scale (near-silence). "
    "1.0 = silent; mid values = stutters / partial dropouts.",
    labelnames=_WATERFALL_LABELS,
    buckets=(0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 0.90, 0.99, 1.0),
    registry=REGISTRY,
)

# clipping_ratio: fraction of samples saturating at ±32767 (or 95 % of
# it). Anything above ~0.001 is audible distortion.
TTS_OUTPUT_CLIPPING_RATIO: Histogram = Histogram(
    "nqai_tts_output_clipping_ratio",
    "Fraction of int16 PCM samples at >= 95 %% full-scale. Above 0.001 "
    "is audible distortion the client will hear before this metric.",
    labelnames=_WATERFALL_LABELS,
    buckets=(0.0, 1e-5, 1e-4, 1e-3, 5e-3, 1e-2, 5e-2, 0.1, 0.5, 1.0),
    registry=REGISTRY,
)

# Speech-rate sanity check: total audio duration / input character count.
# Turkish steady-state read-rate ~70-80 ms/char in our domain; below 30
# ms/char suggests truncation, above 200 ms/char suggests a stuck loop.
TTS_DURATION_PER_CHAR_SECONDS: Histogram = Histogram(
    "nqai_tts_output_seconds_per_char",
    "Output audio duration divided by input character count. "
    "Sanity bound on truncation (too low) and stuck-loop (too high). "
    "Real-voice steady-state for Turkish in our domain is ~0.07-0.08.",
    labelnames=_WATERFALL_LABELS,
    buckets=(0.0, 0.01, 0.03, 0.05, 0.07, 0.10, 0.15, 0.25, 0.50, 1.0,
             2.0, 5.0),
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

WORKER_MODEL_INFO: Gauge = Gauge(
    "nqai_worker_model_info",
    "Active model + revision per worker. Value is constant 1; labels carry "
    "the variable. Use `count by (revision)` to see how many workers are on "
    "each revision during a rollout. Set to 0 on shutdown for clean counts.",
    labelnames=("worker_id", "model_id", "revision"),
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
    "WORKER_LORA_CACHE_EVICTIONS",
    "WORKER_LORA_CACHE_HITS",
    "WORKER_LORA_CACHE_MISSES",
    "WORKER_MODEL_INFO",
    "record_waterfall",
    "render_metrics",
]
