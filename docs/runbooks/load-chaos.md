# Runbook — Load + chaos test playbook

**Owner:** Backend lead · **Script:** [`scripts/load_bench.py`](../../scripts/load_bench.py)
**Companions:** [latency-bench.md](latency-bench.md), [observability-stack.md](observability-stack.md), [database-pool.md](database-pool.md).

## Purpose

The Codex audit + Faz C closure plan called for three baseline load runs (20 / 50 / 200 concurrent) plus four chaos scenarios. This doc is the playbook for running them and what "passed" looks like.

## Baselines

Run each level for ≥60 s of warm steady state (after the model has loaded). Capture `raw.json` + `report.md` into `experiments/<date>-load-<concurrency>/`.

```bash
# 20-user baseline
python scripts/load_bench.py \
    --base-url http://gateway:8000 \
    --api-key "nqai_prod_xxxxxxxx" \
    --voice neeko-v01 \
    --concurrency 20 \
    --duration-s 120 \
    --hardware-label "L4-runpod" \
    --out experiments/2026-XX-XX-load-20

# 50-user smoke
python scripts/load_bench.py ... --concurrency 50 --duration-s 120 ...

# 200-user target simulation
python scripts/load_bench.py ... --concurrency 200 --duration-s 180 ...
```

### Pass criteria

`load_bench.py` (Codex audit fix 2026-05-25) emits the report in two
explicit columns so success rate and backpressure don't get conflated:

* **Accepted success rate** = `successes / (successes + uncontrolled_failures)`.
  Denominator excludes 503s. This is "of the requests the cluster
  decided to handle, how many completed cleanly".
* **Admission rejection rate** = `admission_rejections (503) / total_requests`.
  Non-zero is OK when sized backpressure is at work; zero under
  heavy load might actually mean the limit is too loose.
* **Uncontrolled failures** = anything else (non-503 5xx, client
  timeouts, transport errors). MUST stay close to zero — these are
  what pages the on-call.

| Level | Accepted success rate | client_total_ms p95 | Admission rejection rate | Uncontrolled failures |
|---|---|---|---|---|
| 20  | ≥ 99.5% | bounded by per-hardware latency ([latency-bench.md](latency-bench.md)) | 0 | 0 |
| 50  | ≥ 99% | within 1.5× the 20-user p95 | ≤ 2% | 0 |
| 200 | ≥ 99% (of admitted) | within 3× the 20-user p95 | ≤ 30% (cluster correctly refusing oversize work) | < 0.5% |

