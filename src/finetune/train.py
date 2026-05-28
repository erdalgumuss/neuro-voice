"""Thin subprocess wrapper around VoxCPM's training script.

The actual SGD loop lives in upstream ``scripts/train_voxcpm_finetune.py``
inside the VoxCPM repo; our role is to feed it the config built by
:mod:`finetune.config` and surface the exit code. Bringing the loop into
this codebase would require pulling in VoxCPM's training-only deps
(argbind, accelerate's distributed glue), which we explicitly stay out
of — those are runtime concerns for the training host, not the gateway.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .project import ProjectLayout

logger = logging.getLogger("neurovoice.finetune.train")


def run_training(
    layout: ProjectLayout,
    *,
    voxcpm_repo: Path,
    script: str = "scripts/train_voxcpm_finetune.py",
    python_bin: str = "python",
) -> int:
    """Launch the training subprocess, blocking until it exits.

    ``voxcpm_repo`` is the path to a cloned (or pip-installed-editable)
    VoxCPM checkout that ships the training script. Returns the
    subprocess exit code.
    """
    config_path = layout.lora_config_path
    if not config_path.exists():
        raise FileNotFoundError(
            f"lora config not built yet: {config_path} — run "
            "`finetune config` first"
        )
    train_script = voxcpm_repo / script
    if not train_script.exists():
        raise FileNotFoundError(
            f"VoxCPM training script missing at {train_script}; "
            "clone or update the VoxCPM repo"
        )

    cmd = [python_bin, str(train_script), "--config_path", str(config_path)]
    logger.info("launching trainer: %s (cwd=%s)", " ".join(cmd), voxcpm_repo)
    result = subprocess.run(cmd, cwd=str(voxcpm_repo))
    return result.returncode
