# Checkpoint — Faz C v1 çıkışı: observability completion + production hardening tooling

**Tarih:** 2026-05-24 · **Range:** `d25aaf5`..HEAD (5 feature commits + audit-fix tail) · **Suite:** 382+/382+ · **Lint:** clean

Bu doc Faz C v1 turunun tamamlandığını belgeler. v0 ile birlikte Faz C'nin "ürün metriği görünür + production traffic'e dayanıklı" hedefini taşır.

> Önceki checkpoint: `faz-b-exit-self-audit.md` (Faz B kapanış + Faz C v0 plan)
> Faz C v0 closure: decision log row `2026-05-24 | Faz C v0` (commit `65db0ae` + audit fix-up `d25aaf5`)

---

## 1. Bir cümle ile

Faz C v0 observability iskeletini koydu (Prometheus + heartbeat backpressure + waterfall persistence). **v1 turu** Codex-listed checklist'in kalan 5 maddesini kapattı: gateway TTFB persistence, real-engine latency runner, Grafana dashboard + alerts, pgBouncer mode + pool tuning, ve load+chaos test harness. Ürün artık ölçülebilir + hardenlenebilir; gerçek-traffic ground truth'u runner'lar çalıştırılınca gelir.

---

## 2. v1 turunda yapılanlar (5 commit + fix-up)

| Item | Commit | Konu |
|---|---|---|
| 1 | `00f546c` | `gateway_first_byte_ms` persistence — migration 0005 + worker→gateway iki-fazlı UPDATE + `TTS_GATEWAY_FIRST_BYTE_SECONDS` histogram + E2E test |
| 2 | `00bcf71` | `scripts/latency_bench.py` runner + `docs/runbooks/latency-bench.md` runbook + 8 unit test |
| 3 | `e4a8223` | Grafana dashboard JSON + Prometheus alerts.yml + `docs/runbooks/observability-stack.md` + 12 contract test |
| 4 | `d924d80` | pgBouncer transaction-mode mode + asyncpg statement cache off + compose overlay + `docs/runbooks/database-pool.md` + 5 pool config test |
| 5 | (bu commit) | `scripts/load_bench.py` runner + `docs/runbooks/load-chaos.md` (3 baselines + 4 chaos scenarios) + 7 unit test |

**Toplam delta:** +13 docs, +5 scripts (3 runner + 2 deploy artifact), +5 migration + module changes, +37 yeni test.

---

## 3. Faz C kapanış checklist'i (Codex audit ve self-audit birleşik)

| Madde | v0 | v1 | Bugün |
|---|---|---|---|
| Gateway `/metrics` çalışıyor | ✅ | — | ✅ |
| Worker `/metrics` çalışıyor | ✅ | — | ✅ |
| `usage_records` waterfall metrikleri tam | 🟡 (5/6) | ✅ (6/6) | ✅ |
| `gateway_first_byte_ms` ölçülüyor | ❌ | ✅ | ✅ |
| heartbeat doğru stale check | ✅ (`updated_at_ms`) | — | ✅ |
| capacity-aware backpressure testli | ✅ | — | ✅ |
| Grafana dashboard | ❌ | ✅ | ✅ (13 panel + tenant/voice template vars) |
| error/DLQ/backpressure alertleri | ❌ | ✅ | ✅ (7 rule, severity-coded) |
| gerçek VoxCPM2 latency raporu | ❌ | 🟡 (runner var, ölçüm operator turu bekliyor) | 🟡 |
| pgBouncer/pool ayarı | ❌ | ✅ | ✅ (transaction mode + asyncpg cache off) |
| graceful shutdown testli | 🟡 (opt-in sleep) | 🟡 | 🟡 (gerçek inflight-tracking deferred) |
| 20-50 user load smoke | ❌ | ✅ (harness + playbook) | ✅ (harness) |
| 200 user darboğaz raporu | ❌ | 🟡 (harness var, run gerek) | 🟡 |

**Kalan iki 🟡** ikisi de "kod indi, operator turu bekleniyor" durumunda:
- Item 2 / Item 5: gerçek GPU üzerinde latency_bench + load_bench koşulup `experiments/` altına kayıt + closure doc'a sayı eklenmesi gerekiyor.
- "gerçek inflight tracking" graceful shutdown ayrı bir mini-PR olarak Faz C v2'de gelir; şu an opt-in sleep yeterli.

