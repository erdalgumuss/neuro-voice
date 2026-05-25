"""MLOps PR #1 — reproducibility plumbing tests.

These tests do NOT run real VoxCPM2 inference (8 GB VRAM not available
in CI). They pin the **plumbing contract**: every piece of state that
must be identical for a reproducible inference is either threaded to
the engine or recorded in `usage_records.engine_inputs`.

Three invariants pinned:

1. `seed` from the request reaches `torch.manual_seed` + `cuda.manual_seed_all`
   inside `VoxCPM2Engine.synthesize_stream`. Two calls with the same seed
   call the RNG with the same value.

2. `hf_revision` from the config flows into `from_pretrained(..., revision=)`
   when set, and is omitted when "main" (the unpinned default).

3. The worker pipeline persists `engine_inputs` on `usage_records` with
   every reproducibility-relevant input (model_id, hf_revision, preset,
   cfg/steps actually used, seed, voice_settings resolved, reference
   sha256, plus string-length proxies for the unsafe-to-persist
   pronunciation_dict / context fields).

If these three break, reproducibility is theatre — the API accepts a
seed but the engine doesn't honor it, or the audit trail can't tell
you what changed between two diverging outputs.
"""

from __future__ import annotations

import sys
import types as _types
from unittest.mock import MagicMock, patch

import pytest

# Stub voxcpm so importing the engine module doesn't need the real package.
_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm_model = _types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = _types.ModuleType("voxcpm.model.voxcpm")


class _StubInner:
    sample_rate = 48000


class _StubModel:
    tts_model = _StubInner()

    def generate(self, **kwargs):
        # Engine post-processes whatever this returns; reproducibility
        # tests only care that `torch.manual_seed` was called with the
        # right value BEFORE generate, so the actual return is a stub.
        import numpy as np
        return np.zeros(1024, dtype=np.float32)


_fake_from_pretrained_calls: list[dict] = []


def _stub_from_pretrained(*args, **kwargs):
    _fake_from_pretrained_calls.append({"args": args, "kwargs": kwargs})
    return _StubModel()


_fake_voxcpm.VoxCPM = type("StubFactory", (), {
    "from_pretrained": staticmethod(_stub_from_pretrained),
})
_fake_voxcpm_model_voxcpm.LoRAConfig = object
sys.modules.setdefault("voxcpm", _fake_voxcpm)
sys.modules.setdefault("voxcpm.model", _fake_voxcpm_model)
sys.modules.setdefault("voxcpm.model.voxcpm", _fake_voxcpm_model_voxcpm)


# --------------------------------------------------------------------------- #
# Invariant 1 — seed flows to torch RNG
# --------------------------------------------------------------------------- #
def test_seed_plumbs_to_torch_manual_seed(tmp_path):
    """Same seed twice → torch.manual_seed called twice with the same value.
    Different seed → different value. If this breaks, the `seed` request
    field is documentation, not behavior."""
    for mod in list(sys.modules):
        if mod.startswith(("server", "worker", "db", "repos", "frontend",
                            "registry", "storage")):
            del sys.modules[mod]
    from worker.engine import VoxCPM2Engine

    engine = VoxCPM2Engine(model_id="stub/voxcpm2", device="cpu")

    # Create a fake reference audio file (engine checks is_file()).
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF" + b"\x00" * 60)

    # Voice + segment_sentences pass-through — we just need the
    # synthesize_stream path to reach the seed branch before bailing.
    class _V:
        voice_id = "test"
        adapter = None

    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = False

    with patch.dict(sys.modules, {"torch": fake_torch}), \
         patch.object(engine, "_load", lambda: None), \
         patch("worker.engine.normalize_text", return_value=""), \
         patch("worker.engine.segment_sentences", return_value=[]):
        list(engine.synthesize_stream(
            text="x", voice=_V(), reference_path=ref,
            request_meta={"seed": 42},
        ))
        list(engine.synthesize_stream(
            text="x", voice=_V(), reference_path=ref,
            request_meta={"seed": 42},
        ))
        list(engine.synthesize_stream(
            text="x", voice=_V(), reference_path=ref,
            request_meta={"seed": 99},
        ))

    seed_calls = [c.args[0] for c in fake_torch.manual_seed.call_args_list]
    assert seed_calls == [42, 42, 99], (
        f"expected seed plumbing [42, 42, 99]; got {seed_calls}. "
        "If seeds drop, the API's `seed` field is theatre."
    )


