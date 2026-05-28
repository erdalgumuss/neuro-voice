"""Manifest validation + train/val/test split for VoxCPM2 LoRA training.

The raw manifest (one JSONL row per accepted utterance) becomes three
split manifests the trainer consumes. ``train.jsonl`` carries ref_audio
hints on a tunable ratio so the model sees voice-cloning conditioning
during training, not just text-only.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import soundfile as sf

from .project import ProjectLayout

logger = logging.getLogger("neurovoice.finetune.manifest")


@dataclass(frozen=True)
class SplitConfig:
    seed: int = 20260524
    val_ratio: float = 0.10
    test_ratio: float = 0.10
    ref_audio_ratio: float = 0.40
    min_seconds: float = 1.0
    ideal_min_seconds: float = 3.0
    max_seconds: float = 30.0


def _resolve_audio_path(raw_audio: str, layout: ProjectLayout) -> Path:
    p = Path(raw_audio)
    if p.is_absolute():
        return p
    if (layout.project_dir / p).exists():
        return layout.project_dir / p
    return layout.audio_dir / p


def validate_raw_manifest(
    layout: ProjectLayout, cfg: SplitConfig | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Load + duration-check every row of ``layout.raw_manifest_path``.

    Returns ``(records, warnings)`` — records are validated rows ready
    for split; warnings carry advisory notes (e.g. shorter than ideal).
    Raises on hard failures (missing audio, empty text, out-of-range
    duration).
    """
    cfg = cfg or SplitConfig()
    if not layout.raw_manifest_path.exists():
        raise FileNotFoundError(
            f"raw manifest missing: {layout.raw_manifest_path}"
        )

    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    with layout.raw_manifest_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            audio_path = _resolve_audio_path(item["audio"], layout)
            text = str(item.get("text", "")).strip()
            if not audio_path.exists():
                raise FileNotFoundError(
                    f"line {line_no}: audio missing: {audio_path}"
                )
            if not text:
                raise ValueError(f"line {line_no}: empty text")
            info = sf.info(str(audio_path))
            duration = float(info.frames / info.samplerate)
            if duration < cfg.min_seconds:
                raise ValueError(
                    f"line {line_no}: too short ({duration:.2f}s): {audio_path}"
                )
            if duration > cfg.max_seconds:
                raise ValueError(
                    f"line {line_no}: too long ({duration:.2f}s): {audio_path}"
                )
            if duration < cfg.ideal_min_seconds:
                warnings.append(
                    f"line {line_no}: shorter than ideal "
                    f"({duration:.2f}s): {audio_path.name}"
                )
            records.append({
                "audio": str(audio_path),
                "text": text,
                "duration": round(duration, 3),
                "speaker_id": item.get("speaker_id", layout.voice_id),
            })

    if not records:
        raise ValueError("manifest is empty after validation")
    logger.info(
        "validated %d clips, total %.1f min", len(records),
        sum(r["duration"] for r in records) / 60,
    )
    return records, warnings


def split_manifest(
    layout: ProjectLayout,
    records: list[dict[str, Any]],
    cfg: SplitConfig | None = None,
) -> dict[str, int]:
    """Write ``train.jsonl`` / ``val.jsonl`` / ``test.jsonl`` under
    ``layout.split_dir``. Returns the count per split."""
    cfg = cfg or SplitConfig()
    layout.split_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(cfg.seed)
    shuffled = records[:]
    rng.shuffle(shuffled)

    n = len(shuffled)
    n_test = max(1, int(n * cfg.test_ratio)) if n >= 10 else 0
    n_val = max(1, int(n * cfg.val_ratio)) if n >= 10 else 0

    test_records = shuffled[:n_test]
    val_records = shuffled[n_test:n_test + n_val]
    train_records = shuffled[n_test + n_val:]

    by_speaker: dict[str, list[dict[str, Any]]] = {}
    for record in train_records:
        by_speaker.setdefault(record["speaker_id"], []).append(record)

    def _with_ref(rows: list[dict[str, Any]], *, enable_ref: bool):
        out: list[dict[str, Any]] = []
        for record in rows:
            item = {
                "audio": record["audio"],
                "text": record["text"],
                "duration": record["duration"],
                "dataset_id": 0,
            }
            if enable_ref and rng.random() < cfg.ref_audio_ratio:
                candidates = [
                    c for c in by_speaker.get(record["speaker_id"], [])
                    if c["audio"] != record["audio"]
                ]
                if candidates:
                    item["ref_audio"] = rng.choice(candidates)["audio"]
            out.append(item)
        return out

    train_jsonl = _with_ref(train_records, enable_ref=True)
    val_jsonl = _with_ref(val_records, enable_ref=False)
    test_jsonl = _with_ref(test_records, enable_ref=False)

    for path, rows in (
        (layout.train_manifest_path, train_jsonl),
        (layout.val_manifest_path, val_jsonl),
        (layout.test_manifest_path, test_jsonl),
    ):
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = {"train": len(train_jsonl), "val": len(val_jsonl), "test": len(test_jsonl)}
    logger.info("split written: %s", counts)
    return counts