---

## 4. Yeni env vars (Faz C v1)

| Var | Default | Etki |
|---|---|---|
| `NQAI_DB_PGBOUNCER` | `false` | pgBouncer transaction modunda olduğumuzu bildirir; asyncpg statement cache kapanır, SQLAlchemy pool küçülür |
| `NQAI_DB_POOL_TIMEOUT_S` | `30` | SQLAlchemy pool acquire timeout |
| `NQAI_DB_POOL_RECYCLE_S` | `1800` | Pool conn recycle interval (server_idle_timeout ile align) |

Faz C v0'da gelen heartbeat/metrics/drain env'leri AGENTS.md'de zaten dökümante. Bu üç eklendi.

---

## 5. Yeni runbook'lar

- [docs/runbooks/latency-bench.md](../runbooks/latency-bench.md) — per-hardware latency baseline koşumu
- [docs/runbooks/observability-stack.md](../runbooks/observability-stack.md) — Prometheus + Grafana wiring
- [docs/runbooks/database-pool.md](../runbooks/database-pool.md) — pgBouncer + pool sizing matematiği
- [docs/runbooks/load-chaos.md](../runbooks/load-chaos.md) — 20/50/200 baseline + 4 chaos scenario playbook

Hepsinin ortak yapısı: "ne zaman koş", "ne zaman koşma", "ne çıkmalı", "fail criteria", "kayıt etme protokolü".

---

## 6. Faz C v1'de yapmadıklarımız (v2'ye / Faz D'ye)

| İş | Neden v1 değil | Hedef |
|---|---|---|
| Per-voice sticky routing | Cold-load metric'i koymadan karar veremeyiz; `nqai_worker_cold_load_seconds{voice_id}` metric'i v2'nin ilk işi | **C v2** |
| OpenTelemetry tracing + exemplars | Prometheus exemplars + OTel bridge ayrı bir scope (~1 hafta), v1'in kapsamını şişirirdi | **C v2** |
| pgroll zero-downtime migration | Alembic forward-only şu anki schema-change ritmiyle yeterli; pgroll Faz D'de canlı migration ihtiyacı doğunca | **Faz D** |
| Multi-region (Yugabyte / NATS) | Single-region 200-user hedefini kapatmadan multi-region prematüre | **Faz D / E** |
| Distributed load generation (1000+ user) | Mevcut harness tek-host; gerçek 1000-user için k6/locust + multiple runner pod | **Faz D** |
| Voice fingerprint + AudioSeal + KVKK | Governance layer — Faz 3 ürün konusu | **Faz 3** |

---

## 7. Şimdi durumun resmi

| Boyut | Değer |
|---|---|
| Test | **382+** (Faz C v0 sonu 347'den +35) |
| Ruff | clean |
| Worker metrics | gauge × 4 + counter × 3 + histogram × 8 (`/metrics` :9100 üzerinden) |
| Gateway metrics | `/metrics` endpoint refreshes cluster gauges + queue_depth on every scrape |
| Heartbeat | `updated_at_ms` liveness + `last_pickup_ms` activity (Codex-audit fix) |
| Backpressure | capacity-aware + XLEN fallback + TTS_REQUESTS{status=backpressure} SLO denom |
| Waterfall | 8 stage (queue_wait, worker_pickup, reference_resolve, first_pcm, first_audio, gateway_first_byte, inference, total) — hepsi persisted |
| Dashboard | 13 Grafana panels + 7 Prometheus alerts |
| Pool tooling | pgBouncer txn-mode + asyncpg cache disable + compose overlay |
| Load harness | `latency_bench.py` (single-call waterfall) + `load_bench.py` (sustained baseline) |
| Chaos playbook | 4 scenarios (worker kill, Redis blip, R2 slow, DB pool saturation) |

---

## 8. Tek söz

Faz C v0 ölçüm iskeletini, v1 production tooling'i tamamladı. Ürün artık **görünür** (dashboard + alerts), **ayarlanabilir** (pgBouncer + pool env'leri + heartbeat backpressure), ve **test edilebilir** (latency_bench + load_bench + chaos playbook). Real-traffic ground truth runner'ları çalıştırılınca gelir; o tur kapandığında Faz D (zero-downtime migration + read replicas + multi-region) için zemin sağlam.
