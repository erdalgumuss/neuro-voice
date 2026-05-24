# Runbook — Real-engine latency benchmark

**Owner:** Backend lead · **Script:** [`scripts/latency_bench.py`](../../scripts/latency_bench.py)
**Use case:** Produce the single artifact that says "this is what TTS latency actually looks like on hardware X with real VoxCPM2". The output becomes the citation in the B.1.5 closure doc, the sticky-routing decision, and any SLO budget conversation.

## When to run

- After deploying to a new GPU shape (L4, L40S, A100, etc.) — first thing.
- After a worker-side change that touches the inference pipeline (engine adapter, reference resolver, bridge code).
- Before declaring B.1.5 "closed with real numbers" in the audit doc.
- Quarterly, to detect drift (model upgrades, library bumps, hosting changes).

Do NOT use this script for:
- Audio quality regression — that's `scripts/smoke_test.py`.
- Load testing at scale — that's Faz C v1 item 5 (k6/locust on the to-be-built load harness).

## Prerequisites

1. A running NQAI Voice gateway + worker against real Postgres + Redis + R2:
   ```bash
   docker compose -f docker-compose.dev.yaml up -d
   docker compose -f docker-compose.dev.yaml --profile gpu up worker
   ```
   (Or staging/prod deployment of your choice.)

2. At least one enrolled voice with a real reference audio. The benchmark doesn't enroll for you — use `scripts/bootstrap_voices.py` first.

3. An API key with `tts:write` scope. Mint via the admin UI or `scripts/seed_operator.py` + `/admin/tenants/.../keys`.

4. Optional but recommended: DB URL access from the box running the benchmark, so the script can pull the worker-side waterfall (`queue_wait_ms`, `worker_pickup_ms`, `reference_resolve_ms`, `first_pcm_ms`, `first_audio_ms`, `gateway_first_byte_ms`, `inference_ms`, `rtf`) and not just the client-side TTFB.

## Run it

```bash
export NQAI_DATABASE_URL=postgresql+asyncpg://nqai:nqai@localhost:5432/nqai_voice

python scripts/latency_bench.py \
    --base-url http://localhost:8000 \
    --api-key "nqai_dev_xxxxxxxx" \
    --voice neeko-v01 \
    --requests 30 \
    --concurrency 4 \
    --hardware-label "L4-runpod-2026-05-24" \
    --out experiments/2026-05-24-latency-bench-L4
```

Outputs land under `--out`:
- `raw.json` — per-call samples + percentile dict (machine-readable)
- `report.md` — paste-into-PR Markdown table

If `NQAI_DATABASE_URL` is unset and `--db-url` not passed, the report still works — only the worker-side waterfall columns will be missing.

## What "healthy" looks like (target ranges — local observation, not vendor benchmark)

Numbers below are from prior local runs on T4 with stub references; **real VoxCPM2 numbers go in the B.1.5 closure doc after the first bench**. Treat these as rough sanity bands, not SLOs:

| Stage | T4 expected | L4 expected | A100 expected |
|---|---|---|---|
| `queue_wait_ms` p95 | < 100 | < 100 | < 100 |
| `worker_pickup_ms` p95 | < 50 | < 50 | < 50 |
| `reference_resolve_ms` p95 (warm R2 cache) | < 200 | < 200 | < 200 |
| `first_pcm_ms` p50 | 800–1500 | 400–700 | 200–400 |
| `first_audio_ms` p50 | 850–1600 | 450–800 | 250–500 |
| `gateway_first_byte_ms` p50 | 900–1700 | 500–850 | 280–550 |
| `inference_ms` (full job, single sentence) | 1500–3500 | 800–1800 | 400–1000 |
| `rtf` (warm) | 0.4–0.7 | 0.2–0.4 | 0.1–0.25 |

Big gaps to investigate:
- `gateway_first_byte_ms − first_audio_ms` > 100 ms → gateway/transport / ASGI buffering issue, not worker latency.
- `reference_resolve_ms` > 500 ms repeatedly → R2 cache miss every time. Check LRU + warm cache.
- `worker_pickup_ms` p95 > 500 ms → either XAUTOCLAIM is sweeping a real backlog (look at PEL) or the worker is too slow to drain. Check `nqai_queue_depth{stream="jobs"}` on `/metrics`.
- `first_pcm_ms − queue_wait_ms − reference_resolve_ms` accounts for almost everything → the model itself is slow. Compare to vendor RTF expectations.

## Recording results

After a run, do **all three**:

1. Commit `experiments/<date>-latency-bench-<hardware>/` to the repo.
2. Add a row to `docs/audit/checkpoint-2026-05-24-faz-b1.5-exit.md` Section 8 (the "Şimdi durumun resmi" table) with the real `first_audio_ms p50 / p95` value for the hardware you tested.
3. If anything is wildly outside the expected band, open a decision-log entry: what you observed, what you tried, what you decided to do (cache change, hardware change, model change). Don't silently absorb 3× slower numbers.

## Tuning the run

- `--requests N` should be ≥ 20 for meaningful p95. Default 20 fast-and-loose; 50+ for a quotable number.
- `--concurrency C` — exercise the worker pool. With single worker (`NQAI_WORKER_CAPACITY=1`) anything > 1 mostly stresses queue_wait_ms; with multi-worker it spreads. Default 4.
- `--text-set FILE` — pass your own JSON list of strings to bench domain-specific text (long sentences vs short, child-directed vs adult, etc.).
- `--db-settle-s` — bumps the wait before the DB join. Default 1 s; raise it if `report.md` shows "matched 0/N samples" (means the gateway's `UPDATE gateway_first_byte_ms` hasn't committed yet when we query).

## Known limitations

- Single voice per run. Loop the script for cross-voice comparisons; per-voice cold-load metrics will land in `nqai_worker_cold_load_seconds{voice_id}` (Faz C v1 item 3 successor).
- No PEL / DLQ assertions — this script measures latency only. Reliability under fault is the chaos test harness (Faz C v1 item 5).
- The benchmark request the gateway, NOT the worker directly. Net path includes gateway HTTP framing. That's intentional — production clients hit the gateway, not the worker.
