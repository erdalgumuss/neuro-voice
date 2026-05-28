"""LoRA checkpoint inference test — generate eval prompts and save WAVs.

After training, the operator wants a quick listenable sanity check:
"does the checkpoint at ``latest`` actually sound like the target
voice?" This module loads the LoRA adapter on top of the base model and
synthesizes a configurable set of evaluation prompts to
``layout.output_dir / "infer_<checkpoint>"``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from .project import ProjectLayout

logger = logging.getLogger("neurovoice.finetune.inference")


@dataclass(frozen=True)
class EvalPrompt:
    slug: str
    text: str


# Default neutral-tone Turkish eval prompts. Operators can pass a custom
# list to :func:`run_inference_eval` for domain-specific evaluation
# (warm storyteller, didactic instructor, conversational agent, ...).
DEFAULT_TR_EVAL_PROMPTS: tuple[EvalPrompt, ...] = (
    EvalPrompt("01_neutral_short",
               "Merhaba. Bugün seninle kısa bir test cümlesi sentezleyelim."),
    EvalPrompt("02_calm",
               "Şimdi yavaşça gözlerini kapat, derin bir nefes al ve usulca bırak."),
    EvalPrompt("03_excited",
               "Vay canına! Bunu gerçekten sen mi yaptın? Harika görünüyor!"),
    EvalPrompt("04_laugh",
               "[laughs] Hahaha, bu gerçekten çok komikti. Bunu hiç beklemiyordum."),
    EvalPrompt("05_whisper",
               "[whispers] Şimdi çok sessiz konuşmalıyız. Küçük sırrımızı saklayalım."),
    EvalPrompt("06_instructor",
               "Bu bölümde önce kavramı anlayacağız, sonra birlikte kısa bir örnek çözeceğiz."),
)


def run_inference_eval(
    layout: ProjectLayout,
    *,
    model_dir: Path,
    checkpoint_name: str = "latest",
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
    prompts: tuple[EvalPrompt, ...] = DEFAULT_TR_EVAL_PROMPTS,
) -> Path:
    """Load LoRA checkpoint, synthesize each prompt, write WAVs to disk.

    Returns the directory holding the generated audio + per-prompt
    timing JSON.
    """
    # Lazy import — voxcpm pulls torch + the full base model. Keep the
    # CLI startup fast for the non-inference subcommands.
    from voxcpm import VoxCPM
    try:
        from voxcpm.model.voxcpm import LoRAConfig
    except Exception:
        from voxcpm.model.voxcpm2 import LoRAConfig

    lora_ckpt = layout.checkpoint_dir / checkpoint_name
    if not lora_ckpt.exists():
        raise FileNotFoundError(f"LoRA checkpoint missing: {lora_ckpt}")
    lora_config_payload = json.loads(
        (lora_ckpt / "lora_config.json").read_text(encoding="utf-8")
    )
    lora_config = LoRAConfig(
        **lora_config_payload.get("lora_config", lora_config_payload)
    )

    model = VoxCPM.from_pretrained(
        str(model_dir),
        lora_config=lora_config,
        lora_weights_path=str(lora_ckpt),
        load_denoiser=False,
        optimize=False,
    )

    infer_dir = layout.output_dir / f"infer_{checkpoint_name}"
    infer_dir.mkdir(parents=True, exist_ok=True)

    timings: list[dict[str, object]] = []
    for prompt in prompts:
        t0 = time.time()
        wav = model.generate(
            text=prompt.text,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
        )
        elapsed = time.time() - t0
        out_path = infer_dir / f"{prompt.slug}.wav"
        sf.write(str(out_path), wav, model.tts_model.sample_rate)
        timings.append({
            "slug": prompt.slug, "text": prompt.text,
            "elapsed_seconds": round(elapsed, 3),
            "output": str(out_path.relative_to(layout.project_dir)),
        })
        logger.info("inferred %s in %.1fs -> %s", prompt.slug, elapsed, out_path)

    (infer_dir / "timings.json").write_text(
        json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return infer_dir
