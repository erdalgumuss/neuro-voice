"""LoRA cache LRU eviction — bounded VRAM contract.

Faz A audit F1 (docs/audit/faz-a-mlops-audit.md §4): the engine used to
hold an unbounded dict of VoxCPM2 instances keyed by (model_id, adapter).
On a single GPU that ran out of memory after a handful of voices. This
test pins the LRU semantics: `cache_size=N` keeps at most N distinct
models live; the (N+1)-th unique adapter evicts the least-recently-used
entry, and `empty_cache()` is invoked best-effort.

The test stubs `voxcpm.VoxCPM.from_pretrained` so it runs without a GPU
and without the real 4 GB model load.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# Stub voxcpm + voxcpm.model.voxcpm BEFORE importing engine. The engine
# does `from voxcpm.model.voxcpm import LoRAConfig` inside its config
# loader, so the test fakes that submodule path as well.
_fake_voxcpm = types.ModuleType("voxcpm")
_fake_voxcpm_model = types.ModuleType("voxcpm.model")
_fake_voxcpm_model_voxcpm = types.ModuleType("voxcpm.model.voxcpm")


class _StubInner:
    sample_rate = 48000


class _StubModel:
    """Counts instantiations so we can assert cache hits/misses."""

    instance_count = 0

    def __init__(self) -> None:
        type(self).instance_count += 1
        self.tts_model = _StubInner()

    def generate(self, *a, **kw):
        import numpy as np

        return np.zeros(self.tts_model.sample_rate, dtype=np.float32)


class _StubFactory:
    @staticmethod
    def from_pretrained(model_id, **kwargs):
        return _StubModel()


class _StubLoRAConfig:
    """Minimal LoRAConfig double — engine just needs an object back."""

    def __init__(self, **_kwargs):
        self.kwargs = _kwargs


_fake_voxcpm.VoxCPM = _StubFactory
_fake_voxcpm_model_voxcpm.LoRAConfig = _StubLoRAConfig
sys.modules["voxcpm"] = _fake_voxcpm
sys.modules["voxcpm.model"] = _fake_voxcpm_model
sys.modules["voxcpm.model.voxcpm"] = _fake_voxcpm_model_voxcpm


@pytest.fixture(autouse=True)
def _reset_stub_counter():
    _StubModel.instance_count = 0
    yield


def _make_engine(cache_size: int = 3):
    # Late import so the voxcpm stub registers first.
    for mod_name in list(sys.modules):
        if mod_name.startswith("worker.engine"):
            del sys.modules[mod_name]
    from worker.engine import VoxCPM2Engine

    return VoxCPM2Engine(model_id="stub/voxcpm2", device="cpu", cache_size=cache_size)


def _adapter(tmp_path: Path, name: str):
    """Build a usable LoRAAdapterSpec pointing at a tmp directory + config."""
    from worker.engine import LoRAAdapterSpec

    adir = tmp_path / name
    adir.mkdir(parents=True, exist_ok=True)
    cfg = adir / "lora_config.json"
    cfg.write_text('{"lora_config": {}}', encoding="utf-8")
    # The stub doesn't actually parse the config — it just needs the path
    # to exist. We patch _read_lora_config to skip the voxcpm import.
    return LoRAAdapterSpec(path=adir, config_path=cfg)


# NB: engine._read_lora_config will import LoRAConfig from voxcpm.model.voxcpm
# which is now stubbed above — no per-test monkeypatch needed.


def test_first_load_caches_base_model(tmp_path):
    eng = _make_engine(cache_size=3)
    eng._model_for_adapter(None)
    assert _StubModel.instance_count == 1
    assert len(eng._models) == 1


def test_repeat_lookup_is_cache_hit(tmp_path):
    eng = _make_engine(cache_size=3)
    eng._model_for_adapter(None)
    eng._model_for_adapter(None)
    eng._model_for_adapter(None)
    assert _StubModel.instance_count == 1
    assert eng._evictions_total == 0


def test_distinct_adapters_each_load_once(tmp_path):
    eng = _make_engine(cache_size=3)
    a = _adapter(tmp_path, "a")
    b = _adapter(tmp_path, "b")
    eng._model_for_adapter(a)
    eng._model_for_adapter(b)
    eng._model_for_adapter(a)  # cache hit
    assert _StubModel.instance_count == 2
    assert eng._evictions_total == 0


def test_lru_eviction_at_capacity(tmp_path):
    eng = _make_engine(cache_size=2)
    a = _adapter(tmp_path, "a")
    b = _adapter(tmp_path, "b")
    c = _adapter(tmp_path, "c")

    eng._model_for_adapter(a)  # cache: [a]
    eng._model_for_adapter(b)  # cache: [a, b]
    assert eng._evictions_total == 0
    assert len(eng._models) == 2

    # Loading c with cache full → evict a (LRU)
    eng._model_for_adapter(c)
    assert eng._evictions_total == 1
    assert len(eng._models) == 2

    # a should reload (new instance), b should still be cached
    base_count = _StubModel.instance_count
    eng._model_for_adapter(b)  # cache hit, no new instance
    assert _StubModel.instance_count == base_count
    eng._model_for_adapter(a)  # cache miss, new instance
    assert _StubModel.instance_count == base_count + 1


def test_access_promotes_lru_position(tmp_path):
    """Reading an adapter should refresh its LRU rank so it survives the
    next eviction."""
    eng = _make_engine(cache_size=2)
    a = _adapter(tmp_path, "a")
    b = _adapter(tmp_path, "b")
    c = _adapter(tmp_path, "c")

    eng._model_for_adapter(a)
    eng._model_for_adapter(b)
    eng._model_for_adapter(a)  # promotes a, b becomes LRU

    eng._model_for_adapter(c)  # should evict b, not a

    assert eng._evictions_total == 1
    keys = [k[1] for k in eng._models]  # adapter cache_key tuples
    # a still cached, c just loaded, b gone
    a_key = (str(a.path), str(a.config_path))
    c_key = (str(c.path), str(c.config_path))
    b_key = (str(b.path), str(b.config_path))
    assert a_key in keys
    assert c_key in keys
    assert b_key not in keys


def test_cache_size_zero_rejected(tmp_path):
    with pytest.raises(ValueError):
        _make_engine(cache_size=0)


def test_default_cache_size_from_env(monkeypatch):
    monkeypatch.setenv("NQAI_LORA_CACHE_SIZE", "7")
    # Force re-import so module-level constant picks up the env.
    for mod_name in list(sys.modules):
        if mod_name.startswith("worker.engine"):
            del sys.modules[mod_name]
    from worker.engine import DEFAULT_LORA_CACHE_SIZE

    assert DEFAULT_LORA_CACHE_SIZE == 7
