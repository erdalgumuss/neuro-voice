"""Eval orchestrator — `run(test_set, systems, metrics, voices, ...)`.

For each (system × voice × sentence × metric) tuple, this:
  1. asks the system to synthesize the sentence,
  2. caches the PCM by hash so a retried run doesn't re-bill,
  3. scores every metric against the clip,
  4. writes a JSON intermediate (`raw.jsonl`) and a markdown report.

The runner is a pure orchestrator — no metric / system logic lives
here. That separation is what makes "add a new vendor" or "add a new
metric" each a single file in the right subdirectory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .dataset import TestSentence, TestSet
from .metrics import Metric, MetricResult
from .systems import SystemOutput, TTSSystem

logger = logging.getLogger("nqai_voice.eval.runner")


@dataclass(frozen=True)
class EvalRow:
    """One row in `raw.jsonl` — a single (system, voice, sentence, metric)
    score plus the system metadata that produced the clip."""

    system: str
    voice_id: str
    model_id: str
    sentence_index: int
    sentence_category: str
    sentence_text: str
    elapsed_ms: int
    metric_name: str
    score: float
    direction: str
    detail: dict[str, object] | None


def _cache_key(system: str, voice_id: str, model_id: str, text: str) -> str:
    """Stable hash that drives the on-disk audio cache. Collisions are
    impossible within reason — text + system + voice + model is more
    than enough entropy for a per-run cache. Sha256 truncated to 16
    hex chars (~10^19 namespace) keeps filenames readable."""
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(voice_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(model_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:16]


def _synth_one(
    system: TTSSystem,
    sentence: TestSentence,
    voice_id: str,
    cache_dir: Path,
) -> SystemOutput:
    """Synthesize a single sentence with disk cache. The cache is
    intentional cost discipline — vendor calls cost real money and
    interrupted runs are common during integration."""
    # Probe the system once to learn what model_id it will report; we
    # need that for the cache key. Cheapest probe: synthesize the
    # sentence and check whether the cache has the result. Since the
    # metadata depends on the call, we cache by (system_name, voice,
    # text) only — same hashing skipping model_id — and store the
    # model_id alongside.
    key = _cache_key(system.name, voice_id, "*", sentence.text)
    cache_pcm = cache_dir / f"{key}.pcm"
    cache_meta = cache_dir / f"{key}.json"

    if cache_pcm.exists() and cache_meta.exists():
        meta = json.loads(cache_meta.read_text(encoding="utf-8"))
        from .systems import SystemMetadata
        return SystemOutput(
            pcm_int16=cache_pcm.read_bytes(),
            sample_rate=meta["sample_rate"],
            metadata=SystemMetadata(
                system=meta["system"],
                model_id=meta["model_id"],
                voice_id=meta["voice_id"],
                elapsed_ms=meta["elapsed_ms"],
                extra=meta.get("extra"),
            ),
        )

    out = system.synthesize(text=sentence.text, voice_id=voice_id)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_pcm.write_bytes(out.pcm_int16)
    cache_meta.write_text(json.dumps({
        "sample_rate": out.sample_rate,
        "system": out.metadata.system,
        "model_id": out.metadata.model_id,
        "voice_id": out.metadata.voice_id,
        "elapsed_ms": out.metadata.elapsed_ms,
        "extra": out.metadata.extra,
    }), encoding="utf-8")
    return out


def _score(
    metric: Metric,
    out: SystemOutput,
    sentence: TestSentence,
) -> MetricResult:
    """Apply one metric to one synth clip; soft-fail (nan score +
    exception message in detail) on a metric crash so a bad metric
    doesn't halt the entire run."""
    try:
        return metric.score(
            out.pcm_int16,
            out.sample_rate,
            reference_text=sentence.text,
        )
    except Exception as e:  # noqa: BLE001 — metrics are 3rd-party heavy
        logger.exception(
            "metric %s crashed on system=%s voice=%s sentence=%d",
            metric.name, out.metadata.system, out.metadata.voice_id,
            sentence.index,
        )
        return MetricResult(
            metric_name=metric.name,
            score=float("nan"),
            direction="lower",
            detail={"error": type(e).__name__, "message": str(e)},
        )


