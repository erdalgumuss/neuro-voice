"""Sanity tests for the shipped Grafana dashboard + Prometheus alerts.

These don't simulate Grafana / Prometheus — they're contract tests
that catch the cheap mistakes:

  * Dashboard JSON parses and has the expected schema fields.
  * Every PromQL `expr` references metric NAMES that exist in our
    registry. Catches renames / typos before they surface as empty
    panels on a live deploy.
  * Alert rules YAML parses and follows the same metric-name rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from observability.metrics import REGISTRY

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_PATH = REPO_ROOT / "deploy" / "grafana" / "dashboards" / "nqai-voice.json"
ALERTS_PATH = REPO_ROOT / "deploy" / "prometheus" / "alerts.yml"


def _registered_metric_names() -> set[str]:
    """Every metric the registry currently exposes, in the form a
    PromQL expression would reference (counters keep their `_total`
    suffix; histograms have `_bucket`, `_count`, `_sum` derivatives;
    gauges are the bare name)."""
    names: set[str] = set()
    for family in REGISTRY.collect():
        base = family.name
        if family.type == "counter":
            names.add(base if base.endswith("_total") else f"{base}_total")
        elif family.type == "histogram":
            names.update({base, f"{base}_bucket", f"{base}_count", f"{base}_sum"})
        else:  # gauge / summary / etc.
            names.add(base)
    return names


_METRIC_NAME_RE = re.compile(r"\bnqai_[a-z0-9_]+\b")


def _extract_metric_refs(text: str) -> set[str]:
    return set(_METRIC_NAME_RE.findall(text))


def test_dashboard_json_parses() -> None:
    data = json.loads(DASHBOARD_PATH.read_text())
    assert data["uid"] == "nqai-voice-overview"
    assert data["schemaVersion"] >= 30
    assert isinstance(data["panels"], list) and len(data["panels"]) > 0


def test_dashboard_panel_targets_have_expr() -> None:
    data = json.loads(DASHBOARD_PATH.read_text())
    for panel in data["panels"]:
        targets = panel.get("targets") or []
        assert targets, f"panel '{panel.get('title')}' has no targets"
        for t in targets:
            assert t.get("expr"), (
                f"panel '{panel.get('title')}' target {t.get('refId')} "
                f"missing expr"
            )


def test_dashboard_metric_names_are_registered() -> None:
    """Every nqai_* metric referenced in a panel must exist in our
    registry — catches typos / renamed metrics before they hit prod."""
    data = json.loads(DASHBOARD_PATH.read_text())
    registered = _registered_metric_names()
    dashboard_text = json.dumps(data)
    referenced = _extract_metric_refs(dashboard_text)
    unknown = referenced - registered
    assert not unknown, (
        f"dashboard references metric names not in REGISTRY: {sorted(unknown)}"
    )


def test_alerts_yaml_parses_and_metrics_are_registered() -> None:
    yaml_text = ALERTS_PATH.read_text()
    # Don't pull PyYAML just for a smoke test — a regex pass over the
    # `expr:` lines is enough to catch metric-name typos. The YAML
    # itself is validated by anyone who tries to load it into
    # Prometheus (which is the only consumer).
    expr_blocks = re.findall(r"expr:\s*\|?\s*([^\n]+(?:\n {10,}[^\n]+)*)", yaml_text)
    assert expr_blocks, "alerts.yml has no expr: blocks — file is empty?"

    registered = _registered_metric_names()
    referenced: set[str] = set()
    for block in expr_blocks:
        referenced |= _extract_metric_refs(block)
    unknown = referenced - registered
    assert not unknown, (
        f"alerts.yml references metric names not in REGISTRY: "
        f"{sorted(unknown)}"
    )


def test_dashboard_covers_critical_metrics() -> None:
    """Smoke check: the dashboard must reference each of these — they
    are the load-bearing metrics for the on-call experience. If a
    refactor removes one of these panels we want to know."""
    must_reference = (
        "nqai_tts_requests_total",
        "nqai_tts_errors_total",
        "nqai_tts_first_audio_seconds_bucket",
        "nqai_tts_gateway_first_byte_seconds_bucket",
        "nqai_tts_inference_seconds_bucket",
        "nqai_queue_depth",
        "nqai_worker_count",
        "nqai_worker_capacity_total",
        "nqai_worker_inflight_total",
        "nqai_worker_dlq_total",
    )
    data = json.dumps(json.loads(DASHBOARD_PATH.read_text()))
    missing = [m for m in must_reference if m not in data]
    assert not missing, f"dashboard missing critical metric panels: {missing}"


@pytest.mark.parametrize(
    "alert_name",
    [
        "NqaiVoiceNoHealthyWorkers",
        "NqaiVoiceHighErrorRate",
        "NqaiVoiceDlqGrowing",
        "NqaiVoiceQueueBacklogHigh",
        "NqaiVoiceBackpressureRate",
        "NqaiVoiceFirstAudioP95High",
        "NqaiVoiceGatewayTransportOverhead",
    ],
)
def test_alert_present(alert_name: str) -> None:
    yaml_text = ALERTS_PATH.read_text()
    assert alert_name in yaml_text, (
        f"alert '{alert_name}' missing from alerts.yml — "
        "removing alerts requires a decision-log entry"
    )