def test_no_seed_means_no_torch_manual_seed(tmp_path):
    """When request omits `seed`, we MUST NOT call torch.manual_seed —
    otherwise we lock the RNG to whatever default we picked and the
    engine becomes secretly deterministic for clients who didn't ask
    for it (degrades variety in long-running playback)."""
    for mod in list(sys.modules):
        if mod.startswith(("server", "worker", "db", "repos", "frontend",
                            "registry", "storage")):
            del sys.modules[mod]
    from worker.engine import VoxCPM2Engine

    engine = VoxCPM2Engine(model_id="stub/voxcpm2", device="cpu")

    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF" + b"\x00" * 60)

    class _V:
        voice_id = "test"
        adapter = None

    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = False

    with patch.dict(sys.modules, {"torch": fake_torch}), \
         patch.object(engine, "_load", lambda: None), \
         patch("worker.engine.normalize_text", return_value=""), \
         patch("worker.engine.segment_sentences", return_value=[]):
        # No request_meta at all.
        list(engine.synthesize_stream(
            text="x", voice=_V(), reference_path=ref,
        ))
        # request_meta with seed=None.
        list(engine.synthesize_stream(
            text="x", voice=_V(), reference_path=ref,
            request_meta={"seed": None},
        ))

    assert fake_torch.manual_seed.call_count == 0, (
        "torch.manual_seed must NOT be called when the client did not "
        "request a seed — would secretly lock determinism."
    )