The 200-user level is INTENTIONALLY allowed substantial backpressure-driven 503s (that's the capacity-aware admission doing its job). What's NOT allowed: uncontrolled failures, DLQ growth, connection-pool exhaustion. Reading the report: a 200-user run with `accepted_success_rate=99.5%` + `admission_rejection_rate=22%` + `uncontrolled_failures=0` is **passing**, not failing.

### What to inspect while it runs

Open Grafana ([observability-stack.md](observability-stack.md)) on a side monitor:
* **Queue depth** stays bounded — if it monotonically climbs, the cluster can't keep up.
* **DLQ count (1h)** stays 0 across the whole run.
* **First-audio p95** doesn't drift upward during the run — drift means model warmup is incomplete or memory pressure is building.
* **Worker in-flight ÷ capacity** sits in [0.6, 0.9] for the level you're testing — much lower means concurrency is being absorbed elsewhere (gateway? client?), much higher means workers are the bottleneck.

## Chaos scenarios

For each: start a baseline 50-user load via `load_bench.py` in one terminal, perform the perturbation in another, then keep the load running for another 60 s and capture the report.

### C1 — Worker kill / recover

**Perturbation:** kill one worker pod mid-run.

```bash
docker compose -f docker-compose.dev.yaml --profile gpu stop worker
# (or kubectl delete pod nqai-worker-xxxx)
sleep 30
docker compose -f docker-compose.dev.yaml --profile gpu start worker
```

**Expected behaviour depends on how many other workers are running.**

**Multi-worker case (≥ 2 workers, recommended for this test):**
* In-flight jobs on the killed worker remain in PEL (not XACKed).
* Another live worker's periodic XAUTOCLAIM sweep reclaims them after `xautoclaim_min_idle_ms` (default 30 s).
* Killed worker's heartbeat disappears within `stale_ms` (default 5 s); gateway capacity drops, backpressure tightens.
* When the killed worker restarts, heartbeat reappears, capacity recovers.
* Accepted success rate ≥ 95% over the full window. NO DLQ entries.

**Single-worker case (one worker only):**
* In-flight jobs stay in PEL with no one to reclaim them while the
  worker is down. **There is no recovery during the outage.**
* When the worker restarts, its OWN startup XAUTOCLAIM sweep picks
  the PEL entries back up and processes them. Recovery happens on
  restart, not in real time.
* During the outage the gateway sees `worker_count=0` and falls back
  to XLEN-only backpressure (capacity-aware path can't work without
  heartbeats). 503 rate climbs.

**Fails if:** DLQ grows, accepted success rate drops below 95% (multi-worker), recovered/restarted worker doesn't pick up its previous PEL.

### C2 — Redis transient hiccup

**Perturbation:** pause Redis for 5 seconds.

```bash
docker compose -f docker-compose.dev.yaml pause redis
sleep 5
docker compose -f docker-compose.dev.yaml unpause redis
```

**Expected:**
* Gateway: 503s with `Retry-After: 5` (capacity read failed → XLEN-only fallback fails too).
* Worker: tick loop catches `ResponseError`, sleeps 1 s, retries. No worker crash.
* Heartbeat refresh failures get warn-logged (throttled 1/min), worker keeps running.
* When Redis recovers, traffic resumes.
* No DLQ growth (no job got past acceptance during the pause).

**Fails if:** worker crashes, request handlers raise unhandled exceptions, success rate stays degraded after Redis recovers.

### C3 — R2 / artifact storage slow

**Perturbation:** introduce 2-second latency on R2 (use `tc qdisc` on the R2-bound interface or a chaos-engineering proxy like Toxiproxy in front of S3).

**Expected:**
* `nqai_tts_total_seconds` p95 climbs by ~2 s (archive is the slowest step now).
* `nqai_tts_first_audio_seconds` UNCHANGED — first audio publishes BEFORE archive (worker pipeline invariant since Faz B.1.5).
* Worker may transient-retry archive once before TransientFailure → eventually DLQ if R2 stays slow > retry budget.

**Fails if:** first-audio degrades alongside total (means we accidentally regressed the publish-before-archive invariant), or workers crash instead of marking jobs transient.

### C4 — DB pool saturation

**Perturbation:** open and HOLD a bunch of long-running DB connections from outside the app:

```bash
# Spawn 18 long-running psql sessions to leave only 2 in the default pool
for i in {1..18}; do
  PGPASSWORD=nqai psql -h localhost -U nqai -d nqai_voice \
      -c "SELECT pg_sleep(120);" &
done
```

**Expected today (Faz C v1):**
* Without pgBouncer: gateway / worker exhaust their SQLAlchemy pool → request handlers hit `pool_timeout` → return **500** with a Postgres timeout in the body.
* With pgBouncer: client_conns rise but pgBouncer queues; `query_wait_timeout` (20 s) fires → app sees a Postgres error and again returns **500**.

The 500 is **honest** — the system surfaces the real failure mode rather than hanging — but it's not ideal. A loaded DB pool is functionally indistinguishable from overload, and the on-call response is the same as for a capacity issue (back off, scale up). The right code is **503 with `Retry-After`**, possibly behind a tiny circuit breaker around the `get_session()` dependency. That's a documented Faz C v2 item.

**For this run, pass criteria:**
* The app surfaces a **5xx**, not a hang.
* Errors classify as `uncontrolled_failures` in the load_bench report, NOT as `admission_rejections`.
* DLQ does not grow (DB-side failures inside the worker are caught as `TransientFailure` → PEL → retry, not poison).

**Fails if:** the app hangs (no 5xx ever), DB exhaustion misclassifies as 503 backpressure (would mask a real capacity problem), or the worker DLQs jobs that should have been retryable.

## Recording results

For every baseline + chaos run, commit to `experiments/<date>-<scenario>/`:
1. `raw.json` (from load_bench)
2. `report.md` (from load_bench)
3. A short `notes.md` describing what you perturbed + what you observed in Grafana

If a chaos scenario fails the "Expected" criteria, **do not absorb it silently**. Open a decision-log entry: what broke, what fix is on the table, what regression test we'll add.

## When NOT to run this

- Production database under real customer load. Use a staging clone.
- Without a working `/metrics` + dashboard — you need to SEE the cluster while loading it.
- Before `scripts/latency_bench.py` has produced a baseline. The load-bench report is meaningful only relative to known-good single-user latency.

## What this harness does NOT do

- Distributed load generation. For 1000+ user simulation, run multiple `load_bench.py` processes in parallel from different hosts.
- Long-tail latency analysis (p99.9). Not enough samples in a 60 s window. For that, use a dedicated tool (k6, locust) and a longer run.
- Automated PASS/FAIL gating. The pass criteria above are operator-checked — bake them into a CI step if/when we have a staging GPU box that can hold the load.
