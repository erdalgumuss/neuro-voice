# Runbook — Database connection pool + pgBouncer

**Owner:** Backend lead · **Companion:** [observability-stack.md](observability-stack.md)
**Decision background:** scale-roadmap.md D-07.

## TL;DR

| Scenario | Setup |
|---|---|
| Local single-process dev | Direct Postgres. `NQAI_DB_PGBOUNCER` unset. Default pool 10 + 10 = 20 connections per process. |
| Local stress-test / multi-replica dev | Direct Postgres or pgBouncer overlay (`docker-compose.pgbouncer.yaml`). Either works for ≤4 replicas. |
| **Production** | pgBouncer transaction mode, mandatory. `NQAI_DB_PGBOUNCER=true` on gateway + worker. Pool 5 + 5 = 10 client conns per process; pgBouncer multiplexes onto a small server-conn fleet. |

## Why pgBouncer

Postgres allocates ~10 MB of RAM per backend connection + a process per connection. 200 concurrent clients direct-to-Postgres = 200 backends = 2 GB RAM + process churn. pgBouncer in transaction-pool mode lets us serve 200 concurrent clients with ~20–40 actual Postgres backends because the application's transactions are tiny (single-digit ms) and the pool can fan-in.

The cost: in transaction mode, the same client may hit different server-side connections in successive transactions. This breaks any state that lives on the connection — most notably **asyncpg's prepared-statement cache** and Postgres **advisory locks** / `LISTEN-NOTIFY`. NQAI Voice doesn't use the latter; the prepared-statement cache is handled by `NQAI_DB_PGBOUNCER=true` flipping `statement_cache_size=0` on asyncpg.

## Connection math

```
                     ┌──────────────┐
                     │   Postgres   │  max_connections = 100
                     │              │  (room for admin / Alembic / etc.)
                     └──────┬───────┘
                            │ ≤ 40 server conns (default_pool_size=20 × N dbs)
                     ┌──────┴───────┐
                     │  pgBouncer   │  max_client_conn = 200
                     └──┬───────┬───┘
                ≤200    │       │   ≤200
                 ┌──────┴──┐ ┌──┴──────┐
                 │ Gateway │ │ Worker  │  N replicas
                 │  pool=5 │ │ pool=5  │  + overflow=5 each
                 │  o.f.=5 │ │ o.f.=5  │  = 10 client conns / process
                 └─────────┘ └─────────┘
```

Scaling rules of thumb (rebudget when changing replica counts):

| Replicas (gateway+worker) | client_conn_total | default_pool_size | postgres_conns |
|---|---|---|---|
| 2 + 2 | 40 | 10 | 20 |
| 5 + 5 | 100 | 15 | 30 |
| 10 + 10 | 200 | 20 | 40 |
| 20 + 20 | 400 | 30 | 60 |

If `postgres_conns` is approaching `max_connections - 20` (leaving headroom for admin / Alembic / monitoring), either:
1. Lower `default_pool_size` (multiplexing harder).
2. Raise Postgres `max_connections` (only if RAM allows — ~10 MB / conn).
3. Read-replicate (Faz D+).

## Wiring it up locally

```bash
# Direct Postgres (default):
docker compose -f docker-compose.dev.yaml up -d

# With pgBouncer overlay:
docker compose \
    -f docker-compose.dev.yaml \
    -f docker-compose.pgbouncer.yaml \
    up -d
```

The overlay rewires gateway + worker to talk to `pgbouncer:6432` instead of `postgres:5432` and sets `NQAI_DB_PGBOUNCER=true` so the app disables its statement cache.

Smoke check after bringing pgBouncer up:

```bash
# pgBouncer admin console (read-only stats).
docker compose -f docker-compose.dev.yaml -f docker-compose.pgbouncer.yaml \
    exec pgbouncer psql -h 127.0.0.1 -p 6432 -U nqai -d pgbouncer

pgbouncer=# SHOW pools;
pgbouncer=# SHOW clients;
pgbouncer=# SHOW servers;
```

Healthy output: `cl_active` + `cl_waiting` low under normal traffic; `sv_active` ≤ `default_pool_size`; `sv_idle` >0 (warm connections ready).

## Production checklist

- [ ] pgBouncer running with persistent volume for the auth file (or `auth_query` against `pg_authid` if hosted Postgres allows).
- [ ] TLS enabled (`client_tls_sslmode=require`, `server_tls_sslmode=verify-full` against the Postgres CA).
- [ ] `userlist.txt` populated with the actual SCRAM-SHA-256 hash for the `nqai` role (or wired to `auth_query`).
- [ ] Application env on every gateway + worker:
  - `NQAI_DATABASE_URL=postgresql+asyncpg://nqai:...@pgbouncer:6432/nqai_voice`
  - `NQAI_DB_PGBOUNCER=true`
  - `NQAI_DB_POOL_SIZE=5` (or higher if traffic / pgBouncer side allows)
- [ ] Postgres `max_connections` ≥ `pgbouncer.default_pool_size × dbs + 20` slack.
- [ ] Monitoring: scrape pgBouncer's metrics endpoint (`pgbouncer_exporter`, separate concern) — surfaces `sv_active`, `cl_waiting`, `pool_mode`.
- [ ] Alert: `cl_waiting > 0` for > 1 minute → pgBouncer is saturated, scale pool size or workers.

## Failure modes + recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| Gateway logs `prepared statement does not exist` | `NQAI_DB_PGBOUNCER=true` missing → asyncpg statement cache hits a recycled pgBouncer server conn. | Set the env var on every replica + rolling restart. |
| Latency p95 spikes on DB calls without Postgres CPU jumping | pgBouncer pool exhausted (`cl_waiting > 0`). | Raise `default_pool_size` OR scale Postgres backends. Check `sv_active` against the cap. |
| `query_wait_timeout` errors visible in app logs | Same as above + the timeout was hit. | Same fix; also consider raising `query_wait_timeout` if your `NQAI_SYNC_TIMEOUT_S` is bigger than 20 s. |
| Postgres `out of connections` despite pgBouncer | Either pgBouncer pool size > `max_connections`, or someone is opening direct connections that bypass pgBouncer (Alembic, admin scripts). | Math the totals and raise `max_connections` if needed; route Alembic through pgBouncer in **session** mode (separate alias). |

## Alembic + pgBouncer

Alembic migrations require **session** mode because they issue
multi-transaction operations (`CREATE INDEX CONCURRENTLY`, advisory
locks for migration safety, etc.) that transaction-mode breaks.

Two clean options:
1. Run Alembic against the Postgres host directly (`postgresql://nqai:nqai@postgres:5432/nqai_voice`), bypassing pgBouncer entirely. Simplest.
2. Run a second pgBouncer instance / `[databases]` alias on a different port with `pool_mode=session` reserved for Alembic / admin tooling.

`zero-downtime migration` tooling (pgroll, deferred to Faz C v1 successor) needs option 2.
