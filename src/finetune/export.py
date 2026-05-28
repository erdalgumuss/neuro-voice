"""Archive metadata + zip the outputs/checkpoints for handoff.

The training run produces a tree of artifacts under ``layout.project_dir``;
this step bundles the durable ones into two zip files plus a metadata
JSON so the operator can hand the result off to the voice-promotion
workflow (catalog enrollment, watermark assignment, eval-pin recording).
"""

from __future__ import annotations

import datetime
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .project import ProjectLayout

logger = logging.getLogger("neurovoice.finetune.export")


def write_run_metadata(
    layout: ProjectLayout,
    *,
    model_id: str = "openbmb/VoxCPM2",
    gpu_name: str | None = None,
    vram_gb: float | None = None,
    train_clips: int | None = None,
    val_clips: int | None = None,
    test_clips: int | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Persist run_metadata.json next to the project root."""
    meta: dict[str, Any] = {
        "voice_id": layout.voice_id,
        "model": model_id,
        "date_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "manifest": str(layout.raw_manifest_path),
        "train_manifest": str(layout.train_manifest_path),
        "val_manifest": str(layout.val_manifest_path),
        "test_manifest": str(layout.test_manifest_path),
        "config_path": str(layout.lora_config_path),
        "checkpoint_dir": str(layout.checkpoint_dir),
        "output_dir": str(layout.output_dir),
        "gpu": gpu_name,
        "vram_gb": round(vram_gb, 2) if vram_gb is not None else None,
        "train_clips": train_clips,
        "val_clips": val_clips,
        "test_clips": test_clips,
    }
    if extra:
        meta.update(extra)
    layout.metadata_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    logger.info("wrote run metadata: %s", layout.metadata_path)
    return layout.metadata_path


def archive_outputs(layout: ProjectLayout) -> tuple[Path, Path]:
    """Zip outputs/ and checkpoints/latest. Returns the two zip paths."""
    outputs_zip_base = layout.project_dir / f"{layout.voice_id}-outputs"
    checkpoint_zip_base = layout.project_dir / f"{layout.voice_id}-lora-latest"

    for base in (outputs_zip_base, checkpoint_zip_base):
        zip_path = Path(str(base) + ".zip")
        if zip_path.exists():
            zip_path.unlink()

    shutil.make_archive(
        str(outputs_zip_base), "zip",
        root_dir=str(layout.project_dir), base_dir="outputs",
    )
    shutil.make_archive(
        str(checkpoint_zip_base), "zip",
        root_dir=str(layout.checkpoint_dir), base_dir="latest",
    )
    out_zip = Path(str(outputs_zip_base) + ".zip")
    ckpt_zip = Path(str(checkpoint_zip_base) + ".zip")
    logger.info("archived: %s, %s", out_zip, ckpt_zip)
    return out_zip, ckpt_zip