@dataclass(frozen=True)
class RunPlan:
    """A run is the cartesian product of `systems × voices` filtered
    by a per-system voice list. `metrics` is global — every clip gets
    scored by every metric. `output_dir` is where the markdown + raw
    JSONL land; the runner creates a timestamped subdirectory there."""

    test_set: TestSet
    systems: tuple[TTSSystem, ...]
    metrics: tuple[Metric, ...]
    voices_per_system: dict[str, list[str]]
    output_dir: Path
    cache_dir: Path
    slug: str = "baseline"


def run_plan(plan: RunPlan) -> Path:
    """Execute the plan; return the path to the experiment directory.

    Side effects:
      - Creates `{output_dir}/{date}-{slug}/raw.jsonl` (one EvalRow per line).
      - Creates `{output_dir}/{date}-{slug}/REPORT.md` (human summary).
      - Uses `{cache_dir}/{hash}.pcm` to cache vendor calls across re-runs.
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    exp_dir = plan.output_dir / f"{date}-{plan.slug}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    raw_path = exp_dir / "raw.jsonl"
    report_path = exp_dir / "REPORT.md"

    rows: list[EvalRow] = []
    with raw_path.open("w", encoding="utf-8") as raw_fh:
        for system in plan.systems:
            voice_ids = plan.voices_per_system.get(system.name, [])
            if not voice_ids:
                logger.warning("no voices configured for system=%s — skipping",
                               system.name)
                continue
            for voice_id in voice_ids:
                for sentence in plan.test_set.sentences:
                    try:
                        out = _synth_one(
                            system, sentence, voice_id, plan.cache_dir,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.exception(
                            "synthesize failed system=%s voice=%s sentence=%d",
                            system.name, voice_id, sentence.index,
                        )
                        # Emit one row per metric so the report shows
                        # the failure side-by-side with peer systems.
                        for metric in plan.metrics:
                            row = EvalRow(
                                system=system.name,
                                voice_id=voice_id,
                                model_id="<synth_failed>",
                                sentence_index=sentence.index,
                                sentence_category=sentence.category,
                                sentence_text=sentence.text,
                                elapsed_ms=0,
                                metric_name=metric.name,
                                score=float("nan"),
                                direction="lower",
                                detail={"error": type(e).__name__,
                                        "stage": "synthesize",
                                        "message": str(e)},
                            )
                            rows.append(row)
                            raw_fh.write(json.dumps(_row_dict(row),
                                                    ensure_ascii=False) + "\n")
                        continue
                    for metric in plan.metrics:
                        result = _score(metric, out, sentence)
                        row = EvalRow(
                            system=system.name,
                            voice_id=voice_id,
                            model_id=out.metadata.model_id,
                            sentence_index=sentence.index,
                            sentence_category=sentence.category,
                            sentence_text=sentence.text,
                            elapsed_ms=out.metadata.elapsed_ms,
                            metric_name=result.metric_name,
                            score=result.score,
                            direction=result.direction,
                            detail=result.detail,
                        )
                        rows.append(row)
                        raw_fh.write(json.dumps(_row_dict(row),
                                                ensure_ascii=False) + "\n")

    from .report import write_report
    write_report(
        plan=plan,
        rows=rows,
        report_path=report_path,
    )
    return exp_dir


def _row_dict(row: EvalRow) -> dict:
    """JSON-safe dict (handles NaN → null per JSON spec)."""
    d = {
        "system": row.system,
        "voice_id": row.voice_id,
        "model_id": row.model_id,
        "sentence_index": row.sentence_index,
        "sentence_category": row.sentence_category,
        "sentence_text": row.sentence_text,
        "elapsed_ms": row.elapsed_ms,
        "metric_name": row.metric_name,
        "score": None if _isnan(row.score) else row.score,
        "direction": row.direction,
        "detail": row.detail,
    }
    return d


def _isnan(x: float) -> bool:
    with contextlib.suppress(Exception):
        return x != x  # canonical NaN check w/o math import
    return False


def aggregate(rows: Iterable[EvalRow]) -> dict[tuple[str, str, str], float]:
    """Mean score per (system, voice_id, metric_name), ignoring NaN."""
    bucket: dict[tuple[str, str, str], list[float]] = {}
    for r in rows:
        if _isnan(r.score):
            continue
        bucket.setdefault((r.system, r.voice_id, r.metric_name), []).append(r.score)
    return {k: sum(v) / len(v) for k, v in bucket.items() if v}
