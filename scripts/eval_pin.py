"""Eval pin CLI — stamp an eval run's results onto voices.eval_metrics.

Workflow (operator-driven, ADR-12):

    # 1. Run an eval against the voice (existing harness; produces
    #    experiments/<date>-<slug>/raw.jsonl + REPORT.md):
    PYTHONPATH=src python scripts/eval_run.py \\
        --test-set v0.1-mini \\
        --systems neurovoice \\
        --neurovoice-voice tr-warm-storyteller-v0 \\
        --metrics whisper_wer,whisper_cer,utmosv2,secs \\
        --output-dir experiments/ \\
        --slug pin-tr-warm-storyteller-v0

    # 2. Pin the result onto the voice's DB row:
    PYTHONPATH=src python scripts/eval_pin.py \\
        --voice-db-id <uuid> \\
        --run-dir experiments/2026-05-28-pin-tr-warm-storyteller-v0/

The two-step split keeps the destructive DB write deliberate. Run 1
can be repeated cheaply (cache reuses synth output); run 2 is the
explicit stamping moment.

Behaviour:
  * Reads raw.jsonl from the supplied run directory.
  * Aggregates per-metric: mean score, p95, sample count.
  * Builds the eval_metrics payload per docs/decisions/2026-05-28-eval-pin.md §4.
  * Writes directly to voices.eval_metrics via VoiceRepo.pin_eval.
  * Does NOT go through the admin HTTP endpoint — that path is for
    operator UIs / scripted workflows over a remote host. CLI runs
    on the box with DB access.

Auth:
  * Uses the same DATABASE_URL as the server (read from settings).
  * No tenant scoping (operator-level operation).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _percentile(values: list[float], pct: float) -> float | None:
    """Pure-Python percentile (no numpy). Returns None for empty input.
    pct ∈ [0, 100]; uses linear interpolation between adjacent ranks."""
    finite = [v for v in values if v == v]  # drop NaN
    if not finite:
        return None
    s = sorted(finite)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _aggregate_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float | int | str | None]]:
    """raw.jsonl rows → per-metric aggregated scores.

    One entry per metric_name with: score (mean), p95 (95th percentile),
    n (sample count), direction. Nan scores excluded from the mean +
    p95 but counted toward n via `n_failed`.
    """
    by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_metric[r["metric_name"]].append(r)
    out: dict[str, dict[str, float | int | str | None]] = {}
    for name, group in by_metric.items():
        scores = [float(r["score"]) for r in group]
        finite_scores = [s for s in scores if s == s]
        direction = group[0].get("direction") or "lower"
        out[name] = {
            "score": (
                sum(finite_scores) / len(finite_scores)
                if finite_scores else float("nan")
            ),
            "p95": _percentile(finite_scores, 95.0),
            "n": len(group),
            "n_failed": len(scores) - len(finite_scores),
            "direction": direction,
        }
    return out


def _read_run_dir(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load raw.jsonl + the run's plan metadata (test_set slug, sentence
    count, etc.). Returns (rows, plan_summary)."""
    raw_path = run_dir / "raw.jsonl"
    if not raw_path.is_file():
        raise FileNotFoundError(
            f"raw.jsonl not found at {raw_path}; supply --run-dir pointing "
            "to an experiments/<date>-<slug>/ folder produced by eval_run.py"
        )
    rows: list[dict[str, Any]] = []
    with raw_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"raw.jsonl at {raw_path} is empty")

    # Infer test_set slug + sentence count from row data — eval_run.py
    # currently doesn't emit a separate plan.json (we could add one
    # later; for now derive from rows).
    sentence_indices = {r["sentence_index"] for r in rows}
    plan_summary = {
        "test_set_slug": "unknown",  # operator can override via flag
        "sentence_count": len(sentence_indices),
    }
    return rows, plan_summary


