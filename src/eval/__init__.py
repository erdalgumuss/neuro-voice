"""MLOps PR #3 — NQAI Voice eval harness.

The point of this package is to answer a single question with a
single command:

    "Given test set T, on system S (NQAI / ElevenLabs / MiniMax / ...),
    voice V, what are the WER + UTMOS + clipping + duration numbers?"

Architectural shape (protocol-based; concrete backends gated behind
runtime imports so the package always loads even when GPU / external
SDKs are missing):

    src/eval/
      dataset.py         — load test sentences from data/test-sets/
      metrics/           — `Metric` protocol + Whisper-TR-WER, UTMOSv2
      systems/           — `TTSSystem` protocol + NQAI, ElevenLabs adapters
      runner.py          — orchestrator (system × voice × sentence × metric)
      report.py          — markdown writer to experiments/<date>-<slug>/

The CLI lives at `scripts/eval_run.py` and is the only operator-facing
entry point.

Two design constraints driven by the audit:

1. Reproducibility — every report row records the model_id / preset /
   seed / hf_revision the NQAI system used, so a regression can be
   traced back via `engine_inputs` (PR #1).
2. Test isolation — concrete metric / system backends MUST NOT import
   heavy dependencies (torch, whisper) at module load. Anything that
   needs a 4 GB model download imports it inside the call path.
"""

from __future__ import annotations
