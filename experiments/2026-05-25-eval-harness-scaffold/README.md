# Eval harness scaffold — 2026-05-25

This directory is the conventional landing slot for the first eval
runs. It currently holds only this README because PR #3 ships the
**harness**, not yet the **numbers**. The numbers come from operator
runs against real GPUs + the real ElevenLabs key.

## How to produce the first comparison table

Prereqs:

- Worker process running with `NQAI_WORKER_WARMUP_VOICES="neeko-v01"`
- `NQAI_API_KEY` env set (operator-issued bearer)
- `ELEVENLABS_API_KEY` env set (must have credits)
- A GPU box for Whisper-large-v3 (~3 GB model download, ~16 GB VRAM)

Run:

```bash
PYTHONPATH=src python scripts/eval_run.py \
    --test-set v0.1-mini \
    --systems nqai elevenlabs \
    --nqai-voice neeko-v01 \
    --elevenlabs-voice 21m00Tcm4TlvDq8ikWAM \
    --nqai-model-id nqai-voxcpm2-tr-hd \
    --metrics whisper_wer \
    --output-dir experiments \
    --slug nqai-vs-elevenlabs-baseline
```

The CLI creates `experiments/2026-05-25-nqai-vs-elevenlabs-baseline/`
with `REPORT.md` (the comparison table) and `raw.jsonl` (one row per
system × voice × sentence × metric).

## What "first numbers" should look like

After running the above against a real GPU + real ElevenLabs key, the
report should contain a summary table of this shape:

```
| System       | Voice                  | whisper_wer (↓) | n_nan |
|---|---|---|---|
| nqai         | neeko-v01              | 0.XXX  (n=10)   | 0     |
| elevenlabs   | 21m00Tcm4TlvDq8ikWAM   | 0.XXX  (n=10)   | 0     |
```

A delta of ≤ 0.02 WER between systems means we're competitive in TR
on this slice; bigger gaps either way are the first real datapoint
the "premium TR TTS" strategy actually has.

## Why this is empty

PR #3 deliberately ships scaffolding only. The four-cell table the
MLOps audit asks for is operator work — needs a GPU box, real API
keys, and someone willing to read the audio for a final sanity check.
The harness is the part that's repeatable; the run isn't.

When the operator does run it, the output drops into
`experiments/YYYY-MM-DD-<slug>/` alongside this scaffold; the scaffold
README stays as the "how-to-reproduce" trail.

## Adding new metrics / systems

- New metric → create `src/eval/metrics/<name>.py` following the
  `WhisperWERMetric` template. The class needs a `name` attribute and
  a `.score(pcm_int16, sample_rate, *, reference_text)` method
  returning a `MetricResult`. Register it from `scripts/eval_run.py`.
- New system → create `src/eval/systems/<name>.py` following
  `NQAISystem` / `ElevenLabsSystem`. Same pattern: `name` attribute
  + `.synthesize(text, voice_id)` returning `SystemOutput`. Register
  it from the CLI alongside the existing pair.

Tests for both go in `tests/test_eval_harness.py`. Stub-only — the
real-backend tests live in a separate integration suite gated on
env flags.
