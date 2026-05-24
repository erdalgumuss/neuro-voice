# AGENTS.md — Codex Guide for `neeko-voice`

This file is the Codex-facing operating guide for this repository. It is intentionally
more operational than `CLAUDE.md`: use it to make safe code changes, reviews, audits,
and phase work without losing the architectural thread.

## Project Identity

`neeko-voice` is the NQAI Turkish voice platform: multi-tenant TTS API gateway,
Turkish text frontend, VoxCPM2 inference, voice catalog, Redis Streams async jobs,
Postgres data plane, and R2-compatible object storage.

The product direction is not generic TTS. The premium target is narrow:
Turkish, character voice, child-directed/storytelling/call-center quality, with
NQAI shared voice infrastructure for NEEKO, NIVA, NeuroCourse, and NARO.

## Canonical Context

Read these before changing architecture:

- `docs/architecture/scale-roadmap.md` — canonical v1.0 architecture and phases.
- `docs/architecture/worker-process.md` — Faz B.1 worker split spec.
- `docs/architecture/streaming-protocol.md` — gateway/client streaming protocol.
- `docs/architecture/data-model.md` — Postgres schema and tenant rules.
- `docs/architecture/observability.md` — Faz C metrics/tracing target.
- `docs/decisions/README.md` — decision log. New architecture decisions go here.
- `docs/research/04-latency-80-100ms-strategy.md` — latency strategy for B.1.5+.
- `CLAUDE.md` — existing repo discipline and historical context.

If a requested change conflicts with `scale-roadmap.md`, add/update a decision-log row
in the same change or call it out clearly.

## Current Architecture Mental Model

The desired production shape is:

```text
Client
  -> FastAPI gateway
  -> Auth / tenant / voice access / idempotency / queue submit
  -> Redis Streams job queue
  -> GPU worker pool
  -> VoxCPM2 + LoRA + reference audio
  -> Redis result stream + R2 artifact + Postgres usage/idempotency
  -> Gateway/client
```

Core services:

- `src/server/`: gateway/API/admin/auth/queue/result-stream proxy.
- `src/worker/`: GPU process, Redis consumer, inference pipeline, runtime wiring.
- `src/db/`: SQLAlchemy async models/session.
- `src/repos/`: tenant-scoped repository layer.
- `src/storage/`: R2/S3-compatible object storage and reference resolver.
- `src/frontend/`: Turkish normalization and sentence segmentation.
- `src/audio/`: shared PCM/WAV helpers.
- `migrations/`: Alembic forward-only migrations.

## Phase Boundary

Faz B.1 goal:

- Gateway no longer performs synthesis directly.
- Worker is a real separate process (`python -m worker.main`).
- Async job path works end-to-end: POST job -> Redis -> worker -> artifact/result -> GET complete.
- Sync `/v1/tts` becomes a compatibility proxy over the queue/result stream.
- At-least-once worker semantics are correct enough to scale horizontally.

Faz B.1.5 goal:

- True low-latency streaming path.
- First audio is published before full generation completes.
- Warm worker/cache discipline, latency instrumentation, and transport strategy are in place.
- The system can tell whether latency bottleneck is gateway, queue, worker, model, R2, or client transport.

Do not silently mix B.1 reliability work with B.1.5 latency work. If a latency change is needed,
label it explicitly and keep the B.1 correctness path stable.

## Non-Negotiable Invariants

### Tenant and Data Safety

- Tenant means account/workspace, not product.
- Every cross-tenant query must be tenant-scoped through repos.
- Cross-tenant voice access should return 404, not 403, to prevent existence leaks.
- `audit_log` is append-only.
- Async TTS is idempotent by `request_id`/`Idempotency-Key`; duplicate requests must not double-bill.
- Binary audio does not live in Postgres. Store metadata/state in Postgres, blobs in R2 or local dev files.

### Gateway/Worker Boundary

- Gateway should be CPU/I/O only: auth, validation, queue submit, result proxy, status.
- Worker owns GPU/model inference, reference resolution, adapter loading, result publication, artifact archiving.
- `worker -> server.main/auth/admin` imports are forbidden.
- `server -> worker.engine` is transitional only; remove it when sync proxy is complete.
- `server.queue` is allowed as shared wire schema until a dedicated shared package exists.

### Redis Streams Semantics

- Job stream is durable work.
- Result stream is per request: `nqai.tts.results.{request_id}`.
- `XACK` only after the job reaches a safe terminal point.
- Consumer group creation must not skip already-enqueued jobs.
- `XAUTOCLAIM` must recover stale pending work.
- Retries may happen; result chunks need sequence/attempt discipline or gateway dedupe.
- Poison jobs must not spin forever; transient failures must not be ACKed prematurely.

### Latency Guardrails