def _build_payload(
    *,
    rows: list[dict[str, Any]],
    plan_summary: dict[str, Any],
    test_set_slug: str | None,
    test_set_version: int,
    operator_id: str | None,
    notes: str | None,
    report_uri: str | None,
    voxcpm_version: str | None,
    lora_adapter_uri: str | None,
    lora_adapter_sha256: str | None,
    frontend_pack_id: str | None,
    lexicon_id: str | None,
) -> dict[str, Any]:
    """Compose the voices.eval_metrics blob per ADR-12 §4."""
    return {
        "schema_version": 1,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "test_set": {
            "slug": test_set_slug or plan_summary["test_set_slug"],
            "version": test_set_version,
            "sentence_count": plan_summary["sentence_count"],
        },
        "model": {
            "voxcpm_version": voxcpm_version,
            "lora_adapter_uri": lora_adapter_uri,
            "lora_adapter_sha256": lora_adapter_sha256,
            "frontend_pack_id": frontend_pack_id,
            "lexicon_id": lexicon_id,
        },
        "metrics": _aggregate_metrics(rows),
        "report_uri": report_uri,
        "operator_id": operator_id,
        "notes": notes,
    }


async def _write_pin(voice_db_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Open an async session via the server's settings and stamp the
    payload. No tenant scope (operator operation)."""
    from db.session import AsyncSessionLocal
    from repos import VoiceRepo

    # tenant_id is a sentinel — VoiceRepo.pin_eval operates by voice
    # UUID and does not gate on the tenant filter.
    sentinel_tenant = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        repo = VoiceRepo(session, sentinel_tenant)
        voice = await repo.pin_eval(voice_db_id, payload=payload)
        if voice is None:
            raise SystemExit(
                f"voice id={voice_db_id} not found in DB"
            )
        if voice.purged_at is not None:
            raise SystemExit(
                f"voice id={voice_db_id} is purged; pin refused"
            )
        await session.commit()
        print(
            f"pinned voice_id={voice.voice_id} "
            f"(db_id={voice.id}) — metrics: "
            f"{sorted(payload['metrics'].keys())}"
        )


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval_pin",
        description="Stamp an eval run's aggregated metrics onto "
                    "voices.eval_metrics (ADR-12).",
    )
    p.add_argument(
        "--voice-db-id", required=True,
        help="voices.id UUID — operator-known DB identifier",
    )
    p.add_argument(
        "--run-dir", required=True, type=Path,
        help="experiments/<date>-<slug>/ folder produced by eval_run.py",
    )
    p.add_argument(
        "--test-set-slug",
        help="override test set slug (default: 'unknown' until eval_run "
             "emits a plan.json; supply explicitly for now)",
    )
    p.add_argument("--test-set-version", type=int, default=1)
    p.add_argument("--operator-id")
    p.add_argument("--notes")
    p.add_argument(
        "--report-uri",
        help="optional URI/path of the human-readable report (REPORT.md)",
    )
    # model reproducibility metadata — best-effort, operator supplies
    # what they know about the engine state that produced the audio.
    p.add_argument("--voxcpm-version")
    p.add_argument("--lora-adapter-uri")
    p.add_argument("--lora-adapter-sha256")
    p.add_argument("--frontend-pack-id")
    p.add_argument("--lexicon-id")
    # Operator can dry-run to see the computed payload without
    # touching the DB.
    p.add_argument(
        "--dry-run", action="store_true",
        help="print the computed payload and exit without writing to DB",
    )
    return p


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("NEUROVOICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    sys.path.insert(0, str(_repo_root() / "src"))

    args = _build_argparser().parse_args()

    try:
        voice_db_id = uuid.UUID(args.voice_db_id)
    except ValueError as e:
        print(f"--voice-db-id must be a UUID: {e}", file=sys.stderr)
        return 2

    rows, plan_summary = _read_run_dir(args.run_dir)
    report_uri = args.report_uri
    if report_uri is None:
        candidate = args.run_dir / "REPORT.md"
        if candidate.is_file():
            report_uri = str(candidate)

    payload = _build_payload(
        rows=rows,
        plan_summary=plan_summary,
        test_set_slug=args.test_set_slug,
        test_set_version=args.test_set_version,
        operator_id=args.operator_id,
        notes=args.notes,
        report_uri=report_uri,
        voxcpm_version=args.voxcpm_version,
        lora_adapter_uri=args.lora_adapter_uri,
        lora_adapter_sha256=args.lora_adapter_sha256,
        frontend_pack_id=args.frontend_pack_id,
        lexicon_id=args.lexicon_id,
    )

    if args.dry_run:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    asyncio.run(_write_pin(voice_db_id, payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
