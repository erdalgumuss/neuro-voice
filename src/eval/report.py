"""Markdown report writer for the eval harness.

The report is the deliverable. It MUST be:
  * Human-readable in 30 seconds (the comparison table is the lead).
  * Re-runnable — the header records every input that influenced the
    scores (test set slug, systems + their model_ids, metrics +
    their model sizes, run timestamp).
  * Re-comparable across runs — same shape, same column order,
    same number formatting, every time.

We deliberately keep this in stdlib only — no jinja, no pandas. The
report is a static markdown file checked into `experiments/` next to
the raw JSONL.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .runner import EvalRow, RunPlan, _isnan


def _aggregate(rows: list[EvalRow]) -> dict:
    """Group by (system, voice, metric) → list of scores. Return shape
    is `{system: {voice: {metric: (mean, count, n_nan)}}}` so the
    table writer can emit per-system, per-voice rows."""
    buckets: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    nans: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for r in rows:
        if _isnan(r.score):
            nans[r.system][r.voice_id][r.metric_name] += 1
        else:
            buckets[r.system][r.voice_id][r.metric_name].append(r.score)
    return {"scores": buckets, "nans": nans}


def write_report(*, plan: RunPlan, rows: list[EvalRow], report_path: Path) -> None:
    agg = _aggregate(rows)

    metric_names = sorted({m.name for m in plan.metrics})
    metric_dir: dict[str, str] = {}
    for m in plan.metrics:
        metric_dir[m.name] = "lower" if m.name in ("whisper_wer",) else "higher"

    lines: list[str] = []
    lines.append(f"# NQAI Voice Eval — {plan.slug}")
    lines.append("")
    lines.append(f"- **Test set**: `{plan.test_set.slug}` "
                 f"({len(plan.test_set.sentences)} sentences from "
                 f"`{plan.test_set.path.relative_to(plan.test_set.path.parents[2])}`)")
    lines.append(f"- **Run timestamp (UTC)**: "
                 f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append(f"- **Systems under test**: "
                 f"{', '.join(f'`{s.name}`' for s in plan.systems)}")
    lines.append(f"- **Metrics**: "
                 f"{', '.join(f'`{m.name}`' for m in plan.metrics)}")
    lines.append("- **Raw data**: `raw.jsonl` (one row per "
                 "system × voice × sentence × metric)")
    lines.append("")
    lines.append("## Summary table")
    lines.append("")
    lines.append(_summary_table(agg, metric_names, metric_dir))
    lines.append("")
    lines.append("## Per-sentence breakdown")
    lines.append("")
    lines.append(_per_sentence_table(rows, metric_names))
    lines.append("")
    lines.append(_notes())

    report_path.write_text("\n".join(lines), encoding="utf-8")


def _summary_table(
    agg: dict,
    metric_names: list[str],
    metric_dir: dict[str, str],
) -> str:
    headers = ["System", "Voice", *[
        f"{m} ({'↓' if metric_dir.get(m, 'higher') == 'lower' else '↑'})"
        for m in metric_names
    ], "n_nan"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for system, by_voice in sorted(agg["scores"].items()):
        for voice_id, by_metric in sorted(by_voice.items()):
            cells = [f"`{system}`", f"`{voice_id}`"]
            n_nan_total = 0
            for m in metric_names:
                scores = by_metric.get(m, [])
                n_nan_total += agg["nans"][system][voice_id].get(m, 0)
                if not scores:
                    cells.append("—")
                else:
                    mean = sum(scores) / len(scores)
                    cells.append(f"{mean:.3f}  (n={len(scores)})")
            cells.append(str(n_nan_total))
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _per_sentence_table(
    rows: list[EvalRow],
    metric_names: list[str],
) -> str:
    """One row per sentence × system × voice; columns are the metrics.
    Useful when reading the report on a long screen — you can see
    which specific sentence dropped a system."""
    # Group rows by sentence first, then within each sentence list per
    # (system, voice).
    by_sentence: dict[int, list[EvalRow]] = defaultdict(list)
    for r in rows:
        by_sentence[r.sentence_index].append(r)

    if not by_sentence:
        return "_(no rows produced)_"

    lines: list[str] = []
    for sidx in sorted(by_sentence):
        srows = by_sentence[sidx]
        text = srows[0].sentence_text
        category = srows[0].sentence_category
        lines.append(f"### Sentence {sidx} — {category}")
        lines.append(f"> {text}")
        lines.append("")
        headers = ["System", "Voice", *metric_names]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        per_sv: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        for r in srows:
            per_sv[(r.system, r.voice_id)][r.metric_name] = r.score
        for (system, voice), metrics in sorted(per_sv.items()):
            cells = [f"`{system}`", f"`{voice}`"]
            for m in metric_names:
                v = metrics.get(m)
                if v is None or _isnan(v):
                    cells.append("—")
                else:
                    cells.append(f"{v:.3f}")
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def _notes() -> str:
    return (
        "## Notes\n"
        "\n"
        "- Lower WER is better (↓); higher UTMOS is better (↑).\n"
        "- `n_nan` counts metric calls that returned NaN — usually empty\n"
        "  PCM from a synthesis failure, or a 3rd-party metric crash\n"
        "  (see `raw.jsonl` for the per-row `detail.error`).\n"
        "- `engine_inputs` in `usage_records` (PR #1) carries the\n"
        "  reproducibility envelope for NQAI rows: model_id,\n"
        "  hf_revision, cfg/timesteps, seed. Cross-reference there\n"
        "  if two runs of the same row produced different scores.\n"
        "- Cached audio per (system, voice, text) lives under the\n"
        "  configured `cache_dir`. Delete it to force a fresh run.\n"
    )
