"""Smoke tests for src/worker/ package surface.

The skeleton checks (package imports, entry point exists) now live
here; functional checks for the real consumer/runtime are in
`test_worker_consumer.py` and `test_worker_runtime.py`."""

from __future__ import annotations

import asyncio
import sys
import types as _types


def test_worker_package_imports_cleanly():
    """Worker package must be import-safe in any environment — no
    voxcpm/torch/CUDA touches at import time (those land lazily when
    `engine.warmup()` is actually called)."""
    import worker  # noqa: F401
    import worker.consumer  # noqa: F401
    import worker.main  # noqa: F401
    import worker.pipeline  # noqa: F401
    import worker.runtime  # noqa: F401

    assert callable(worker.main.run)


def test_worker_main_exits_zero_on_stop_event(monkeypatch):
    """`run()` must boot, install signal handlers, run the consumer
    until stop_event fires, then exit 0.

    We mock the heavy bits (engine + redis + archive) so the test
    doesn't need a GPU or a real Redis."""
    # Stub voxcpm so the engine module imports.
    _fake = _types.ModuleType("voxcpm")
    _fake.VoxCPM = type("SF", (), {
        "from_pretrained": staticmethod(lambda *a, **kw: None),
    })
    sys.modules.setdefault("voxcpm", _fake)
    sys.modules.setdefault("voxcpm.model", _types.ModuleType("voxcpm.model"))
    m = _types.ModuleType("voxcpm.model.voxcpm")
    m.LoRAConfig = object
    sys.modules.setdefault("voxcpm.model.voxcpm", m)

    import fakeredis.aioredis
    fake_redis = fakeredis.aioredis.FakeRedis()

    class _StubEngine:
        sample_rate = 48000
        def warmup(self) -> None:
            pass

    async def fake_boot_worker(**_kw):
        return _StubEngine(), fake_redis, None

    import worker.main as main_mod
    import worker.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "boot_worker", fake_boot_worker)
    monkeypatch.setattr(main_mod, "boot_worker", fake_boot_worker)

    # Make the consumer exit fast — block_ms tiny + max_iterations.
    # The default run() blocks on stop_event; for this smoke we don't
    # want to fork a separate task for SIGTERM, so we monkey-patch
    # consumer to use max_iterations=1.
    from worker.consumer import WorkerConsumer
    original_run = WorkerConsumer.run

    async def bounded_run(self, *, max_iterations: int | None = None) -> None:
        return await original_run(self, max_iterations=1)

    monkeypatch.setattr(WorkerConsumer, "run", bounded_run)

    rc = main_mod.run()
    assert rc == 0


def test_worker_main_handles_boot_failure_with_nonzero_exit(monkeypatch):
    """If `boot_worker` raises (e.g. Redis unreachable), `run()` must
    log + exit with a non-zero code — never silently no-op."""
    async def boom(**_kw):
        raise RuntimeError("simulated boot failure")

    import worker.main as main_mod
    import worker.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "boot_worker", boom)
    monkeypatch.setattr(main_mod, "boot_worker", boom)

    rc = main_mod.run()
    assert rc != 0


def test_run_is_safe_under_keyboard_interrupt(monkeypatch):
    """`run()` catches KeyboardInterrupt cleanly so Ctrl-C → exit 0."""
    async def trip(**_kw):
        raise KeyboardInterrupt()

    import worker.main as main_mod
    import worker.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "boot_worker", trip)
    monkeypatch.setattr(main_mod, "boot_worker", trip)

    # KeyboardInterrupt during boot_worker propagates out of asyncio.run
    # and we want the outer wrapper to swallow it.
    rc = main_mod.run()
    assert rc == 0


def test_worker_package_init_does_not_import_voxcpm():
    """Regression guard — `import worker` must NEVER drag in voxcpm.
    A future refactor that pulls engine into worker/__init__.py would
    break CPU-only dev boxes; this test fails loudly when that happens.
    """
    # Drop any prior cache then re-import the package.
    for mod in list(sys.modules):
        if mod.startswith("worker") or mod == "voxcpm":
            del sys.modules[mod]
    import worker  # noqa: F401
    assert "voxcpm" not in sys.modules


# Pytest harness for asyncio in this file (no fixture needed; the run
# tests use sync run() and asyncio internally).
_ = asyncio  # silence ruff F401 for the unused-but-doc-purposeful import
