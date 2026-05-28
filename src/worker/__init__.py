"""NeuroVoice — GPU worker process.

This package owns synthesis: VoxCPM2 inference + sentence-chunked
streaming + result-stream publication + idempotency completion + usage
recording. The gateway (`src/server/`) does **none** of this — it
authenticates, validates, enqueues a job, and streams whatever chunks
the worker XADDs back.

Import direction (enforced by review, no static check yet):
  * worker  → frontend, registry (enroll-only path uses it), repos, db,
              storage, audio, server.queue (wire schemas only)
  * server  → audio, server.queue, repos, db, storage, frontend
  * worker → server.*: forbidden EXCEPT for `server.queue` (which is a
    leaf wire-format module — see worker-process.md §1).
  * server → worker.*: forbidden after step 6 (sync proxy cutover).

Process entry point: `python -m worker.main`. Spec:
[docs/architecture/worker-process.md](../../docs/architecture/worker-process.md).
"""
