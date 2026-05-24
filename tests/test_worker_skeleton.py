"""Skeleton smoke test for src/worker/ (Faz B.1 step 2).

Confirms the package is importable and the entry point returns cleanly
without doing any GPU work or touching Redis/DB. Real consumer behaviour
arrives in step 4 (see worker-process.md §4-§5)."""

from __future__ import annotations


def test_worker_package_imports_cleanly():
    """The worker package must be import-safe in any environment —
    no voxcpm/torch/CUDA touches at module-import time. Step 4's
    consumer module will lazy-load the engine inside `run()`."""
    import worker  # noqa: F401
    import worker.main  # noqa: F401

    # Package surface stays minimal — only the entry point should be
    # exposed from worker.main right now. Anything heavier moves in
    # later steps with explicit assertions added then.
    assert callable(worker.main.run)


def test_worker_main_run_returns_zero():
    from worker.main import run

    assert run() == 0


def test_worker_main_is_executable_via_dash_m():
    """`python -m worker.main` must succeed. This is the canonical
    process entry point compose/k8s will invoke."""
    import pathlib
    import subprocess
    import sys

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"

    proc = subprocess.run(
        [sys.executable, "-m", "worker.main"],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(src_dir),
            "PATH": "/usr/bin:/bin",
        },
        check=False,
        timeout=10,
    )
    assert proc.returncode == 0, (
        f"worker.main exited {proc.returncode}\nstderr:\n{proc.stderr}"
    )