- Do not call a path "streaming" if it drains full generation before yielding chunks.
- Avoid `list(engine.synthesize_stream(...))` in a latency-sensitive path.
- R2 upload and DB finalization must not block first audio in the live streaming path.
- Warmup-on-boot should keep model cold-start out of user-visible latency.
- Reference audio and LoRA adapter access should be cache-aware and bounded.
- Measure latency as a waterfall: queue wait, worker pickup, reference resolve, adapter load,
  first model frame, first PCM, gateway first chunk, client TTFB, total inference, RTF.
- B.1.5 live TTS is WebRTC-first through LiveKit. WebSocket/HTTP streaming are compatibility
  or debug paths, not the primary low-latency media path.
- Live requests must use admission control from `nqai.worker.live.*` heartbeats; they should
  not sit in the durable Redis job queue waiting for capacity.

## Commands

Install:

```bash
pip install -e ".[dev]"
```

Common verification:

```bash
ruff check src tests
python -m pytest
```

Targeted tests are preferred while developing:

```bash
python -m pytest tests/test_worker_consumer.py -q
python -m pytest tests/test_worker_pipeline.py -q
python -m pytest tests/test_result_stream.py -q
python -m pytest tests/test_async_e2e.py -q
python -m pytest tests/test_api_smoke.py -q
```

Local stack:

```bash
docker compose -f docker-compose.dev.yaml up -d
docker compose -f docker-compose.dev.yaml exec gateway alembic upgrade head
docker compose -f docker-compose.dev.yaml exec gateway python scripts/seed_operator.py --email you@nqai.com
```

Server:

```bash
PYTHONPATH=src python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Worker:

```bash
PYTHONPATH=src python -m worker.main
```

Migrations:

```bash
alembic upgrade head
```

Alembic migrations are forward-only. Do not implement real downgrades unless the project policy changes.

## Coding Style

- Python 3.10-3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async.
- Prefer repository methods over direct ORM queries in request/worker paths.
- Keep blocking I/O (`boto3`, disk-heavy audio transforms, model warmup) out of the event loop via
  `asyncio.to_thread` or an equivalent boundary.
- Use typed dataclasses/Pydantic schemas for wire formats.
- Keep edits narrow. Avoid broad refactors while Claude Code or another agent is actively working.
- Preserve user/other-agent changes in a dirty worktree. Never revert unrelated edits.
- Do not commit raw voice data, `.env`, secrets, private contracts, API keys, or model weights.

## Packaging Note

`pyproject.toml` uses `src` layout. When worker packaging becomes production-critical, ensure package
discovery includes `worker*` alongside `server*`, `storage*`, `db*`, etc. Do not assume Docker can
import `worker` unless packaging and `PYTHONPATH` paths are verified.

## Documentation Rules

- Architecture/model/eval strategy changes require a `docs/decisions/README.md` row.
- Update docs in the same change when behavior or phase scope changes.
- Research docs are reference, not binding architecture, unless decision log or scale roadmap adopts them.
- For claims about benchmarks, cost, latency, or model quality, include source/date or mark the value as local observation.

## Review/Audit Posture

When asked to audit/review:

- Lead with findings, ordered by severity.
- Use file/line references.
- Separate B.1 correctness issues from B.1.5 latency/product issues.
- Prefer "blocks B.1", "blocks B.1.5", "production hardening", and "cleanup" labels.
- Do not spend audit effort proving tests are red unless the user asks; focus on architecture, failure modes,
  distribution, idempotency, latency, and operational fitness.

## Definition of Done

B.1 is done when:

- `python -m worker.main` boots a real worker.
- Gateway and worker are separately runnable.
- Gateway no longer needs model inference for `/v1/tts`.
- Async job E2E passes with real gateway + real worker consumer + fake engine/R2 in tests.
- Sync `/v1/tts` is queue-proxied and marked as compatibility/deprecation path.
- Worker crash/retry semantics are documented and covered by targeted tests.
- Transient/unknown failures have bounded retry + DLQ + terminal idempotency/usage side effects.
- Result stream retry duplicates are not client-visible (`seq` dedupe + attempt-start cleanup).
- Queue backpressure applies consistently to sync, stream, and async submit paths.
- Usage rows include enough worker/latency metadata for B.1.5 waterfall analysis.
- R2 reference cache has a bounded eviction policy.

B.1.5 is done when:

- First audio is emitted before full generation completes.
- Live session endpoint exists and returns a LiveKit room/token only when warm worker capacity exists.
- Latency waterfall metrics are recorded.
- Warm worker/reference/adapter cache behavior is explicit.
- R2 artifact finalization is not on the live first-audio critical path.
- A measured report distinguishes infra latency from model/runtime latency.
- `src/worker/live.py` keeps latency-sensitive generation on a thread-to-async frame bridge, not a full-drain list.
