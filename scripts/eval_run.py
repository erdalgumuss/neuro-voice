"""Eval harness CLI.

Single command to produce the comparison table the audit asks for:

    PYTHONPATH=src python scripts/eval_run.py \\
        --test-set v0.1-mini \\
        --systems neurovoice elevenlabs \\
        --neurovoice-voice tr-warm-storyteller-v0 \\
        --elevenlabs-voice 21m00Tcm4TlvDq8ikWAM \\
        --metrics whisper_wer \\
        --output-dir experiments \\
        --slug neurovoice-vs-elevenlabs-baseline

Auth:
  - NEUROVOICE_API_KEY  — bearer for NeuroVoice rows
  - ELEVENLABS_API_KEY  — for ElevenLabs rows

Default behaviour is INTENTIONALLY conservative: nothing real runs
unless you ask for it by name. `--list-*` flags introspect the
available test sets, metrics, and systems without producing any
audio or scores.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("NEUROVOICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # `src/` on the path so this script runs `PYTHONPATH=src python …`
    # AND vanilla `python scripts/eval_run.py` (workshop ergonomics).
    sys.path.insert(0, str(_repo_root() / "src"))

    parser = _build_argparser()
    args = parser.parse_args()

    if args.list_test_sets:
        from eval.dataset import list_test_sets
        for slug in list_test_sets():
            print(slug)
        return 0
    if args.list_metrics:
        # `register_metric` populates the registry; CLI registers
        # known metrics below.
        _register_real_metrics(whisper_model_size=args.whisper_model)
        from eval.metrics import list_metrics
        for m in list_metrics():
            print(m)
        return 0
    if args.list_systems:
        _register_real_systems(args)
        from eval.systems import list_systems
        for s in list_systems():
            print(s)
        return 0

    if not args.systems:
        parser.error("--systems is required for a real run")
    if not args.metrics:
        parser.error("--metrics is required for a real run")

    neurovoice_voices = args.neurovoice_voice or []
    elevenlabs_voices = args.elevenlabs_voice or []
    voice_count = len(neurovoice_voices) + len(elevenlabs_voices)
    _register_real_metrics(
        whisper_model_size=args.whisper_model,
        secs_reference_audio=args.secs_reference_audio,
        voice_count=voice_count,
    )
    _register_real_systems(args)

    from eval.dataset import load_test_set
    from eval.metrics import get_metric
    from eval.runner import RunPlan, run_plan
    from eval.systems import get_system

    test_set = load_test_set(args.test_set)

    voices_per_system: dict[str, list[str]] = {}
    if "neurovoice" in args.systems:
        if not args.neurovoice_voice:
            parser.error("--neurovoice-voice required when --systems includes neurovoice")
        voices_per_system["neurovoice"] = args.neurovoice_voice
    if "elevenlabs" in args.systems:
        if not args.elevenlabs_voice:
            parser.error(
                "--elevenlabs-voice required when --systems includes elevenlabs"
            )
        voices_per_system["elevenlabs"] = args.elevenlabs_voice

    systems = tuple(get_system(name) for name in args.systems)
    metrics = tuple(get_metric(name) for name in args.metrics)

    output_dir = Path(args.output_dir).resolve()
    cache_dir = Path(args.cache_dir).resolve()

    plan = RunPlan(
        test_set=test_set,
        systems=systems,
        metrics=metrics,
        voices_per_system=voices_per_system,
        output_dir=output_dir,
        cache_dir=cache_dir,
        slug=args.slug,
    )
    exp_dir = run_plan(plan)
    print(f"\nReport written to: {exp_dir / 'REPORT.md'}")
    print(f"Raw JSONL:         {exp_dir / 'raw.jsonl'}")
    print(f"Audio cache:       {cache_dir}/")
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval_run",
        description="Run NeuroVoice eval comparison and produce a report.",
    )
    p.add_argument("--test-set", default="v0.1-mini",
                   help="registered test-set slug (default: v0.1-mini)")
    p.add_argument("--systems", nargs="+",
                   choices=["neurovoice", "elevenlabs"],
                   help="systems to score (one or more)")
    p.add_argument("--metrics", nargs="+",
                   help="metric names to apply (e.g. whisper_wer)")
    p.add_argument("--neurovoice-voice", nargs="+",
                   help="NeuroVoice voice slug(s) — required if `neurovoice` in --systems")
    p.add_argument("--elevenlabs-voice", nargs="+",
                   help="ElevenLabs voice id(s) — required if `elevenlabs`")
    p.add_argument("--neurovoice-model-id", default="voxcpm2-tr-hd",
                   help="NeuroVoice preset (default: voxcpm2-tr-hd)")
    p.add_argument("--neurovoice-base-url", default="http://localhost:8000")
    p.add_argument("--elevenlabs-model-id", default="eleven_multilingual_v2")
    p.add_argument("--whisper-model", default="large-v3",
                   help="Whisper size (large-v3 default, medium for cheap)")
    p.add_argument("--output-dir", default="experiments",
                   help="where to write `<date>-<slug>/REPORT.md`")
    p.add_argument("--cache-dir", default="/tmp/neurovoice-eval-cache",
                   help="audio cache (skip vendor re-bills across re-runs)")
    p.add_argument("--slug", default="baseline",
                   help="report subdirectory name suffix")
    # ADR-12 — SECS reference audio. SECS scores TTS output similarity
    # against the voice's clone reference, so it needs a per-voice
    # reference path. v0 only supports SINGLE-voice runs when SECS is
    # enabled (the metric registry is global and SECSMetric binds at
    # construction). Multi-voice + SECS would require a runner refactor
    # to per-voice metric resolution; tracked as ADR-12 follow-up.
    p.add_argument("--secs-reference-audio", type=Path,
                   help="path to the voice's reference audio (required "
                        "when --metrics includes 'secs'); single-voice "
                        "runs only in v0")

    p.add_argument("--list-test-sets", action="store_true")
    p.add_argument("--list-metrics", action="store_true")
    p.add_argument("--list-systems", action="store_true")
    return p


def _register_real_metrics(
    *,
    whisper_model_size: str,
    secs_reference_audio: "Path | None" = None,
    voice_count: int = 0,
) -> None:
    """Lazy: only import the heavy metric modules when asked. Keeps
    the CLI startup fast for --list-* probes.

    Registers the four metrics ADR-12 ships: whisper_wer, whisper_cer
    (sharing one Whisper model load), utmosv2, secs. SECS requires a
    reference audio path supplied via --secs-reference-audio and is
    restricted to single-voice runs in v0 (see module docstring of
    secs.py — global registry + per-voice binding is incompatible
    with multi-voice runs).
    """
    from eval.metrics import register_metric
    from eval.metrics.whisper_wer import WhisperCERMetric, WhisperWERMetric

    wer_metric = WhisperWERMetric(model_size=whisper_model_size)
    register_metric("whisper_wer", wer_metric)
    # Share the same Whisper model load for CER (~3 GB checkpoint
    # otherwise loaded twice).
    register_metric(
        "whisper_cer",
        WhisperCERMetric(shared_metric=wer_metric),
    )
    from eval.metrics.utmosv2 import UTMOSv2Metric
    register_metric("utmosv2", UTMOSv2Metric())

    if secs_reference_audio is not None:
        if voice_count > 1:
            raise SystemExit(
                "SECS is single-voice per run in v0; "
                "drop --secs-reference-audio or restrict --neurovoice-voice "
                "to a single slug. Multi-voice SECS is tracked as an ADR-12 "
                "follow-up (runner needs per-voice metric resolution)."
            )
        if not secs_reference_audio.is_file():
            raise SystemExit(
                f"--secs-reference-audio path not found: {secs_reference_audio}"
            )
        from eval.metrics.secs import SECSMetric
        import soundfile as sf
        data, sr = sf.read(str(secs_reference_audio), dtype="int16", always_2d=False)
        if data.ndim == 2:
            import numpy as np
            data = data.mean(axis=1).astype(np.int16)
        secs = SECSMetric.from_reference_pcm(data.tobytes(), int(sr))
        register_metric("secs", secs)


def _register_real_systems(args) -> None:
    """Register the systems the CLI knows how to invoke. Each adapter
    is imported lazily for the same reason as metrics — we don't
    want a no-network probe to fail because httpx wasn't installed."""
    from eval.systems import register_system
    if "neurovoice" in (args.systems or []) or args.list_systems:
        from eval.systems.neurovoice import NeuroVoiceSystem
        register_system("neurovoice", NeuroVoiceSystem(
            api_key=os.environ.get("NEUROVOICE_API_KEY", ""),
            base_url=args.neurovoice_base_url,
            model_id=args.neurovoice_model_id,
        ))
    if "elevenlabs" in (args.systems or []) or args.list_systems:
        from eval.systems.elevenlabs import ElevenLabsSystem
        register_system("elevenlabs", ElevenLabsSystem(
            api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
            model_id=args.elevenlabs_model_id,
        ))


if __name__ == "__main__":
    raise SystemExit(main())
