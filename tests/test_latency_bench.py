"""Unit tests for the latency benchmark runner.

The point isn't to validate latency numbers — it's to keep the script
parseable and the percentile / report-formatting helpers honest, so
a refactor doesn't silently break the artifact ops will paste into
the closure doc.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load scripts/latency_bench.py without making `scripts` a package.
# Must register in sys.modules BEFORE exec_module so @dataclass can
# resolve forward refs via cls.__module__ lookup.
_SPEC = importlib.util.spec_from_file_location(
    "latency_bench",
    Path(__file__).resolve().parent.parent / "scripts" / "latency_bench.py",
)
assert _SPEC is not None and _SPEC.loader is not None
latency_bench = importlib.util.module_from_spec(_SPEC)
sys.modules["latency_bench"] = latency_bench
_SPEC.loader.exec_module(latency_bench)


def test_percentile_empty_returns_none() -> None:
    assert latency_bench._percentile([], 0.5) is None


def test_percentile_single_returns_value() -> None:
    assert latency_bench._percentile([42.0], 0.95) == 42.0


def test_percentile_monotonic() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    p50 = latency_bench._percentile(values, 0.5)
    p95 = latency_bench._percentile(values, 0.95)
    p99 = latency_bench._percentile(values, 0.99)
    assert p50 is not None and p95 is not None and p99 is not None
    assert p50 < p95 <= p99
    assert p50 == 30.0  # median of 5 sorted values


def test_percentile_handles_unsorted_input() -> None:
    values = [50.0, 10.0, 30.0, 20.0, 40.0]
    assert latency_bench._percentile(values, 0.5) == 30.0


def test_compute_percentiles_skips_none_waterfall_fields() -> None:
    """If --db-url wasn't supplied, waterfall fields stay None on the
    sample. The percentile compute must NOT crash on None — it just
    reports n=0 for those fields."""
    samples = [
        latency_bench.CallSample(
            request_id="r1", voice="v", text_preview="t",
            client_first_byte_ms=100.0, client_total_ms=500.0,
            audio_bytes=1024, status_code=200, ok=True,
        ),
        latency_bench.CallSample(
            request_id="r2", voice="v", text_preview="t",
            client_first_byte_ms=120.0, client_total_ms=600.0,
            audio_bytes=2048, status_code=200, ok=True,
        ),
    ]
    out = latency_bench._compute_percentiles(samples)
    assert out["client_first_byte_ms"]["n"] == 2.0
    assert out["client_first_byte_ms"]["p50"] is not None
    # No DB join → these stay n=0.
    assert out["queue_wait_ms"]["n"] == 0.0
    assert out["first_audio_ms"]["p50"] is None


def test_compute_percentiles_excludes_failed_samples() -> None:
    """Failed (ok=False) samples must not pollute the percentile pool —
    error latency is not a server latency signal."""
    samples = [
        latency_bench.CallSample(
            request_id="ok", voice="v", text_preview="t",
            client_first_byte_ms=100.0, client_total_ms=500.0,
            audio_bytes=1024, status_code=200, ok=True,
        ),
        latency_bench.CallSample(
            request_id="fail", voice="v", text_preview="t",
            client_first_byte_ms=0.0, client_total_ms=50.0,
            audio_bytes=0, status_code=503, ok=False, error="backpressure",
        ),
    ]
    out = latency_bench._compute_percentiles(samples)
    assert out["client_first_byte_ms"]["n"] == 1.0
    assert out["client_first_byte_ms"]["p50"] == 100.0


def test_format_markdown_smoke() -> None:
    summary = latency_bench.RunSummary(
        hardware_label="test-rig",
        base_url="http://localhost",
        voice="vt",
        requests=2,
        concurrency=1,
        started_at="2026-05-24T00:00:00+00:00",
        finished_at="2026-05-24T00:00:05+00:00",
        duration_s=5.0,
        success_rate=1.0,
        samples=[],
        percentiles={
            f: {"n": 2.0, "p50": 100.0, "p90": 110.0, "p95": 115.0,
                 "p99": 119.0, "min": 100.0, "max": 120.0, "mean": 110.0}
            for f in latency_bench._WATERFALL_FIELDS
        },
        notes=["test note"],
    )
    md = latency_bench._format_markdown(summary)
    assert "# NQAI Voice latency benchmark — test-rig" in md
    assert "client_first_byte_ms" in md
    assert "p50" in md and "p95" in md
    assert "test note" in md


def test_argparse_rejects_missing_required() -> None:
    """A typo'd invocation should fail fast, not hang on a server."""
    with pytest.raises(SystemExit):
        latency_bench.main(["--api-key", "x"])
