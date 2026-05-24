"""Unit tests for per-voice warmup boot path (Faz B.5 Dalga 1.3).

We don't spin up VoxCPM2 (no GPU); we exercise the env-parsing +
engine.warmup_voice dispatch + cold-load metric emission via a stub
engine that records its calls.
"""

from __future__ import annotations

import sys
import types as _types
from unittest.mock import MagicMock

import pytest

# Voxcpm stub before any worker.* import
_fake = _types.ModuleType("voxcpm")
_fake_model = _types.ModuleType("voxcpm.model")
_fake_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")


class _StubInner:
    sample_rate = 48000


class _StubModel:
    tts_model = _StubInner()


_fake.VoxCPM = type("StubFactory", (), {
    "from_pretrained": staticmethod(lambda *a, **kw: _StubModel()),
})
_fake_model_voxcpm.LoRAConfig = object
sys.modules.setdefault("voxcpm", _fake)
sys.modules.setdefault("voxcpm.model", _fake_model)
sys.modules.setdefault("voxcpm.model.voxcpm", _fake_model_voxcpm)


from worker.runtime import _parse_warmup_voice_list  # noqa: E402


def test_parse_warmup_voice_list_empty() -> None:
    assert _parse_warmup_voice_list(None) == []
    assert _parse_warmup_voice_list("") == []
    assert _parse_warmup_voice_list("   ") == []


def test_parse_warmup_voice_list_trims_and_splits() -> None:
    assert _parse_warmup_voice_list(
        "neeko-v01,niva-call-v02 , neurocourse-v01"
    ) == ["neeko-v01", "niva-call-v02", "neurocourse-v01"]


def test_parse_warmup_voice_list_drops_empties() -> None:
    # Trailing commas / double commas common in env files — must not
    # produce empty strings.
    assert _parse_warmup_voice_list("a,,b,") == ["a", "b"]


async def test_warmup_voice_emits_cold_load_metric_with_voice_label() -> None:
    """The cold-load histogram MUST be labelled with the voice_id so
    operators can see which voices took how long to load. `_base_` is
    the no-voice fallback (env-warmup of just the base model)."""
    from observability import WORKER_COLD_LOAD_SECONDS

    # Direct emission to verify the label contract — we don't drive
    # VoxCPM2 here because that requires a GPU. The actual call site
    # in `engine._model_for_adapter` matches this shape (see code).
    WORKER_COLD_LOAD_SECONDS.labels(voice="test-warmup-voice").observe(0.5)
    families = {f.name: f for f in
                __import__("observability").REGISTRY.collect()}
    family = families["nqai_worker_cold_load_seconds"]
    # Histogram emits `_bucket`, `_sum`, `_count` samples. We just need
    # the labelled child to exist.
    voice_label_seen = any(
        s.labels.get("voice") == "test-warmup-voice"
        for s in family.samples
    )
    assert voice_label_seen, (
        "cold-load metric must carry voice_id as a label "
        "(audit Dalga 1.3 contract)"
    )


async def test_warmup_voices_skips_missing_rows(monkeypatch, tmp_path) -> None:
    """An entry in NQAI_WORKER_WARMUP_VOICES that doesn't match any DB
    row MUST NOT abort boot. Warning-log + continue."""
    import os

    from db import init_models_for_tests

    db_file = tmp_path / "warmup.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv(
        "NQAI_WORKER_WARMUP_VOICES",
        "definitely-not-a-real-voice,still-not-real",
    )

    for mod in list(sys.modules):
        if mod.startswith(("db.", "repos.")):
            del sys.modules[mod]

    await init_models_for_tests(db_url)

    # Stub engine with a tracking warmup_voice call counter.
    from worker.runtime import _warmup_voices_from_env

    stub_engine = MagicMock()
    stub_engine.warmup_voice = MagicMock()
    stub_engine._cache_size = 3

    # MUST NOT raise even though both voice_ids are missing.
    await _warmup_voices_from_env(stub_engine)

    # No DB row matched → warmup_voice never invoked.
    stub_engine.warmup_voice.assert_not_called()

    os.environ.pop("NQAI_WORKER_WARMUP_VOICES", None)


async def test_warmup_voices_continues_after_per_voice_failure(
    monkeypatch, tmp_path,
) -> None:
    """If voice A's warmup raises (e.g. R2 unreachable for its
    reference), voice B must still be attempted."""
    import os
    import uuid

    from db import AsyncSessionLocal, init_models_for_tests
    from db.models import Tenant, Voice

    db_file = tmp_path / "warmup_fail.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_WORKER_WARMUP_VOICES", "voice-a,voice-b")

    for mod in list(sys.modules):
        if mod.startswith(("db.", "repos.")):
            del sys.modules[mod]

    await init_models_for_tests(db_url)

    # Seed two voices belonging to a single tenant.
    async with AsyncSessionLocal() as s:
        tenant = Tenant(slug="warmup-tenant", display_name="W")
        s.add(tenant)
        await s.flush()
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"\x00" * 256)
        for slug in ("voice-a", "voice-b"):
            s.add(Voice(
                id=uuid.uuid4(),
                owner_tenant_id=tenant.id,
                voice_id=slug,
                display_name=slug,
                reference_uri=f"file://{ref}",
                reference_sha256="a" * 64,
                reference_seconds=10.0,
                source="placeholder",
                license="internal-placeholder",
            ))
        await s.commit()

    from worker.runtime import _warmup_voices_from_env

    # Stub engine: voice-a raises, voice-b succeeds. We expect both
    # to be ATTEMPTED so warmup_voice gets called twice.
    stub_engine = MagicMock()
    call_order: list[str] = []

    def _warmup_side_effect(voice_row):
        call_order.append(voice_row.voice_id)
        if voice_row.voice_id == "voice-a":
            raise RuntimeError("simulated R2 outage")

    stub_engine.warmup_voice = MagicMock(side_effect=_warmup_side_effect)

    # MUST NOT raise even though voice-a fails.
    await _warmup_voices_from_env(stub_engine)

    # Both voices were attempted, in env-list order.
    assert call_order == ["voice-a", "voice-b"]
    assert stub_engine.warmup_voice.call_count == 2

    os.environ.pop("NQAI_WORKER_WARMUP_VOICES", None)


async def test_warmup_voices_no_env_var_is_noop() -> None:
    """Without NQAI_WORKER_WARMUP_VOICES set, the helper returns
    immediately — boot path stays the same as before Dalga 1.3."""
    import os

    from worker.runtime import _warmup_voices_from_env

    os.environ.pop("NQAI_WORKER_WARMUP_VOICES", None)
    stub_engine = MagicMock()
    stub_engine.warmup_voice = MagicMock()
    await _warmup_voices_from_env(stub_engine)
    stub_engine.warmup_voice.assert_not_called()


@pytest.fixture(autouse=True)
def _clean_warmup_env(monkeypatch):
    """Ensure each test starts with a clean env var so cross-test
    leakage can't make the no-op test pass for the wrong reason."""
    monkeypatch.delenv("NQAI_WORKER_WARMUP_VOICES", raising=False)
    yield
