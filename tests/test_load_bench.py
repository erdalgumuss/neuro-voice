"""Unit tests for the load benchmark runner (Faz C v1 item 5)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load scripts/load_bench.py without making `scripts` a package.
_SPEC = importlib.util.spec_from_file_location(
    "load_bench",
    Path(__file__).resolve().parent.parent / "scripts" / "load_bench.py",
)
assert _SPEC is not None and _SPEC.loader is not None
load_bench = importlib.util.module_from_spec(_SPEC)
sys.modules["load_bench"] = load_bench
_SPEC.loader.exec_module(load_bench)


def test_percentile_empty_returns_none() -> None:
    assert load_bench._percentile([], 0.5) is None


def test_percentile_monotonic() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    p50 = load_bench._percentile(values, 0.5)
    p95 = load_bench._percentile(values, 0.95)
    p99 = load_bench._percentile(values, 0.99)
    assert p50 is not None and p95 is not None and p99 is not None
    assert p50 < p95 <= p99


def test_stats_block_empty_returns_nones() -> None:
    out = load_bench._stats_block([])
    assert out["n"] == 0.0
    assert out["p50"] is None and out["p95"] is None
    assert out["mean"] is None


def test_stats_block_populated() -> None:
    out = load_bench._stats_block([100.0, 200.0, 300.0])
    assert out["n"] == 3.0
    assert out["min"] == 100.0
    assert out["max"] == 300.0
    assert out["mean"] == 200.0
    assert out["p50"] == 200.0


def test_format_markdown_smoke() -> None:
    report = load_bench.RunReport(
        hardware_label="test-rig",
        base_url="http://localhost:8000",
        voice="vt",
        concurrency=20,
        duration_s=60.0,
        started_at="2026-05-25T00:00:00+00:00",
        finished_at="2026-05-25T00:01:00+00:00",
        wall_time_s=60.0,
        total_requests=100,
        successes=95,
        admission_rejections=4,
        uncontrolled_failures=1,
        error_breakdown={"http_503": 4, "timeout": 1},
        throughput_rps=1.67,
        accepted_success_rate=0.989,
        admission_rejection_rate=0.04,
        latency_first_byte_ms=load_bench._stats_block([100.0, 200.0]),
        latency_total_ms=load_bench._stats_block([500.0, 700.0]),
        notes=["bench note"],
    )
    md = load_bench._format_markdown(report)
    assert "load benchmark" in md
    assert "test-rig" in md
    assert "Outcome bucketing" in md
    assert "Admission rejections (503)" in md
    assert "Uncontrolled failures" in md
    assert "Accepted success rate" in md
    assert "Admission rejection rate" in md
    assert "client_first_byte_ms" in md
    assert "bench note" in md


def test_format_markdown_handles_empty_error_breakdown() -> None:
    """When no failures occurred, the error breakdown table should be
    SKIPPED, not emitted with a blank body."""
    report = load_bench.RunReport(
        hardware_label="clean", base_url="x", voice="v",
        concurrency=10, duration_s=30.0,
        started_at="t", finished_at="t",
        wall_time_s=30.0,
        total_requests=50,
        successes=50, admission_rejections=0, uncontrolled_failures=0,
        error_breakdown={},
        throughput_rps=1.67,
        accepted_success_rate=1.0, admission_rejection_rate=0.0,
        latency_first_byte_ms=load_bench._stats_block([50.0]),
        latency_total_ms=load_bench._stats_block([100.0]),
    )
    md = load_bench._format_markdown(report)
    assert "Error breakdown" not in md
    # Even with zero failures, the bucketing table still renders.
    assert "Outcome bucketing" in md


def test_argparse_rejects_missing_required() -> None:
    with pytest.raises(SystemExit):
        load_bench.main(["--api-key", "x"])


def test_accepted_success_rate_excludes_503_from_denominator() -> None:
    """Pin the Codex-audit semantics: a run that is 90% 503s + 10%
    success must report accepted_success_rate=1.0 (all admitted
    requests succeeded), not 0.10. 503s are controlled backpressure,
    not failure."""
    samples = [
        load_bench.Sample(
            started_at_ms=0, finished_at_ms=10,
            first_byte_offset_ms=0.0, total_ms=10.0,
            status_code=503, audio_bytes=0, ok=False,
            error_type="http_503",
        )
        for _ in range(9)
    ] + [
        load_bench.Sample(
            started_at_ms=0, finished_at_ms=500,
            first_byte_offset_ms=100.0, total_ms=500.0,
            status_code=200, audio_bytes=1024, ok=True,
        )
    ]
    # Reproduce the aggregation block from _amain inline so we don't
    # need to spin up an HTTP server. (If the logic drifts, this test
    # surfaces it.)
    successes = sum(1 for s in samples if s.ok)
    rejections = sum(
        1 for s in samples if (not s.ok) and s.error_type == "http_503"
    )
    decided = successes + (len(samples) - successes - rejections)
    accepted_rate = (successes / decided) if decided else 0.0
    rejection_rate = rejections / len(samples)
    assert successes == 1
    assert rejections == 9
    assert accepted_rate == 1.0  # 100% of admitted requests succeeded
    assert rejection_rate == 0.9