# --------------------------------------------------------------------------- #
# Invariant 2 — hf_revision flows to from_pretrained
# --------------------------------------------------------------------------- #
def test_hf_revision_threaded_when_pinned(tmp_path):
    """Pinned revision → `from_pretrained(model_id, revision=...)`.
    Unpinned ("main") → revision NOT in kwargs (use HF default).
    Two engines built against different revisions both record the
    value they were asked for.

    Patches `voxcpm.VoxCPM.from_pretrained` directly at test time so
    test-order doesn't matter (other tests in the suite stub voxcpm
    via `sys.modules.setdefault` whose first-write-wins semantics
    would otherwise pin the wrong factory)."""
    for mod in list(sys.modules):
        if mod.startswith(("server", "worker", "db", "repos", "frontend",
                            "registry", "storage")):
            del sys.modules[mod]
    from worker.engine import VoxCPM2Engine

    pinned = VoxCPM2Engine(
        model_id="openbmb/VoxCPM2",
        device="cpu",
        hf_revision="abc123def456",
    )
    assert pinned.hf_revision == "abc123def456"
    assert pinned.model_id == "openbmb/VoxCPM2"

    unpinned = VoxCPM2Engine(
        model_id="openbmb/VoxCPM2",
        device="cpu",
        hf_revision="main",
    )
    assert unpinned.hf_revision == "main"

    captured: list[dict] = []

    def _spy_from_pretrained(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        return _StubModel()

    # `from voxcpm import VoxCPM` runs INSIDE `_model_for_adapter`
    # (lazy import), so we patch the source module's attribute.
    with patch("voxcpm.VoxCPM.from_pretrained", side_effect=_spy_from_pretrained):
        pinned._model_for_adapter(None, voice_id="test")
        unpinned._model_for_adapter(None, voice_id="test")

    assert len(captured) == 2, (
        f"expected 2 from_pretrained calls; got {len(captured)}"
    )
    pinned_kwargs = captured[0]["kwargs"]
    unpinned_kwargs = captured[1]["kwargs"]
    assert pinned_kwargs.get("revision") == "abc123def456", (
        f"pinned engine must pass revision kwarg; got kwargs={pinned_kwargs}"
    )
    assert "revision" not in unpinned_kwargs, (
        "unpinned engine (revision='main') MUST NOT pass `revision=` so "
        "HuggingFace uses its repo default. Currently passing: "
        f"revision={unpinned_kwargs.get('revision')!r}"
    )


# --------------------------------------------------------------------------- #
# Invariant 3 — engine_inputs persisted on usage_records (shape + content)
# --------------------------------------------------------------------------- #
@pytest.fixture
async def setup_db(tmp_path, monkeypatch):
    db_file = tmp_path / "repro.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("NQAI_DATABASE_URL", db_url)
    monkeypatch.setenv("NQAI_JWT_SECRET", "test-secret-must-be-at-least-32-chars-long")

    for mod in list(sys.modules):
        if mod.startswith(("server", "worker", "db", "repos", "frontend",
                            "registry", "storage")):
            del sys.modules[mod]

    from db import AsyncSessionLocal, init_models_for_tests
    from db.models import ApiKey, Tenant, Voice

    await init_models_for_tests(db_url)
    async with AsyncSessionLocal() as s:
        tenant = Tenant(slug="repro", display_name="R")
        s.add(tenant)
        await s.flush()
        api_key = ApiKey(
            tenant_id=tenant.id,
            prefix="nqai_dev_zzzzzzzzzzzzzz",
            secret_hash="x",
            scopes=["tts:read", "tts:write"],
        )
        s.add(api_key)
        await s.flush()
        ref_path = tmp_path / "ref.wav"
        ref_path.write_bytes(b"\x00" * 256)
        voice = Voice(
            owner_tenant_id=tenant.id,
            voice_id="repro-voice",
            display_name="Repro",
            reference_uri=f"file://{ref_path}",
            reference_sha256="deadbeef" * 8,
            reference_seconds=10.0,
            source="placeholder",
            license="internal-placeholder",
        )
        s.add(voice)
        await s.commit()
        return {
            "tenant_id": tenant.id,
            "api_key_id": api_key.id,
            "voice_id": voice.voice_id,
            "ref_path": ref_path,
            "artifact_dir": tmp_path / "artifacts",
        }


class _RecordingStubEngine:
    """Mimics BaseSynthEngine. Exposes `model_id` + `hf_revision`
    properties so the pipeline can snapshot them."""

    sample_rate = 48000
    model_id = "stub/voxcpm2"
    hf_revision = "pinned-sha-aaa"

    def warmup(self) -> None:
        pass

    def synthesize_stream(
        self, *, text, voice, reference_path, language_id="tr",
        engine_overrides=None, request_meta=None,
    ):
        from dataclasses import dataclass
        @dataclass
        class _C:
            pcm_int16: bytes
            sample_rate: int = 48000
            sentence_index: int = 0
            sentence_text: str = ""
            elapsed_ms: float = 1.0
        yield _C(pcm_int16=b"\x00\x00" * 1024, sentence_text=text.strip())


async def test_engine_inputs_persisted_with_reproducibility_fields(setup_db):
    """E2E plumbing — the worker pipeline must persist `engine_inputs`
    on `usage_records` with at least: model_id, hf_revision, preset_id,
    cfg_value, inference_timesteps, seed, voice_settings, reference_sha256,
    plus the three string-length proxies (pronunciation_dict_size,
    previous_text_len, next_text_len)."""
    import fakeredis.aioredis

    from server.queue import TtsJobPayload
    from worker.pipeline import process_one_job
    setup = setup_db

    redis = fakeredis.aioredis.FakeRedis()
    engine = _RecordingStubEngine()

    import uuid

    from db import AsyncSessionLocal
    from repos import IdempotencyRepo
    job = TtsJobPayload(
        request_id=str(uuid.uuid4()),
        tenant_id=str(setup["tenant_id"]),
        api_key_id=str(setup["api_key_id"]),
        voice_id=setup["voice_id"],
        text="merhaba",
        seed=42,
        previous_text="önceki cümle",
        next_text="sonraki cümle",
        pronunciation_dict={"NQAI": "en-ku-a-ay", "X": "iks"},
        voice_settings={"stability": 0.6, "similarity_boost": 0.8, "speed": 1.0},
        model_id="nqai-voxcpm2-tr-hd",
    )
    async with AsyncSessionLocal() as s:
        await IdempotencyRepo(s, setup["tenant_id"]).reserve(
            request_id=uuid.UUID(job.request_id),
            api_key_id=setup["api_key_id"],
            request_hash="h",
        )
        await s.commit()

    setup["artifact_dir"].mkdir(parents=True, exist_ok=True)

    async def _archive(rid, pcm, sr):
        path = setup["artifact_dir"] / f"{rid}.pcm"
        path.write_bytes(pcm)
        return f"file://{path}"

    def _resolve(_uri):
        return setup["ref_path"]

    await process_one_job(
        job, redis=redis, engine=engine,
        resolve_reference=_resolve, archive_to_r2=_archive,
    )

    from repos import UsageRepo
    async with AsyncSessionLocal() as s:
        rows = await UsageRepo(s, setup["tenant_id"]).recent(limit=1)
    assert rows
    ei = rows[0].engine_inputs
    assert ei is not None, "engine_inputs MUST be persisted; got NULL"

    # Reproducibility-critical fields.
    assert ei["model_id"] == "stub/voxcpm2"
    assert ei["hf_revision"] == "pinned-sha-aaa"
    assert ei["preset_id"] == "nqai-voxcpm2-tr-hd"
    assert ei["cfg_value"] is not None
    assert ei["inference_timesteps"] is not None
    assert ei["seed"] == 42
    assert ei["reference_sha256"] == "deadbeef" * 8

    # Voice settings actually applied (not raw request — resolved).
    assert ei["voice_settings"]["stability"] == 0.6
    assert ei["voice_settings"]["similarity_boost"] == 0.8

    # Size proxies, not the raw content.
    assert ei["pronunciation_dict_size"] == 2
    assert ei["previous_text_len"] == len("önceki cümle")
    assert ei["next_text_len"] == len("sonraki cümle")


async def test_engine_inputs_persisted_even_without_dalga_26_fields(setup_db):
    """Bare job (no seed / context / pron_dict) — engine_inputs still
    written with the reproducibility-critical fields populated and the
    optional ones zeroed/None. Eliminates the silent-NULL failure mode
    where pre-PR-1 jobs would have nothing recorded."""
    import uuid

    import fakeredis.aioredis

    from db import AsyncSessionLocal
    from repos import IdempotencyRepo, UsageRepo
    from server.queue import TtsJobPayload
    from worker.pipeline import process_one_job
    setup = setup_db

    redis = fakeredis.aioredis.FakeRedis()
    job = TtsJobPayload(
        request_id=str(uuid.uuid4()),
        tenant_id=str(setup["tenant_id"]),
        api_key_id=str(setup["api_key_id"]),
        voice_id=setup["voice_id"],
        text="x",
    )
    async with AsyncSessionLocal() as s:
        await IdempotencyRepo(s, setup["tenant_id"]).reserve(
            request_id=uuid.UUID(job.request_id),
            api_key_id=setup["api_key_id"],
            request_hash="h",
        )
        await s.commit()
    setup["artifact_dir"].mkdir(parents=True, exist_ok=True)

    async def _archive(rid, pcm, sr):
        p = setup["artifact_dir"] / f"{rid}.pcm"
        p.write_bytes(pcm)
        return f"file://{p}"

    await process_one_job(
        job, redis=redis, engine=_RecordingStubEngine(),
        resolve_reference=lambda _: setup["ref_path"],
        archive_to_r2=_archive,
    )

    async with AsyncSessionLocal() as s:
        rows = await UsageRepo(s, setup["tenant_id"]).recent(limit=1)
    ei = rows[0].engine_inputs
    assert ei is not None
    assert ei["seed"] is None
    assert ei["pronunciation_dict_size"] == 0
    assert ei["previous_text_len"] == 0
    assert ei["next_text_len"] == 0
    # The reproducibility-critical fields ARE populated even when the
    # client didn't send Dalga 2.6 surface.
    assert ei["model_id"] == "stub/voxcpm2"
    assert ei["hf_revision"] == "pinned-sha-aaa"
    assert ei["reference_sha256"] is not None
