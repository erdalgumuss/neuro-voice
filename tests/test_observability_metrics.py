"""Tests for the ``observability`` package — Faz C step 2.

These tests pin down the metric inventory, label schema, and helper
behaviour without instrumenting any production call site. Once parallel
agents land their pieces, the orchestrator will wire ``.inc()`` / ``.observe()``
calls into hot paths; the contracts asserted here protect that wiring.
"""

from __future__ import annotations

import pytest
from prometheus_client import CONTENT_TYPE_LATEST

from observability import (
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

# ---------------------------------------------------------------------------
# Inventory expected from the spec
# ---------------------------------------------------------------------------

EXPECTED_METRICS: dict[str, dict[str, object]] = {
    "nqai_tts_requests_total": {
        "type": "counter",
        "labels": {"tenant", "voice", "status"},
    },
    "nqai_tts_errors_total": {
        "type": "counter",
        "labels": {"type"},
    },
    "nqai_tts_deprecated_endpoint_total": {
        "type": "counter",
        "labels": {"endpoint"},
    },
    "nqai_worker_dlq_total": {
        "type": "counter",
        "labels": set(),
    },
    "nqai_tts_queue_wait_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_worker_pickup_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_reference_resolve_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_first_pcm_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_first_audio_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_gateway_first_byte_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_worker_cold_load_seconds": {
        "type": "histogram",
        "labels": {"voice"},
    },
    "nqai_tts_inference_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_total_seconds": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_output_rms_normalized": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_output_silence_ratio": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_output_clipping_ratio": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_tts_output_seconds_per_char": {
        "type": "histogram",
        "labels": {"tenant", "voice"},
    },
    "nqai_worker_capacity_total": {
        "type": "gauge",
        "labels": set(),
    },
    "nqai_worker_inflight_total": {
        "type": "gauge",
        "labels": set(),
    },
    "nqai_worker_count": {
        "type": "gauge",
        "labels": set(),
    },
    "nqai_queue_depth": {
        "type": "gauge",
        "labels": {"stream"},
    },
}

FORBIDDEN_LABELS: frozenset[str] = frozenset({"request_id", "id", "session_id", "job_id"})


def _collect() -> dict[str, object]:
    """Return ``{metric_name: metric_family}`` from the dedicated registry.

    prometheus_client strips ``_total`` from a counter family's name (the
    suffix lives on the sample, not the family). To let EXPECTED_METRICS
    document the full sample name (which matches what shows up in
    ``/metrics`` output), we register counter families under both keys.
    """

    out: dict[str, object] = {}
    for family in REGISTRY.collect():
        # Counters: re-key under the documented `_total` sample form so
        # EXPECTED_METRICS can use the name that actually shows up in
        # `/metrics` output (matching prometheus exposition conventions).
        if family.type == "counter" and not family.name.endswith("_total"):
            out[f"{family.name}_total"] = family
        else:
            out[family.name] = family
    return out


# ---------------------------------------------------------------------------
# Inventory + label schema
# ---------------------------------------------------------------------------


def test_all_expected_metrics_are_registered() -> None:
    families = _collect()
    for name in EXPECTED_METRICS:
        assert name in families, f"missing metric in REGISTRY: {name}"


def test_no_unexpected_metrics_in_registry() -> None:
    families = _collect()
    extra = set(families) - set(EXPECTED_METRICS)
    assert not extra, f"registry contains unexpected metrics: {sorted(extra)}"


@pytest.mark.parametrize("name,spec", sorted(EXPECTED_METRICS.items()))
def test_metric_type_and_labels_match_spec(name: str, spec: dict[str, object]) -> None:
    families = _collect()
    family = families[name]
    assert family.type == spec["type"], (
        f"{name}: expected type {spec['type']}, got {family.type}"
    )

    # Every sample on the family carries the same label keys; pick the first
    # sample we can find. Counters/Histograms register a child eagerly only
    # if they have zero label names — for labelled metrics there may be no
    # children yet, so we fall back to the family's declared label names via
    # the private ``_labelnames`` attribute on the original collector.
    samples = list(family.samples)
    label_keys = (
        set(samples[0].labels.keys()) - {"le", "quantile"} if samples else set()
    )

    expected_labels = set(spec["labels"])  # type: ignore[arg-type]
    # If the metric is labelled but has no observations yet, ``samples`` for a
    # counter/gauge is empty; for histograms there's always a ``_created``
    # sample even before observations. We therefore prefer reading
    # ``_labelnames`` directly when present.
    declared = getattr(_lookup_collector(name), "_labelnames", None)
    if declared is not None:
        label_keys = set(declared)

    assert label_keys == expected_labels, (
        f"{name}: label mismatch — expected {sorted(expected_labels)}, got {sorted(label_keys)}"
    )


def _lookup_collector(metric_name: str):
    """Resolve a metric family back to its source collector object."""

    # prometheus_client stores registered collectors in ``_names_to_collectors``.
    # The mapping uses the *full sample name* (e.g. ``nqai_tts_requests_total``)
    # which matches ``family.name`` for our metrics.
    mapping = getattr(REGISTRY, "_names_to_collectors", {})
    return mapping.get(metric_name) or mapping.get(f"{metric_name}_total")


# ---------------------------------------------------------------------------
# Cardinality discipline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_METRICS))
def test_no_unbounded_labels_anywhere(name: str) -> None:
    collector = _lookup_collector(name)
    declared = set(getattr(collector, "_labelnames", ()) or ())
    leaked = declared & FORBIDDEN_LABELS
    assert not leaked, (
        f"{name}: forbidden high-cardinality label(s) detected: {sorted(leaked)}"
    )


# ---------------------------------------------------------------------------
# render_metrics()
# ---------------------------------------------------------------------------


def test_render_metrics_returns_bytes_and_text_plain_content_type() -> None:
    body, content_type = render_metrics()
    assert isinstance(body, bytes)
    assert content_type.startswith("text/plain"), content_type
    # CONTENT_TYPE_LATEST drives the assertion above; sanity-check the constant.
    assert CONTENT_TYPE_LATEST.startswith("text/plain")
    # Body should at least contain one of our metric HELP lines.
    assert b"nqai_tts_requests_total" in body


# ---------------------------------------------------------------------------
# record_waterfall()
# ---------------------------------------------------------------------------


def _count_value(histogram, *, tenant: str, voice: str) -> float:
    child = histogram.labels(tenant=tenant, voice=voice)
    # prometheus_client.Histogram stores per-bucket counts as NON-cumulative
    # internally (cumulation happens only at render time). Total observations
    # = sum across all bucket counters, which also equals child._count if
    # available — but older versions don't expose _count.
    return sum(b.get() for b in child._buckets)


def test_record_waterfall_observes_all_eight_histograms_when_fully_populated() -> None:
    tenant = "test-tenant-full"
    voice = "test-voice-full"

    record_waterfall(
        tenant=tenant,
        voice=voice,
        queue_wait_ms=120,
        worker_pickup_ms=15,
        reference_resolve_ms=80,
        first_pcm_ms=240,
        first_audio_ms=310,
        gateway_first_byte_ms=370,
        inference_ms=1_500,
        total_ms=1_900,
    )

    for histogram in (
        TTS_QUEUE_WAIT_SECONDS,
        TTS_WORKER_PICKUP_SECONDS,
        TTS_REFERENCE_RESOLVE_SECONDS,
        TTS_FIRST_PCM_SECONDS,
        TTS_FIRST_AUDIO_SECONDS,
        TTS_GATEWAY_FIRST_BYTE_SECONDS,
        TTS_INFERENCE_SECONDS,
        TTS_TOTAL_SECONDS,
    ):
        assert _count_value(histogram, tenant=tenant, voice=voice) >= 1.0


def test_record_waterfall_skips_none_values_silently() -> None:
    tenant = "test-tenant-partial"
    voice = "test-voice-partial"

    record_waterfall(
        tenant=tenant,
        voice=voice,
        queue_wait_ms=50,
        first_audio_ms=200,
        # All other stages intentionally left as None.
    )

    # Recorded ones bump.
    assert _count_value(TTS_QUEUE_WAIT_SECONDS, tenant=tenant, voice=voice) >= 1.0
    assert _count_value(TTS_FIRST_AUDIO_SECONDS, tenant=tenant, voice=voice) >= 1.0

    # Skipped ones must NOT have produced a child for this (tenant, voice).
    for histogram in (
        TTS_WORKER_PICKUP_SECONDS,
        TTS_REFERENCE_RESOLVE_SECONDS,
        TTS_FIRST_PCM_SECONDS,
        TTS_GATEWAY_FIRST_BYTE_SECONDS,
        TTS_INFERENCE_SECONDS,
        TTS_TOTAL_SECONDS,
    ):
        # ``_metrics`` is the private label-tuple -> child mapping on a
        # MetricWrapperBase. Absence proves the helper didn't lazily create
        # a child for the skipped stage.
        assert (tenant, voice) not in histogram._metrics, (
            f"{histogram._name}: child created for skipped None value"
        )


def test_record_waterfall_with_all_none_is_noop() -> None:
    # Should not raise and should not mint any new label children.
    tenant = "test-tenant-noop"
    voice = "test-voice-noop"
    record_waterfall(tenant=tenant, voice=voice)
    for histogram in (
        TTS_QUEUE_WAIT_SECONDS,
        TTS_WORKER_PICKUP_SECONDS,
        TTS_REFERENCE_RESOLVE_SECONDS,
        TTS_FIRST_PCM_SECONDS,
        TTS_FIRST_AUDIO_SECONDS,
        TTS_GATEWAY_FIRST_BYTE_SECONDS,
        TTS_INFERENCE_SECONDS,
        TTS_TOTAL_SECONDS,
    ):
        assert (tenant, voice) not in histogram._metrics


# ---------------------------------------------------------------------------
# Counter usability
# ---------------------------------------------------------------------------


def test_tts_requests_counter_increments_with_full_label_set() -> None:
    child = TTS_REQUESTS.labels(
        tenant="acme",
        voice="neeko-v01",
        status="success",
    )
    before = child._value.get()
    child.inc()
    after = child._value.get()
    assert after == before + 1.0


def test_tts_requests_counter_rejects_app_label() -> None:
    """app_label is a user-controlled header (X-NQAI-App) and was
    explicitly removed from TTS_REQUESTS to keep Prometheus cardinality
    bounded. Per-app breakdowns live in usage_records.app_label
    (Postgres). Reinstating the label would be a regression — pin it."""
    with pytest.raises(ValueError):
        TTS_REQUESTS.labels(
            tenant="acme",
            voice="neeko-v01",
            app_label="neeko",
            status="success",
        )


def test_tts_errors_counter_supports_documented_type_enum() -> None:
    for err_type in ("poison", "transient", "unknown", "dlq"):
        TTS_ERRORS.labels(type=err_type).inc()
    # Sanity: at least one sample per type ended up in the family.
    families = _collect()
    samples = [s for s in families["nqai_tts_errors_total"].samples if s.name.endswith("_total")]
    seen_types = {s.labels["type"] for s in samples}
    assert {"poison", "transient", "unknown", "dlq"} <= seen_types


def test_worker_dlq_counter_is_label_free() -> None:
    before = WORKER_DLQ._value.get()
    WORKER_DLQ.inc()
    assert WORKER_DLQ._value.get() == before + 1.0


# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------


def test_worker_gauges_accept_set_calls() -> None:
    WORKER_CAPACITY.set(42)
    WORKER_INFLIGHT.set(7)
    WORKER_COUNT.set(3)
    assert WORKER_CAPACITY._value.get() == 42
    assert WORKER_INFLIGHT._value.get() == 7
    assert WORKER_COUNT._value.get() == 3


def test_queue_depth_gauge_supports_stream_enum() -> None:
    QUEUE_DEPTH.labels(stream="jobs").set(11)
    QUEUE_DEPTH.labels(stream="dlq").set(0)
    assert QUEUE_DEPTH.labels(stream="jobs")._value.get() == 11
    assert QUEUE_DEPTH.labels(stream="dlq")._value.get() == 0


# ---------------------------------------------------------------------------
# Bucket configuration
# ---------------------------------------------------------------------------


def test_waterfall_buckets_are_monotonic_and_cover_50ms_to_30s() -> None:
    assert tuple(sorted(WATERFALL_BUCKETS)) == WATERFALL_BUCKETS
    assert WATERFALL_BUCKETS[0] <= 0.05
    assert WATERFALL_BUCKETS[-1] >= 30.0
