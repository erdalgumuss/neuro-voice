"""LoRA training config builder + VRAM-aware preset selection.

The VoxCPM training script consumes a YAML config; this module writes
that file. Batch size + step budget scale to the detected GPU so the
operator does not have to hand-tune for L4 vs L40S vs A100.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .project import ProjectLayout

logger = logging.getLogger("neurovoice.finetune.config")


@dataclass(frozen=True)
class VramPreset:
    batch_size: int
    grad_accum_steps: int
    max_batch_tokens: int


# Empirically calibrated on L4 (24 GB) / L40S (48 GB) / A100 (40 GB) shapes.
# Below 20 GB the batch shrinks aggressively; above 30 GB it widens.
DEFAULT_VRAM_PRESETS = {
    "small":  VramPreset(batch_size=2, grad_accum_steps=8, max_batch_tokens=3072),
    "medium": VramPreset(batch_size=4, grad_accum_steps=4, max_batch_tokens=4096),
    "large":  VramPreset(batch_size=8, grad_accum_steps=2, max_batch_tokens=8192),
}


def select_vram_preset(vram_gb: float) -> VramPreset:
    if vram_gb < 20:
        return DEFAULT_VRAM_PRESETS["small"]
    if vram_gb < 30:
        return DEFAULT_VRAM_PRESETS["medium"]
    return DEFAULT_VRAM_PRESETS["large"]


def step_budget_for_minutes(train_minutes: float) -> int:
    """Rough step count vs how many minutes of training audio you have.

    Roughly: at 30 min you can converge a usable LoRA in 300 steps; at
    120+ min the budget extends to 1000. Operator can override by passing
    ``num_iters`` explicitly to :func:`build_lora_config`.
    """
    if train_minutes < 30:
        return 300
    if train_minutes < 60:
        return 500
    if train_minutes < 120:
        return 800
    return 1000


@dataclass(frozen=True)
class LoRAHyperparameters:
    """Bottleneck rank + scale + which sub-modules get LoRA-wrapped."""
    enable_lm: bool = True
    enable_dit: bool = True
    enable_proj: bool = False
    r: int = 32
    alpha: int = 32
    dropout: float = 0.0


def build_lora_config(
    layout: ProjectLayout,
    *,
    model_dir: Path,
    train_minutes: float,
    vram_gb: float,
    num_iters: int | None = None,
    learning_rate: float = 1e-4,
    weight_decay: float = 0.01,
    sample_rate: int = 16000,
    out_sample_rate: int = 48000,
    lora: LoRAHyperparameters | None = None,
) -> dict[str, Any]:
    """Return the dict that gets written as ``voxcpm2_lora.yaml``."""
    if lora is None:
        lora = LoRAHyperparameters()
    preset = select_vram_preset(vram_gb)
    steps = int(num_iters) if num_iters is not None else step_budget_for_minutes(train_minutes)

    config: dict[str, Any] = {
        "pretrained_path": str(model_dir),
        "train_manifest": str(layout.train_manifest_path),
        "val_manifest": str(layout.val_manifest_path),
        "sample_rate": sample_rate,
        "out_sample_rate": out_sample_rate,
        "batch_size": preset.batch_size,
        "grad_accum_steps": preset.grad_accum_steps,
        "num_workers": 2,
        "num_iters": steps,
        "log_interval": 10,
        "valid_interval": max(100, steps // 2),
        "save_interval": max(100, steps // 2),
        "learning_rate": learning_rate,
        "weight_decay": weight_decay,
        "warmup_steps": min(100, max(20, steps // 10)),
        "max_steps": steps,
        "max_batch_tokens": preset.max_batch_tokens,
        "save_path": str(layout.checkpoint_dir),
        "tensorboard": str(layout.log_dir),
        "lambdas": {"loss/diff": 1.0, "loss/stop": 1.0},
        "lora": {
            "enable_lm": lora.enable_lm,
            "enable_dit": lora.enable_dit,
            "enable_proj": lora.enable_proj,
            "r": lora.r,
            "alpha": lora.alpha,
            "dropout": lora.dropout,
        },
    }
    return config


def write_lora_config(layout: ProjectLayout, config: dict[str, Any]) -> Path:
    """Persist the config dict at ``layout.lora_config_path``."""
    layout.conf_dir.mkdir(parents=True, exist_ok=True)
    layout.lora_config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    logger.info("wrote lora config: %s", layout.lora_config_path)
    return layout.lora_config_path
