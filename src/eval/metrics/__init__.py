"""MLOps PR #3 — metric backend protocol + registry.

A `Metric` scores a synthesized audio clip against the expected
sentence. Implementations are isolated behind a protocol so the
harness can:

  * Run with a stub metric in tests (no model download).
  * Run with a real metric on a GPU box (Whisper-TR / UTMOSv2).
  * Mix and match — e.g. score WER for every clip but UTMOSv2 only
    on a sample, because UTMOSv2 is the slowest backend.

The runner orchestrates which metrics fire per clip; this module
just defines what a metric IS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MetricResult:
    """One metric × one clip = one score row. `direction` is "higher"
    (UTMOSv2, SECS) or "lower" (WER) — the report writer uses it to
    color/sort columns sensibly."""

    metric_name: str
    score: float
    direction: str           # "higher" | "lower"
    detail: dict[str, object] | None = None  # backend-specific notes


class Metric(Protocol):
    """Backend contract. `score(clip_pcm, sample_rate, reference_text)`
    returns a `MetricResult`.

    Contract notes:
      * Inputs are int16 PCM bytes + sample rate. The backend resamples
        if it needs a different rate.
      * `reference_text` is the EXPECTED text (the test sentence). WER
        backends transcribe + diff; quality backends (UTMOSv2) ignore it.
      * Implementations MUST NOT mutate the input PCM.
      * Implementations MAY return a `score=nan` if the clip can't be
        scored (e.g. empty audio for WER). The report writer surfaces
        nan as "—" so it doesn't poison aggregates.
    """

    name: str

    def score(
        self,
        pcm_int16: bytes,
        sample_rate: int,
        *,
        reference_text: str,
    ) -> MetricResult: ...


# Convenience registry — populated by the concrete backends so the CLI
# can resolve "--metrics whisper_wer,utmosv2" by name.
_REGISTRY: dict[str, Metric] = {}


def register_metric(name: str, metric: Metric) -> None:
    """Idempotent — re-registering the same name replaces the previous
    instance, which is the right behavior for unit tests that swap a
    stub in via fixture."""
    _REGISTRY[name] = metric


def get_metric(name: str) -> Metric:
    if name not in _REGISTRY:
        raise KeyError(
            f"metric '{name}' not registered. Known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def list_metrics() -> list[str]:
    return sorted(_REGISTRY)
