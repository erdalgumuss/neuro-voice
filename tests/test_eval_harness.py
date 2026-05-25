"""MLOps PR #3 — eval harness scaffold tests.

What we pin here:

  1. Test-set parser lifts the 10 sentences out of `v0.1-mini.md`
     in the right order. Drift in the markdown shape would silently
     produce empty runs without this.
  2. Metric protocol + registry round-trips. Registering a stub and
     resolving it by name is the wiring every backend depends on.
  3. System protocol + cache: registering a stub system, calling
     `_synth_one` twice, the second call hits the cache (no second
     synth() invocation). This is the line of code that prevents
     re-billing on retries.
  4. End-to-end runner: stub system + stub metric + sample test set →
     `REPORT.md` exists with the comparison table + raw.jsonl has one
     row per (system × voice × sentence × metric).

What we deliberately do NOT test:
  * Real Whisper / UTMOSv2 backends (require GPU + model downloads).
  * Real ElevenLabs API (requires credit budget).
  These integration tests belong in a separate suite gated on env
  flags; the unit suite must stay 90-second runnable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from eval.dataset import list_test_sets, load_test_set
from eval.metrics import (
    Metric,
    MetricResult,
    get_metric,
    list_metrics,
    register_metric,
)
from eval.runner import RunPlan, _cache_key, _synth_one, run_plan
from eval.systems import (
    SystemMetadata,
    SystemOutput,
    get_system,
    list_systems,
    register_system,
)


# --------------------------------------------------------------------------- #
# Test-set parser
# --------------------------------------------------------------------------- #
def test_v0_1_mini_loads_ten_sentences():
    assert "v0.1-mini" in list_test_sets()
    ts = load_test_set("v0.1-mini")
    assert ts.slug == "v0.1-mini"
    assert len(ts.sentences) == 10, (
        f"v0.1-mini test set is supposed to be 10 sentences; got "
        f"{len(ts.sentences)}. Markdown table shape drift?"
    )
    # Spot-check the first row.
    first = ts.sentences[0]
    assert first.index == 1
    assert first.category == "Oyun"
    assert "hayvanı" in first.text


def test_unknown_test_set_slug_raises():
    """Refuse to silently fall back to a default — every benchmark
    run must record an explicit test_set slug."""
    with pytest.raises(KeyError, match="not registered"):
        load_test_set("does-not-exist")


# --------------------------------------------------------------------------- #
# Metric protocol + registry
# --------------------------------------------------------------------------- #
@dataclass
class _StubMetric:
    name: str = "stub_metric"
    fixed_score: float = 0.42

    def score(
        self, pcm_int16: bytes, sample_rate: int, *, reference_text: str,
    ) -> MetricResult:
        return MetricResult(
            metric_name=self.name,
            score=self.fixed_score,
            direction="lower",
            detail={"len_pcm": len(pcm_int16), "ref_len": len(reference_text)},
        )


def test_metric_registry_round_trip():
    m = _StubMetric(name="stub_round_trip")
    register_metric("test_round_trip", m)
    assert "test_round_trip" in list_metrics()
    assert get_metric("test_round_trip") is m
    out = get_metric("test_round_trip").score(
        b"\x00" * 32, 48000, reference_text="merhaba",
    )
    # The METRIC chooses its own `name` (so reports reflect what was
    # actually computed); the REGISTRY key is just an alias. They can
    # diverge — verified here so future refactors don't accidentally
    # couple them.
    assert out.metric_name == "stub_round_trip"
    assert out.score == pytest.approx(0.42)
    assert out.direction == "lower"


def test_metric_protocol_accepts_arbitrary_dataclass():
    """Protocol — duck typing — any class with `name` + `.score(...)`
    qualifies. Keeps third-party backends from having to inherit
    from anything internal."""
    class _OtherMetric:
        name = "external"

        def score(self, pcm_int16, sample_rate, *, reference_text):
            return MetricResult("external", 1.0, "higher")

    m: Metric = _OtherMetric()  # type-check passes via protocol
    register_metric("external", m)
    assert get_metric("external") is m


# --------------------------------------------------------------------------- #
# System adapter + cache
# --------------------------------------------------------------------------- #
@dataclass
class _StubSystem:
    name: str = "stub_system"
    sample_rate: int = 48000
    call_count: int = 0

    def synthesize(self, *, text: str, voice_id: str) -> SystemOutput:
        self.call_count += 1
        return SystemOutput(
            pcm_int16=b"\x00\x02" * 256,  # tiny non-silent PCM
            sample_rate=self.sample_rate,
            metadata=SystemMetadata(
                system=self.name,
                model_id="stub-model",
                voice_id=voice_id,
                elapsed_ms=42,
            ),
        )


def test_synth_cache_skips_second_call(tmp_path: Path):
    """The whole point of the cache: re-running an eval after a
    crash MUST NOT re-bill vendor calls. Two `_synth_one` invocations
    with the same args → second one returns the cached audio without
    invoking system.synthesize."""
    sys = _StubSystem()
    ts = load_test_set("v0.1-mini")
    sentence = ts.sentences[0]

    out1 = _synth_one(sys, sentence, "test-voice", tmp_path / "cache")
    out2 = _synth_one(sys, sentence, "test-voice", tmp_path / "cache")

    assert sys.call_count == 1, (
        f"expected 1 synth call, got {sys.call_count}. "
        "Cache miss on second call would cause vendor re-bill."
    )
    assert out1.pcm_int16 == out2.pcm_int16
    assert out2.metadata.model_id == "stub-model"
    # The cache is keyed by (system, voice, text), NOT model_id —
    # so changing the system's reported model_id between runs would
    # still hit the cache. Operators that need a fresh fetch
    # explicitly delete the cache_dir.


def test_cache_key_distinguishes_text_voice_system():
    """A collision here would silently return audio for the wrong
    sentence — catastrophic, so pin it."""
    a = _cache_key("nqai", "alice", "hd", "merhaba")
    b = _cache_key("nqai", "bob", "hd", "merhaba")
    c = _cache_key("nqai", "alice", "hd", "selam")
    d = _cache_key("elevenlabs", "alice", "hd", "merhaba")
    assert len({a, b, c, d}) == 4


def test_system_registry_round_trip():
    s = _StubSystem(name="stub_sys")
    register_system("stub_sys", s)
    assert "stub_sys" in list_systems()
    assert get_system("stub_sys") is s


# --------------------------------------------------------------------------- #
# End-to-end runner — stubs only
# --------------------------------------------------------------------------- #
def test_end_to_end_run_produces_report_and_raw_jsonl(tmp_path: Path):
    """Smoke test of the full orchestrator. Stub system, stub metric,
    real test set. Verifies:
      * raw.jsonl has one row per (system × voice × sentence × metric)
      * REPORT.md exists and contains the comparison table headers
      * report includes the metric names, system names, voice names
    """
    ts = load_test_set("v0.1-mini")
    sys_a = _StubSystem(name="stub_a")
    sys_b = _StubSystem(name="stub_b")
    metric = _StubMetric(name="stub_metric", fixed_score=0.1)

    plan = RunPlan(
        test_set=ts,
        systems=(sys_a, sys_b),
        metrics=(metric,),
        voices_per_system={"stub_a": ["voice-x"], "stub_b": ["voice-y"]},
        output_dir=tmp_path / "experiments",
        cache_dir=tmp_path / "cache",
        slug="harness-test",
    )
    exp_dir = run_plan(plan)

    raw = exp_dir / "raw.jsonl"
    report = exp_dir / "REPORT.md"
    assert raw.exists()
    assert report.exists()

    # 2 systems × 1 voice each × 10 sentences × 1 metric = 20 rows.
    lines = raw.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 20

    report_md = report.read_text(encoding="utf-8")
    assert "## Summary table" in report_md
    assert "stub_a" in report_md
    assert "stub_b" in report_md
    assert "voice-x" in report_md
    assert "voice-y" in report_md
    assert "stub_metric" in report_md
    # All rows scored 0.1 → mean should be 0.100 in the summary.
    assert "0.100" in report_md


def test_runner_records_synth_failure_as_nan_row(tmp_path: Path):
    """A vendor outage MUST NOT halt the run — the comparison table
    just shows `—` for that cell. The raw.jsonl row records the
    exception type so the operator can debug after the fact."""

    @dataclass
    class _FailingSystem:
        name: str = "failing"

        def synthesize(self, *, text: str, voice_id: str):
            raise RuntimeError("simulated vendor outage")

    ts = load_test_set("v0.1-mini")
    plan = RunPlan(
        test_set=ts,
        systems=(_FailingSystem(),),
        metrics=(_StubMetric(),),
        voices_per_system={"failing": ["any-voice"]},
        output_dir=tmp_path / "experiments",
        cache_dir=tmp_path / "cache",
        slug="failure-test",
    )
    exp_dir = run_plan(plan)
    raw_lines = (exp_dir / "raw.jsonl").read_text("utf-8").strip().splitlines()
    # 1 system × 1 voice × 10 sentences × 1 metric = 10 rows, all failed.
    assert len(raw_lines) == 10
    import json
    for line in raw_lines:
        row = json.loads(line)
        assert row["score"] is None  # NaN → null
        assert row["detail"]["error"] == "RuntimeError"
        assert row["detail"]["stage"] == "synthesize"
    report_md = (exp_dir / "REPORT.md").read_text("utf-8")
    assert "—" in report_md  # dashes for missing scores
    assert "n_nan" in report_md.lower()


def test_metric_crash_is_isolated_to_one_row(tmp_path: Path):
    """A metric crashing on one clip MUST NOT halt the run nor poison
    peer-system scores in the same row position. The crashed cell
    is NaN; everything else completes."""

    @dataclass
    class _FlakyMetric:
        name: str = "flaky"

        def score(self, pcm_int16, sample_rate, *, reference_text):
            if "iPhone" in reference_text:  # sentence 9 contains iPhone
                raise ValueError("simulated metric crash")
            return MetricResult(self.name, 0.5, "lower")

    ts = load_test_set("v0.1-mini")
    plan = RunPlan(
        test_set=ts,
        systems=(_StubSystem(name="s1"),),
        metrics=(_FlakyMetric(),),
        voices_per_system={"s1": ["v1"]},
        output_dir=tmp_path / "experiments",
        cache_dir=tmp_path / "cache",
        slug="flaky-metric-test",
    )
    exp_dir = run_plan(plan)
    import json
    rows = [
        json.loads(line)
        for line in (exp_dir / "raw.jsonl").read_text("utf-8").strip().splitlines()
    ]
    # 10 rows; exactly 1 NaN (the iPhone sentence).
    assert len(rows) == 10
    nan_rows = [r for r in rows if r["score"] is None]
    assert len(nan_rows) == 1
    assert "iPhone" in nan_rows[0]["sentence_text"]
    assert nan_rows[0]["detail"]["error"] == "ValueError"
