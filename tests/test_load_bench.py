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
        started_at="2026-05-24T00:00:00+00:00",
        finished_at="2026-05-24T00:01:00+00:00",
        wall_time_s=60.0,
        total_requests=100,
        successes=98,
        failures=2,
        error_breakdown={"http_503": 2},
        throughput_rps=1.67,
        success_rate=0.98,
        latency_first_byte_ms=load_bench._stats_block([100.0, 200.0]),
        latency_total_ms=load_bench._stats_block([500.0, 700.0]),
        notes=["bench note"],
    )
    md = load_bench._format_markdown(report)
    assert "load benchmark" in md
    assert "test-rig" in md
    assert "http_503" in md  # error breakdown surfaced
    assert "client_first_byte_ms" in md
    assert "98 / 100" in md  # success rate detail
    assert "bench note" in md


def test_format_markdown_handles_empty_error_breakdown() -> None:
    """When no failures occurred, the error breakdown table should be
    SKIPPED, not emitted with a blank body."""
    report = load_bench.RunReport(
        hardware_label="clean", base_url="x", voice="v",
        concurrency=10, duration_s=30.0,
        started_at="t", finished_at="t",
        wall_time_s=30.0,
        total_requests=50, successes=50, failures=0,
        error_breakdown={},
        throughput_rps=1.67, success_rate=1.0,
        latency_first_byte_ms=load_bench._stats_block([50.0]),
        latency_total_ms=load_bench._stats_block([100.0]),
    )
    md = load_bench._format_markdown(report)
    assert "Error breakdown" not in md


def test_argparse_rejects_missing_required() -> None:
    with pytest.raises(SystemExit):
        load_bench.main(["--api-key", "x"])
