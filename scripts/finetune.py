"""Command-line driver for the LoRA fine-tune pipeline.

Single CLI with subcommands mirroring the eight steps in
:mod:`finetune`. Each subcommand is independent — re-runnable, idempotent
where possible — so an operator can resume after any failure.

Example end-to-end run (from a fresh project directory):

    NEUROVOICE_DEEPGRAM_API_KEY=sk_live_...

    python scripts/finetune.py transcribe \\
        --root /content/drive/MyDrive/neurovoice/finetune \\
        --voice tr-warm-storyteller-v1

    python scripts/finetune.py validate-manifest --voice tr-warm-storyteller-v1
    python scripts/finetune.py split-manifest    --voice tr-warm-storyteller-v1
    python scripts/finetune.py write-config      --voice tr-warm-storyteller-v1 \\
        --model-dir /content/drive/MyDrive/neurovoice/models/VoxCPM2 \\
        --vram-gb 24 --train-minutes 45

    python scripts/finetune.py train   --voice tr-warm-storyteller-v1 \\
        --voxcpm-repo /content/VoxCPM

    python scripts/finetune.py infer   --voice tr-warm-storyteller-v1 \\
        --model-dir /content/drive/MyDrive/neurovoice/models/VoxCPM2

    python scripts/finetune.py export  --voice tr-warm-storyteller-v1 \\
        --gpu L4 --vram-gb 24
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _layout(args: argparse.Namespace):
    sys.path.insert(0, str(_repo_root() / "src"))
    from finetune.project import ProjectLayout
    layout = ProjectLayout(root=Path(args.root).resolve(), voice_id=args.voice)
    layout.ensure_dirs()
    return layout


def cmd_transcribe(args: argparse.Namespace) -> int:
    from finetune.transcribe import TranscribeConfig, build_raw_manifest
    layout = _layout(args)
    api_key = args.deepgram_api_key or os.environ.get("NEUROVOICE_DEEPGRAM_API_KEY")
    if not api_key:
        print(
            "Deepgram API key required — pass --deepgram-api-key or set "
            "NEUROVOICE_DEEPGRAM_API_KEY", file=sys.stderr,
        )
        return 2
    accepted, skipped = build_raw_manifest(layout, api_key, TranscribeConfig())
    print(f"transcribe done: {accepted} accepted, {skipped} skipped")
    print(f"raw manifest: {layout.raw_manifest_path}")
    print(f"review csv:   {layout.segments_review_csv}")
    return 0


def cmd_validate_manifest(args: argparse.Namespace) -> int:
    from finetune.manifest import validate_raw_manifest
    layout = _layout(args)
    records, warnings = validate_raw_manifest(layout)
    print(f"validated {len(records)} clips")
    if warnings:
        print(f"\n{len(warnings)} warnings:")
        for w in warnings[:20]:
            print("  -", w)
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more")
    return 0


def cmd_split_manifest(args: argparse.Namespace) -> int:
    from finetune.manifest import SplitConfig, split_manifest, validate_raw_manifest
    layout = _layout(args)
    records, _ = validate_raw_manifest(layout)
    cfg = SplitConfig(
        seed=args.seed,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        ref_audio_ratio=args.ref_audio_ratio,
    )
    counts = split_manifest(layout, records, cfg)
    print(f"split: train={counts['train']} val={counts['val']} test={counts['test']}")
    return 0


def cmd_write_config(args: argparse.Namespace) -> int:
    from finetune.config import build_lora_config, write_lora_config
    from finetune.manifest import validate_raw_manifest
    layout = _layout(args)
    records, _ = validate_raw_manifest(layout)
    train_minutes = sum(r["duration"] for r in records) / 60
    config = build_lora_config(
        layout,
        model_dir=Path(args.model_dir).resolve(),
        train_minutes=train_minutes,
        vram_gb=args.vram_gb,
        num_iters=args.num_iters,
        learning_rate=args.learning_rate,
    )
    path = write_lora_config(layout, config)
    print(f"wrote {path}")
    print(f"  train_minutes={train_minutes:.1f} steps={config['max_steps']}")
    print(f"  batch_size={config['batch_size']} grad_accum={config['grad_accum_steps']}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from finetune.train import run_training
    layout = _layout(args)
    rc = run_training(
        layout,
        voxcpm_repo=Path(args.voxcpm_repo).resolve(),
        python_bin=args.python_bin,
    )
    if rc != 0:
        print(f"trainer exited with code {rc}", file=sys.stderr)
    return rc


def cmd_infer(args: argparse.Namespace) -> int:
    from finetune.inference import run_inference_eval
    layout = _layout(args)
    infer_dir = run_inference_eval(
        layout,
        model_dir=Path(args.model_dir).resolve(),
        checkpoint_name=args.checkpoint,
        cfg_value=args.cfg_value,
        inference_timesteps=args.inference_timesteps,
    )
    print(f"inference outputs: {infer_dir}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from finetune.export import archive_outputs, write_run_metadata
    layout = _layout(args)
    write_run_metadata(
        layout,
        gpu_name=args.gpu,
        vram_gb=args.vram_gb,
    )
    out_zip, ckpt_zip = archive_outputs(layout)
    print(f"metadata: {layout.metadata_path}")
    print(f"outputs zip: {out_zip}")
    print(f"checkpoint zip: {ckpt_zip}")
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="finetune",
        description="LoRA fine-tune pipeline driver for NeuroVoice voices.",
    )
    p.add_argument(
        "--root",
        default=os.environ.get("NEUROVOICE_FINETUNE_ROOT", "./finetune"),
        help="parent directory holding per-voice project dirs",
    )
    p.add_argument(
        "--voice", required=True,
        help="voice_id slug; one subdirectory under --root per voice",
    )
    p.add_argument("--log-level", default=os.environ.get("NEUROVOICE_LOG_LEVEL", "INFO"))

    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe", help="ffmpeg + Deepgram + segmentation -> raw manifest")
    t.add_argument("--deepgram-api-key")
    t.set_defaults(func=cmd_transcribe)

    v = sub.add_parser("validate-manifest", help="check audio + duration on raw manifest")
    v.set_defaults(func=cmd_validate_manifest)

    s = sub.add_parser("split-manifest", help="write train/val/test JSONLs with ref_audio mixing")
    s.add_argument("--seed", type=int, default=20260524)
    s.add_argument("--val-ratio", type=float, default=0.10)
    s.add_argument("--test-ratio", type=float, default=0.10)
    s.add_argument("--ref-audio-ratio", type=float, default=0.40)
    s.set_defaults(func=cmd_split_manifest)

    c = sub.add_parser("write-config", help="generate voxcpm2_lora.yaml with VRAM-aware preset")
    c.add_argument("--model-dir", required=True)
    c.add_argument("--vram-gb", type=float, required=True)
    c.add_argument("--num-iters", type=int, default=None, help="override auto step count")
    c.add_argument("--learning-rate", type=float, default=1e-4)
    c.set_defaults(func=cmd_write_config)

    tr = sub.add_parser("train", help="invoke the VoxCPM training script")
    tr.add_argument("--voxcpm-repo", required=True)
    tr.add_argument("--python-bin", default="python")
    tr.set_defaults(func=cmd_train)

    i = sub.add_parser("infer", help="run inference eval prompts against a checkpoint")
    i.add_argument("--model-dir", required=True)
    i.add_argument("--checkpoint", default="latest")
    i.add_argument("--cfg-value", type=float, default=2.0)
    i.add_argument("--inference-timesteps", type=int, default=10)
    i.set_defaults(func=cmd_infer)

    e = sub.add_parser("export", help="metadata.json + zip outputs/checkpoint")
    e.add_argument("--gpu", default=None)
    e.add_argument("--vram-gb", type=float, default=None)
    e.set_defaults(func=cmd_export)

    return p


def main() -> int:
    parser = _build_argparser()
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
