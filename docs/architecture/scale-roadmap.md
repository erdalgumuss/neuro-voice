# NQAI Voice — Scale Roadmap (kanonik)

**Doc owner:** Tech Lead · **Karar tarihi:** 2026-05-24 · **Hedef horizon:** 4 hafta to first prod, 8 hafta to 200-user scale

Bu doküman çekirdek mimari, bileşen seçimleri, faz çıktıları ve karar gerekçelerini içerir. Ekip her bileşeni `docs/architecture/<bileşen>.md` altındaki detay belgelerinden okur; bu dosya tek pencereli yol haritasıdır.

> **Geçerli sürüm:** Platform v0.2 (VoxCPM2 single-process, in-memory auth, file-system catalog). Bu roadmap onu v1.0 multi-tenant scale-ready omurgaya yükseltir.

---

## 0. Yönetici özeti

Tek-process VoxCPM2 prototipinden, **4 tenant × 5 concurrent = 20 user başlangıç** ve **3-4× yatay ölçek ile 200 user** taşıyabilen bir mimariye geçiyoruz. Hedef pattern: **stateless API gateway + Redis Streams iş kuyruğu + GPU worker pool + Postgres data plane + S3-compat object storage**.

> **Terminoloji notu (Refactor R, 2026-05-24):** "tenant" **account/workspace** demektir, "ürün" değil. Bir tenant kendi voice catalog'unu yönetir, kendi API key'lerini üretir, kendi usage'ını ödenir. Ürün (NEEKO toy, NIVA call-center, NeuroCourse, NARO) tenant ile **1:1 değil** — bir tenant ("erdal-dev") birden çok ürüne hizmet edebilir, bir ürünün ("neeko-app") production tenant'ı staging tenant'ından ayrı olabilir. Ürün attribution `X-NQAI-App` request header'ı ile `usage_records.app_label` üzerinden yapılır. Voice'lar tenant'a aittir ama `visibility` (`private/shared/public`) + `voice_access` tablosu ile cross-tenant paylaşılabilir. ElevenLabs/OpenAI ile uyumlu mental model.

Çekirdek varsayımlar:
- 4 client uygulama (NEEKO toy, NIVA call-center, NeuroCourse instructor, NARO) ortak omurgayı tüketir.
- Her account (tenant) kendi API key'lerini üretir; ürünler `X-NQAI-App` header ile rollup edilir.
- Streaming TTS canlı hattı **HTTP chunked primary** (`POST /v1/tts/stream`); long-running + presigned artifact için **async jobs** (`POST /v1/tts/jobs`); sync `POST /v1/tts` queue proxy backward-compat (RFC 8594 sunset). Duplex voice-agent (NIVA call-center, gerçek 2-yönlü konuşma) bu yüzeyde değil — gerektiğinde ayrı bir product surface + ayrı transport (WebRTC veya gRPC bidi) ile gelir. Karar dayanağı: ElevenLabs / OpenAI Audio / Cartesia / MiniMax endüstri standardı; WebRTC SFU hop tek-yönlü TTS'e değer katmaz. Bkz. [decisions/README.md](../decisions/README.md) 2026-05-24 "Faz B.1.5 — streaming TTS API (HTTP chunked + async jobs, NO WebRTC)" satırı.
- Birincil base model: VoxCPM2 (Apache-2.0). Inference runtime: önce direct `voxcpm`, Faz C'de `nano-vllm-voxcpm` (resmi paket, RTX 4090'da RTF 0.13, batched concurrent + FastAPI server).
- "Premium" tanımı [voxcpm2-integration.md](voxcpm2-integration.md) bölümünde, sayısal hedefler [observability.md](observability.md) SLO'larında.

---

## 1. Hedefler ve non-goals

### In scope (v1.0 product surface)

| Hedef | Ölçüm |
|---|---|
| 4 tenant aynı anda hizmet alır, birbirinin verilerini göremez | tenant isolation testi yeşil, audit log her cross-tenant erişim girişimini yakalar |
| 20 concurrent TTS request, p95 ses-üretim latency'si ≤ 5 s | k6/locust load test |
| Streaming TTFB (time to first byte) p50 ≤ 800 ms, p95 ≤ 1.5 s | Prometheus histogram `nqai_tts_ttfb_seconds` |
| Her tenant kendi API key'lerini yönetir (CRUD) — sade web UI | Admin UI demo |
| Inference worker crash sonrası in-flight iş kaybolmaz (at-least-once) | chaos test: worker kill → eventual completion |
| 200 user yatay scale: worker N=4 → N=16 + DB connection pool ayarı dışında değişiklik gerektirmez | load test 200 vu, p95 ≤ 6 s |
| Her bileşenin RED metrics'i (Rate/Errors/Duration) Grafana'da | dashboard URL |
| TLS uçtan uca, secret'ler ortam değişkenlerinden değil sır yöneticisinden | secret audit |

### Out of scope (v1.0)

- **Türkçe SFT veya per-character LoRA** — Faz 2-3 ürün konusu (bkz. [voxcpm2-integration.md §10](voxcpm2-integration.md)).
- **Edge / on-device inference** — Faz 3+.
- **Voice fingerprint + watermark + KVKK rider** — Faz 3'te `voice-governance-layer` ile gelir.
- **Çok-bölgeli (multi-region) deployment** — Tek bölge (EU veya US) başlangıçta; multi-region Faz D sonrası.

---

## 2. Mimari prensipler

Her kod kararı bu beş prensipten birine dayanır. Aksi durumda decision log satırı + onaylı sapma not'u şart.

| # | Prensip | Pratik yansıması |
|---|---|---|
| **P1** | **Stateless services** | Gateway ve worker process'leri restart edildiğinde state kaybı yok. Tüm durum Postgres + Redis + object storage'da. |
| **P2** | **At-least-once with idempotency keys** | Worker crash + retry sonrası yan etki tekrarlanmamalı. Her iş `request_id` (client tarafı UUIDv7) ile idempotent. |
| **P3** | **Observability-first** | Hiçbir endpoint metric + structured log + trace üretmeden merge edilmez. Her request kendi `trace_id`'sini taşır. |
| **P4** | **Fail-safe defaults** | Auth açık varsayımı, rate limit aktif varsayımı, TLS varsayılan. Aksini açmak için explicit env flag + log uyarısı. |
| **P5** | **Defense in depth** | TLS (Cloudflare) + Bearer auth (DB) + per-key rate limit + per-tenant quota + audit log. Tek katman yenilirse diğeri ayakta. |

---

## 3. Çekirdek mimari (logical)

```
                                  ┌─────────────────────────────┐
                                  │   Admin UI (FastAPI+HTMX)   │
                                  │   /admin                    │
                                  │   • tenant CRUD             │
                                  │   • API key generate/revoke │
                                  │   • usage dashboard         │
                                  │   • JWT login (operator)    │
                                  └──────────────┬──────────────┘
                                                 │ (same backend)
   NEEKO toy ─────┐                              ▼
                  │                ┌─────────────────────────────┐
   NIVA ──────────┤   HTTPS        │   API Gateway (FastAPI)     │
   call-center    │   wss://       │   • Bearer auth (DB)        │
                  │   api.nqai     │   • Pydantic validate       │
   NeuroCourse ───┤   .voice       │   • Per-key rate limit      │
   instructor     │                │   • Submit job → Redis      │
                  │                │   • WebSocket / chunked HTTP│
   NARO ──────────┘                │   • OTel auto-instrument    │
                                   └──────┬────────────────┬─────┘
                                          │ (submit + ack) │ (results)
                                          ▼                ▼
                          ┌──────────────────────────────────────┐
                          │   Redis Streams                       │
                          │   ┌────────────────────────────────┐  │
                          │   │ stream: nqai.tts.jobs          │  │
                          │   │ consumer group: tts-workers    │  │
                          │   │ stream: nqai.tts.results.{rid} │  │
                          │   └────────────────────────────────┘  │
                          │   + rate-limit sliding window         │
                          │   + voice metadata cache              │
                          └──────────────┬───────────────────────┘
                                         │ XREADGROUP / XACK
                          ┌──────────────┼─────────────────┬────────────┐
                          ▼              ▼                 ▼            ▼
                     ┌─────────┐    ┌─────────┐       ┌─────────┐  ┌─────────┐
                     │Worker 1 │    │Worker 2 │       │Worker N │  │ (k8s    │
                     │ (GPU)   │    │ (GPU)   │  ···  │ (GPU)   │  │  HPA)   │
                     │ VoxCPM2 │    │ VoxCPM2 │       │ VoxCPM2 │  │         │
                     │ +Türkçe │    │ frontend│       │ frontend│  └─────────┘
                     │ frontend│    │         │       │         │
                     └────┬────┘    └────┬────┘       └────┬────┘
                          │              │                 │
                          └──────────────┼─────────────────┘
                                         ▼
                          ┌──────────────────────────────────────┐
                          │   PostgreSQL 16                       │
                          │   ┌────────────────────────────────┐  │
                          │   │ tenants                        │  │
                          │   │ api_keys (argon2id hash)       │  │
                          │   │ operators (admin UI)           │  │
                          │   │ voices                         │  │
                          │   │ usage_records (time-series)    │  │
                          │   │ audit_log                      │  │
                          │   │ job_status (idempotency map)   │  │
                          │   └────────────────────────────────┘  │
                          └──────────────┬───────────────────────┘
                                         │
                                         ▼
                          ┌──────────────────────────────────────┐
                          │  Object Storage (Cloudflare R2)       │
                          │  • voices/<tenant>/<voice_id>.wav     │
                          │  • adapters/<voice_id>/<version>.bin  │
                          │  • snapshots/<request_id>.wav (opt.)  │
                          │  • model-cache/voxcpm2/<sha>.bin      │
                          └──────────────────────────────────────┘

                          ┌──────────────────────────────────────┐
                          │  Observability                        │
                          │  • Prometheus (metrics scrape)        │
                          │  • Grafana (dashboards + alerts)      │
                          │  • Loki (structured JSON logs)        │
                          │  • Tempo veya Honeycomb (OTel traces) │
                          │  • Alertmanager → PagerDuty / Slack   │
                          └──────────────────────────────────────┘
```

### Bileşen sorumluluk matrisi

| Bileşen | Sorumluluk | Sınır |
|---|---|---|
| **Admin UI** | Operator login, tenant + API key CRUD, usage view | Hiçbir TTS endpoint'ine doğrudan dokunmaz, gateway altında |
| **API Gateway** | Auth, validate, rate-limit, job submit, response stream | GPU bilmez, model yüklü değil — pure I/O, ~256 MB RAM |
| **Redis Streams** | Job queue + result channel + cache + rate-limit counter | Tek SPOF noktası → Redis Sentinel veya managed (Upstash) |
| **GPU Worker** | Pull job → frontend normalize → VoxCPM2 generate → push chunks | Sadece queue ile konuşur; Postgres'i okur (manifest), yazmaz |
| **PostgreSQL** | Truth of state for tenants/keys/voices/usage | Migration sıkı (Alembic) — manuel SQL yasak |
| **Object Storage** | Binary blob'lar (audio, adapters, model weights cache) | Public erişim yok, gateway veya worker imzalı URL ile çeker |
| **Observability** | Metric + log + trace + alert | Critical path'te değil, scrape ve pull tabanlı |

---

## 4. Bileşen seçim matrisi

Her seçim 3-eksenli değerlendirildi: **olgunluk × ekip familiarity × değiştirilebilirlik**.

| Katman | Seçim | Sürüm | Gerekçe | Alternatif (ne zaman) |
|---|---|---|---|---|
| **API framework** | FastAPI | 0.115+ | OpenAPI built-in, async-first, Pydantic v2 entegre, voxcpm topluluğunda standart | Starlette direct (ekstra perf gerekirse), Litestar (FastAPI'ye yakın ergonomi, daha hızlı) |
| **ASGI server** | uvicorn | 0.32+ | FastAPI ile standart, `--workers` flag basit horizontal scale | Granian (Rust-based, %30+ throughput); Hypercorn (HTTP/3 desteği) |
| **DB** | PostgreSQL | 16 | Olgun, JSON+TS desteği, row-level security (multi-tenancy için), async asyncpg | YugabyteDB (Faz E+ multi-region için Postgres wire-compat), CockroachDB (lisans değişikliği riski) |
| **ORM** | SQLAlchemy | 2.0 async + asyncpg | Async-first, Alembic migration olgun, FastAPI ile ideal | Tortoise ORM (basit ama daha az olgun), pure SQL + asyncpg (max perf, daha çok boilerplate) |
| **Migrations** | Alembic | 1.13+ | Standart, code-first, rollback OK, online migration için pgroll opsiyonel | pgroll (zero-downtime migration için Faz D'de) |
| **Queue** | Redis Streams + FastStream | redis 7.4, faststream 0.6+ | At-least-once + consumer groups + XAUTOCLAIM + sub-ms latency; FastStream Python async wrapper standart; cache + queue + rate-limit aynı instance | NATS JetStream (multi-region için Faz E+); Kafka (overkill, ops ağır); RabbitMQ (queue OK ama cache + rate-limit ayrı servis gerekir) |
| **Cache** | Redis (queue ile aynı instance başlangıçta, Faz C'de ayrılır) | 7.4 | Voice manifest cache, rate limit, session, tek instance ile başlar | Memcached (cache-only, queue ayrı kalır) |
| **Object storage** | Cloudflare R2 | — | S3 API, **zero egress fee** (TTS audio download trafiği çok), 10 GB free | Backblaze B2 (egress 0.01/GB), AWS S3 (en olgun ama egress pahalı), MinIO self-hosted (Faz D opsiyonel) |
| **Inference runtime (Faz A-B)** | `voxcpm` direct | 0.5+ | Mevcut implementation, basit | — |
| **Inference runtime (Faz C+)** | `nano-vllm-voxcpm` | resmi paket | RTX 4090 RTF 0.13 (vs 0.30 direct), batched concurrent, FastAPI server gömülü, drop-in upgrade | Triton Inference Server (Faz D+ multi-model + multi-tenant routing için) |
| **Streaming protocol (primary)** | WebSocket (Starlette WS) | — | True bidirectional, browser native, mobile SDK desteği iyi | Server-Sent Events (tek yönlü, basit) |
| **Streaming protocol (fallback)** | HTTP/2 chunked WAV | — | Browser/curl/ffplay native, WebSocket destekleyemeyen istemciler için | gRPC bidirectional stream (Faz D+ B2B SDK için opsiyonel) |
| **Audio encoding** | PCM int16 raw + WAV header (Faz A-B); Opus 24 kbps + WebM (Faz C+) | — | PCM en düşük overhead + maximum compatibility; Opus 5-10x küçük bandwidth tasarrufu | Ogg/Vorbis (browser uyumu Opus kadar değil) |
| **API key auth** | Bearer token, prefix + secret, argon2id hash, DB | argon2-cffi 23.1+ | OWASP recommended (2024), scrypt'ten daha güçlü, memory-hard | bcrypt (eski ama hâlâ kabul edilebilir; argon2id 2025+ default) |
| **Admin auth** | JWT HS256, 1h access + 7d refresh, httpOnly cookie | PyJWT 2.9+ | Standart, Stateless, basit | Session-based auth (DB-backed, daha az ölçeklenebilir) |
| **Rate limit** | Redis sliding window counter (per-key + per-tenant) | redis-py 5+ | Sub-ms, atomic via Lua | API gateway level (Cloudflare Rules — Faz D'de eklenir) |
| **Admin UI** | FastAPI + Jinja2 + HTMX | htmx 2.0+ | Server-side rendering, JS minimum, 1-page CRUD için fazlasıyla yeterli, ayrı build pipeline yok | Next.js 15 (Faz E ekip büyürse) |
| **Container** | Docker + Compose (dev), Kubernetes (prod, Faz D+) | Docker 27, Compose 2.30, K8s 1.31 | Standart | Nomad (HashiCorp, basit ama ekosistem küçük) |
| **GPU cloud** | RunPod (start), Modal (managed), Lambda (alternatif) | — | Saatlik fiyat $0.5-1.5, A10G/L4/A100 spot OK, Docker image deploy | Vast.ai (en ucuz spot, daha az SLA), self-managed (Faz E+) |
| **CPU cloud** | Hetzner (start, ucuz), DigitalOcean (alternatif) | — | EU bölgesi düşük gecikme, CX22 5 EUR/ay başlangıç | AWS/GCP/Azure (Faz D B2B müşteri ister) |
| **Edge / TLS** | Cloudflare (free → pro) | — | TLS otomatik, DDoS koruması, edge cache, R2 ile gateway-free egress | Fastly, AWS CloudFront |
| **Observability — metrics** | Prometheus + Grafana | Prom 2.55, Grafana 11.4 | OSS standart, self-host olabilir, alertmanager ile pager rotation | VictoriaMetrics (storage daha ucuz, Faz D+) |
| **Observability — logs** | Loki + Grafana | Loki 3.3 | Prometheus ile tek pencere, label-based indexing ucuz | ELK stack (overkill başlangıç için) |
| **Observability — traces** | OpenTelemetry SDK → Tempo veya Honeycomb | OTel 1.30+ | Vendor-neutral protocol; Tempo self-host (Grafana stack), Honeycomb managed | Jaeger (eski ama OSS), Datadog (paralı) |
| **Secrets** | env (dev), Doppler/Infisical (staging+), HashiCorp Vault (prod ölçek) | — | Aşamalı yükseltme | AWS Secrets Manager (cloud lock-in) |
| **CI/CD** | GitHub Actions + GHCR | — | Repo ile entegre, ücretsiz private tier 2000 dk/ay yeterli başlangıç | GitLab CI (Faz E ekip büyürse) |
| **IaC** | Terraform (Faz D+) | 1.10+ | Multi-cloud, mature provider ekosistemi | Pulumi (Python kod, fakat ekibin Terraform familiarity'si daha yüksek) |
| **Load test** | k6 (Grafana) | 0.55+ | JavaScript scripting, distributed worker, Grafana entegre | Locust (Python, daha kolay ama daha az ölçeklenir) |

---

## 5. Min gereksinimler (per-node)

Hardware sizing 20-user baseline; 200-user için worker sayısı × 4, DB/Redis dikey büyütme.

| Rol | vCPU | RAM | Disk | Network | GPU | Tahmini provider | Aylık (20-user) |
|---|---|---|---|---|---|---|---|
| **API Gateway (n=2 HA)** | 2 | 4 GB | 50 GB SSD | 1 Gbps | — | Hetzner CX22 / DO basic | 2 × 5 EUR = 10 EUR |
| **PostgreSQL primary** | 4 | 8 GB | 100 GB NVMe | 1 Gbps | — | Hetzner CX31 + backup | 12 EUR + storage |
| **PostgreSQL standby (Faz C+)** | 4 | 8 GB | 100 GB NVMe | 1 Gbps | — | aynı | 12 EUR |
| **Redis (queue + cache + RL)** | 2 | 4 GB | 20 GB SSD | 1 Gbps | — | Hetzner CX22 veya Upstash managed | 5 EUR veya $10 managed |
| **GPU Worker** | 4 | 16 GB | 80 GB NVMe (model cache) | 1 Gbps | 16-24 GB VRAM (L4 / A10G / RTX 4090) | RunPod / Lambda spot | $0.5-1.5/saat × usage |
| **Object Storage** | — | — | 50 GB (start) | — | — | Cloudflare R2 | $0.015/GB/ay = $0.75 + $0 egress |
| **Monitoring stack** | 4 | 8 GB | 200 GB SSD (metric + log retention 30g) | 1 Gbps | — | Hetzner CX31 | 12 EUR |

**Toplam CPU + storage (20-user, Faz B sonu):** ~50 EUR/ay + R2 + GPU usage.

**200-user scale (Faz D+):** worker N=16, DB read replicas 2-3, Redis Cluster veya Sentinel, dedicated monitoring host — toplam ~$300-600/ay sabit + GPU usage proporsiyonel.

---

## 6. Zorunlu mimari kararlar (P0 — sapma yok)

| # | Karar | Neden zorunlu |
|---|---|---|
| **D-01** | Her client request'i `X-Request-Id` header'i veya body field'ı taşımak zorunda (yoksa gateway UUIDv7 atar) | Trace propagation, idempotency anahtarı, audit log korelasyonu |
| **D-02** | Her API key prefix + secret formatında (`nqai-prod-<rand14>_<rand40>`); DB'de sadece prefix + argon2id(secret) saklanır | Leak'te plain secret yok; prefix lookup hızlı |
| **D-03** | TLS uçtan uca — `wss://` ve `https://` zorunlu; HTTP düz dinleyici yok (sadece `/health/live` internal port) | OWASP A02:2021 |
| **D-04** | Audit log'da auth deneme + voice CRUD + key gen/revoke + admin login `INSERT` olarak yazılır, asla `DELETE` veya `UPDATE` edilmez | Forensic + compliance temel |
| **D-05** | Idempotency: gateway aynı `request_id`'yi 24 saat içinde 2. kez görürse cache'lenmiş cevabı döner | At-least-once delivery'de duplicate execution'ı önler |
| **D-06** | Worker XACK sadece TÜM chunk'lar başarıyla gateway'e push edildikten sonra | Crash sonrası XAUTOCLAIM ile başka worker devralır, ses kesintisiz |
| **D-07** | Postgres connection pool boyutu = (worker thread × 2) + 5 buffer; pgBouncer (transaction mode) Faz C+ | Connection exhaustion = production outage |
| **D-08** | Tüm tenant-scoped query'ler `WHERE tenant_id = ?` eklenir; ORM repository pattern bu filter'ı zorunlu kılar | SQL injection değil, **business logic injection** önlenir |
| **D-09** | Secret'ler ortam değişkeninden okunur — kod, config dosyası, Docker image içinde **yasak** | Secret leak engellenme |
| **D-10** | Voice reference audio yalnızca object storage'da; DB'de URI + sha256 + size + sample-rate metadata | Postgres performansı, backup boyutu, replication maliyeti |
| **D-11** | Worker model'i lazy-load eder ama process boyunca canlı tutar; HTTP /admin/warmup ile eager trigger | Cold-start latency kullanıcıya yansımaz |
| **D-12** | Streaming TTS API **HTTP chunked primary** (`POST /v1/tts/stream`); long-running + presigned artifact için **async jobs** (`POST /v1/tts/jobs`); sync `POST /v1/tts` deprecated queue proxy (RFC 8594 sunset 2026-09-01). Duplex voice-agent (NIVA) bu yüzeyde değil — ayrı transport (WebRTC/gRPC) ile ayrı product surface olarak gelir | ElevenLabs / OpenAI Audio / Cartesia / MiniMax endüstri standardı; WebRTC SFU hop tek-yönlü TTS'e değer katmaz, vendor + ops kompleksitesi getirir. Önceki "WebRTC primary" karar 2026-05-24 reversed (bkz. [decisions/README.md](../decisions/README.md)) |
| **D-13** | Rate limit Redis sliding window (Lua atomic) — per-key + per-tenant aggregate; aşan istek 429 + `Retry-After` header | Multi-tenant fairness |
| **D-14** | Backpressure: Redis Streams queue depth `> N_workers × 4` → gateway 429 + `Retry-After` (queue dolduğunda istek almak `OOM` riskine girer) | Cascading failure önleme |
| **D-15** | Her metric label seti **bounded** — tenant_id, voice_id, status — sınırsız cardinality'li label yasak (örn. request_id label olarak değil, exemplar olarak) | Prometheus memory blow-up önleme |

---

## 7. Streaming protocol (uç nokta detayı)

### 7.1 HTTP chunked primary path

```
Client                             Gateway                       Redis            Worker
  │  POST /v1/tts/stream
  │  Authorization: Bearer <key>
  │  Accept: audio/wav
  ├──────────────────────────────►│
  │                               │  validate key + tenant + voice access
  │                               │  XADD nqai.tts.jobs
  │                               ├───────────────────►│
  │                               │                    │ XREADGROUP
  │                               │                    ├──────────────►│
  │                               │                    │               │ resolve ref/cache + generate
  │                               │                    │               │ XADD nqai.tts.results.{rid}
  │                               │                    │ ◄─────────────┤   (per-sentence, frame-by-frame
  │                               │ XREAD seq=0+        │               │    via iter_engine_chunks)
  │                               │ ◄──────────────────┤               │
  │  ◄═══ chunked WAV (audio/wav) ─┤
  │      RIFF header + PCM frames as they arrive
  │  ◄── trailer / connection close on `final` chunk
```

### 7.2 Async job + presigned artifact

Long-running iş veya batch flow için `POST /v1/tts/jobs` (Stripe `Idempotency-Key`) → 202 → `GET /v1/tts/jobs/{id}` polling → `audio_url` (presigned R2). Detay: [streaming-protocol.md §2](streaming-protocol.md).

### 7.3 Sync `/v1/tts` (DEPRECATED queue proxy)

`POST /v1/tts` aynı queue üzerinden çalışır; gateway result stream'i drain edip TEK WAV body döner. RFC 8594 `Deprecation: true` + `Sunset: Mon, 01 Sep 2026 00:00:00 GMT` + `Link: </v1/tts/jobs>; rel="successor-version"` header'ları. Yeni kod kullanmamalı; 2026-09-01'da 410.

### 7.4 Latency budget (p95 hedefler)

| Adım | Bütçe | Ölçüm |
|---|---|---|
| TLS handshake | 50 ms | Cloudflare edge logs |
| Gateway auth + validate + queue submit | 30 ms | `nqai_gateway_submit_seconds` |
| Redis XADD + XREADGROUP propagation | 5 ms | `nqai_queue_pickup_seconds` |
| Worker frontend normalize | 10 ms | `nqai_frontend_seconds` |
| VoxCPM2 ilk cümle generate (warmed) | 1500 ms (T4) / 500 ms (L4/A100) | `nqai_inference_first_sentence_seconds` |
| Worker XADD result + gateway XREAD | 5 ms | `nqai_result_propagation_seconds` |
| Gateway HTTP chunked first byte | 10 ms | `nqai_stream_first_byte_seconds` |
| **TTFB total** | **~1610 ms (T4) / 610 ms (L4)** | `nqai_tts_ttfb_seconds` |
| Sonraki cümleler (warm pipeline) | 800-1500 ms her biri | `nqai_inference_per_sentence_seconds` |

Hedef SLO'lar: TTFB p50 ≤ 800 ms (L4 ile), p95 ≤ 1.5 s. Bu Faz C'de Nano-vLLM ile gerçekleşir; Faz B sınırı T4 hardware'inde p95 ~2-3 s.

> Detaylı protocol spec + SDK örneği: [streaming-protocol.md](streaming-protocol.md).

---

## 8. Multi-tenancy + Auth modeli

### 8.1 Veri modeli (Postgres, özet)

```sql
-- Tenant: dış müşteri (NEEKO, NIVA, ...)
CREATE TABLE tenants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT NOT NULL UNIQUE,             -- "neeko-prod", "niva-prod"
    display_name  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | suspended | deleted
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- API key: tenant'a ait
CREATE TABLE api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    prefix        TEXT NOT NULL UNIQUE,             -- "nqai_prod_a1b2c3d4e5f6g7" (lookup index)
    secret_hash   TEXT NOT NULL,                    -- argon2id($argon2id$v=19$...)
    scopes        TEXT[] NOT NULL DEFAULT ARRAY['tts:read','tts:write'],
    rate_limit_per_minute INT NOT NULL DEFAULT 60,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at    TIMESTAMPTZ,
    last_used_at  TIMESTAMPTZ,
    label         TEXT                              -- "production", "staging"
);
CREATE INDEX ON api_keys (tenant_id);
CREATE INDEX ON api_keys (prefix) WHERE revoked_at IS NULL;

-- Voice: tenant-scoped catalog
CREATE TABLE voices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    voice_id            TEXT NOT NULL,              -- kebab-case
    display_name        TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'tr',
    gender              TEXT NOT NULL DEFAULT 'neutral',
    style_tags          TEXT[] NOT NULL DEFAULT '{}',
    reference_uri       TEXT NOT NULL,              -- s3://r2/voices/<tenant>/<voice>.wav
    reference_sha256    TEXT NOT NULL,
    reference_seconds   REAL NOT NULL,
    source              TEXT NOT NULL,
    license             TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_key      UUID REFERENCES api_keys(id),
    UNIQUE (tenant_id, voice_id)
);
CREATE INDEX ON voices (tenant_id);

-- Usage: time-series, ileride TimescaleDB hypertable
CREATE TABLE usage_records (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    api_key_id    UUID NOT NULL REFERENCES api_keys(id),
    voice_id      TEXT NOT NULL,
    request_id    UUID NOT NULL UNIQUE,             -- D-05 idempotency
    char_count    INT NOT NULL,
    duration_ms   INT NOT NULL,
    elapsed_ms    INT NOT NULL,
    status        TEXT NOT NULL,                    -- ok | error | timeout
    error_code    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON usage_records (tenant_id, created_at DESC);
CREATE INDEX ON usage_records (request_id);

-- Audit: append-only
CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_type    TEXT NOT NULL,                    -- 'api_key' | 'operator' | 'system'
    actor_id      UUID,
    tenant_id     UUID,
    action        TEXT NOT NULL,                    -- 'auth.success' | 'voice.create' | ...
    target_type   TEXT,
    target_id     TEXT,
    ip_addr       INET,
    user_agent    TEXT,
    payload       JSONB
);
CREATE INDEX ON audit_log (tenant_id, occurred_at DESC);

-- Operators: admin UI users
CREATE TABLE operators (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    roles         TEXT[] NOT NULL DEFAULT ARRAY['admin'],
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
```

> Tam schema, indices, RLS policies, migration policy: [data-model.md](data-model.md).

### 8.2 API key formatı + lifecycle

```
nqai_prod_a1b2c3d4e5f6g7_<40-char base62 secret>
└──┬──┘ └─┬──┘ └─────┬─────┘
   │      │           └─ secret (DB'de argon2id hash)
   │      └─ prefix (DB'de plain + index)
   └─ environment label
```

- **Generation:** Admin UI'dan POST → 14-char prefix + 40-char secret rastgele üretilir, secret tek seferlik dönüştür (sonra DB'de hash kalır)
- **Validation:** prefix DB'de bulunur → secret_hash argon2id ile compare → revoked_at NULL check → scope check → rate limit check
- **Rotation:** mevcut key revoked_at = now(), yeni key oluşturulur, 7 günlük grace window'da iki key de geçerli
- **Revoke:** UPDATE revoked_at = now() — cascade etkisi yok, sadece auth fail

> Tam auth spec + JWT operator flow + rate-limit algoritması: [auth-multi-tenant.md](auth-multi-tenant.md).

---

## 9. Yatay ölçek hesabı (20 → 200 user)

### Baseline (20 concurrent)

| Bileşen | Adet | Spec |
|---|---|---|
| Gateway | 2 | 2 vCPU each (HA + LB) |
| Worker | 4 | 1 GPU (L4/A10G) each, max 5 concurrent per worker |
| Postgres | 1 primary | 4 vCPU, 8 GB RAM |
| Redis | 1 | 2 vCPU, 4 GB RAM |

Throughput: 4 × 5 = 20 concurrent. p95 TTFB 1.5-2 s (Faz C Nano-vLLM ile).

### 200 concurrent ölçek

| Bileşen | Adet | Değişiklik |
|---|---|---|
| Gateway | 4-6 | uvicorn `--workers 4` per node, k8s HPA CPU-based |
| Worker | 16 | aynı L4 sınıfı, k8s GPU node pool |
| Postgres | 1 primary + 2 read replica | read-heavy (auth + voice manifest lookup), pgBouncer transaction pool |
| Redis | Sentinel (3 node) veya Cluster (6 node) | Stream throughput burada bottleneck olmaz (50k msg/s) |
| Object Storage | aynı R2 | egress = $0 |

Throughput: 16 × 5 = **80 concurrent steady** — peak 200'e queue ile yumuşatılır. Queue derinliği p95 ≤ 30 saniye target.

**Bottleneck noktaları (sırasıyla iddialı):**
1. **GPU başına eşzamanlılık** — VoxCPM2 thread-unsafe, worker process başına 1 inference; Nano-vLLM ile batched 4-8 → bottleneck rahatlar
2. **Postgres connection pool** — pgBouncer transaction mode + max_connections artırılır
3. **Redis Streams throughput** — tek instance 100k+ msg/s, Cluster gerektirmez (50k çok aşılırsa)
4. **R2 egress** — $0 olduğu için sınırsız practical

### Maliyet projeksiyon (aylık)

| Senaryo | Compute | Storage | Network | Toplam |
|---|---|---|---|---|
| 20-user baseline (Faz B) | ~50 EUR CPU + ~$200 GPU (kısmi) | $5 | $0 | **~$250/ay** |
| 50-user (Faz C exit) | ~80 EUR CPU + ~$500 GPU | $10 | $0 | **~$600/ay** |
| 200-user (Faz D+) | ~200 EUR CPU + ~$1800 GPU | $30 | $0 | **~$2200/ay** |

GPU maliyeti tüm masrafın %80'i — Nano-vLLM ile batching %3-4x throughput → maliyet düşer.

---

## 10. Faz A — Foundation (DB + Auth + Admin) [~6-8 net saat]

**Hedef:** Multi-tenant veri planı çalışır, 1 tenant tek user düzgün girer, audit'li.

| Adım | Çıktı | DoD |
|---|---|---|
| **A.1** | Postgres + Alembic + SQLAlchemy 2 async data model (§8.1 schema) | `alembic upgrade head` temiz, `pytest tests/test_data_model.py` yeşil |
| **A.2** | Repository pattern: `TenantRepo`, `ApiKeyRepo`, `VoiceRepo`, `UsageRepo`, `AuditRepo` (tüm tenant_id filter zorunlu — D-08) | her repo test'i yeşil, RLS smoke test ile cross-tenant isolation doğrulanır |
| **A.3** | Auth refactor: `src/server/auth.py` Postgres DB-backed; argon2id hash check; scope check; rate-limit hook | mevcut 55 test güncellenir, yeni auth test'leri eklenir |
| **A.4** | Data migration: `scripts/migrate_filesystem_to_db.py` — `configs/voices/*.yaml` + `data/reference-audio/*` → R2 upload + DB INSERT | idempotent, dry-run flag, 1 NEEKO voice migrate edilir |
| **A.5** | Admin endpoints: `POST /admin/tenants`, `POST /admin/tenants/{id}/keys`, `DELETE`, `GET /admin/usage` (operator JWT auth) | OpenAPI doc, basit happy-path test'i |
| **A.6** | Sade Admin UI: FastAPI + Jinja2 + HTMX (1 sayfa: tenant list + key generate butonu + revoke + son 30g usage) | `localhost:8000/admin` ile manuel demo |
| **A.7** | `docker-compose.dev.yaml`: gateway + postgres + redis (worker yok) | `docker compose up` ile lokal full stack |
| **A.8** | Object storage adapter: `src/storage/r2.py` — `upload_reference`, `download_reference`, `presigned_url` (boto3 veya minio client) | unit test (moto S3 mock) |

**Checkpoint A (gözlemlenebilir):**
> "Admin UI'dan 4 tenant + 4 key generate edildi, her key kendi tenant'ının voice'larını listeler, başkasını göremez. NEEKO referans audio R2'de, DB'de URI + sha256 tutulur. Mevcut TTS endpoint hâlâ tek-process (Faz B'de ayrılır)."

---

## 11. Faz B — Inference plane (queue + workers) [~7-9 net saat]

**Hedef:** Gateway GPU'dan bağımsız, worker'lar yatay ölçeklenir.

| Adım | Çıktı | DoD |
|---|---|---|
| **B.1** | `src/worker/` paketi: Redis Streams consumer (FastStream), VoxCPM2 engine + frontend orada yaşar, gateway'den taşınır | `python -m worker.main` ile worker tek başına ayağa kalkar |
| **B.2** | Job schema (msgspec.Struct): `TtsJob {request_id, tenant_id, voice_id, text, mode, stream_uri}`, `TtsResult {chunk_seq, pcm_bytes, sentence_text, final}` | unit test schema round-trip |
| **B.3** | Gateway refactor: `POST /v1/tts` → `XADD nqai.tts.jobs` + Redis pub/sub `nqai.tts.results.{request_id}` subscribe + WAV header + chunk yield → response stream | mevcut HTTP API contract'ı korunur, lokal e2e test |
| **B.4** | WebSocket endpoint: `/v1/tts/ws` (§7.1 protocol) — Starlette WS handler | basit JS client veya `websocat` ile manuel demo |
| **B.5** | Idempotency (D-05): gateway aynı `request_id` 2. kez görürse cache'lenen response döner; cache Redis HSET 24 saat TTL | duplicate request test |
| **B.6** | Backpressure (D-14): gateway her istek öncesi `XLEN nqai.tts.jobs` okur, threshold üstünde 429 + `Retry-After` | load test ile doğrulanır |
| **B.7** | At-least-once güvencesi (D-06): worker XACK sadece tüm chunk'lar gönderildikten sonra; XAUTOCLAIM 30 sn'de bir pending mesajları yeniden işler | chaos test: worker SIGKILL → başka worker devralır |
| **B.8** | `docker-compose.dev.yaml`: + redis + 1 worker (GPU passthrough) | full stack up, e2e test |

**Checkpoint B:**
> "Gateway + 1 worker. 5 concurrent request kuyrukta sıralanır, sıralı işlenir (single worker). Worker SIGKILL → başka worker devraldı (chaos test). Idempotency duplicate request'i yakaladı."

---

## 12. Faz C — Scale + Observability [~7-9 net saat]

**Hedef:** 20 concurrent gerçekten taşınıyor, dashboard'da görünüyor.

| Adım | Çıktı | DoD |
|---|---|---|
| **C.1** | Worker × 4 deploy (RunPod 4× L4 veya tek node multi-container) — Redis Streams consumer group otomatik load-balance eder | smoke 20 concurrent request, dağıtık tüketim doğrulanır |
| **C.2** | Prometheus exporter (gateway + worker + redis + postgres exporter) | `:9090/targets` yeşil |
| **C.3** | Grafana dashboard JSON (paneller: per-tenant request rate, TTFB p50/p95/p99, RTF, queue depth, GPU util, error rate, top voice IDs) | dashboard URL ekrana yansıyor |
| **C.4** | OpenTelemetry SDK + auto-instrumentation (FastAPI, asyncpg, redis); export → Tempo (Grafana stack) veya Honeycomb | trace exemplar'ları metric'lere bağlı |
| **C.5** | Structured JSON logs (`structlog`): tüm log'lar `{trace_id, request_id, tenant_id, voice_id, level, msg}` formatında; Loki agent gönderir | Grafana Logs'ta filtreli sorgu |
| **C.6** | Alertmanager → Slack webhook: SLO ihlali (p95 > 5s, error rate > %2, queue depth > 100) → alert | alert test ile manuel tetiklenir |
| **C.7** | Health endpoints: `GET /health/live` (process ayakta mı), `GET /health/ready` (DB + Redis + worker pool sağlıklı mı) | k8s liveness/readiness probe çalışır |
| **C.8** | Load test: `tests/load/k6_20_concurrent.js` — 20 vu, 5 dakika, p95 hedef ≤ 5 s | k6 raporu commit edilir |
| **C.9** | (Opsiyonel) Nano-vLLM swap: worker'ın inference adapter'ı `nano-vllm-voxcpm` kullanır; A/B test direct vs nano-vllm | RTF karşılaştırma decision log satırı |

**Checkpoint C:**
> "20 concurrent, p95 ≤ 5 s, error rate ≤ %1, 4 worker dağıtık, dashboard canlı, alert tetiklenir. Nano-vLLM swap RTF iki katı iyileştirme gösterirse v1.0'da default olur."

> Detaylı metric tanımları + alert rule'ları: [observability.md](observability.md).

---

## 13. Faz D — Production [~8-10 net saat]

**Hedef:** 4 client onboard, public domain, monitoring + alerting + CI/CD.

| Adım | Çıktı | DoD |
|---|---|---|
| **D.1** | Docker images: `nqai-voice-gateway`, `nqai-voice-worker`, `nqai-voice-admin` → GHCR | `docker pull ghcr.io/erdalgumuss/nqai-voice-gateway:latest` çalışır |
| **D.2** | GitHub Actions CI: lint (ruff) + test (pytest) + build (multi-arch) + push (GHCR) + deploy notify | her PR'da yeşil check, main merge → image build |
| **D.3** | Kubernetes manifest'ler (`deploy/k8s/`) veya Docker Compose prod profile; HPA gateway için CPU-based, worker için custom (Redis queue depth) | `kubectl apply -k deploy/k8s/overlays/prod` veya `docker compose -f docker-compose.prod.yaml up` |
| **D.4** | Cloudflare in front: TLS otomatik, custom domain `api.nqai.voice` (veya `voice.nqai.com`), R2 binding, rate-limit edge rule | curl ile public hit, TLS A+ rating |
| **D.5** | IaC (Terraform) ya da manuel script: Hetzner Cloud node provisioning + RunPod GPU pool + Postgres backup S3 | `terraform apply` ile reproducible deploy |
| **D.6** | Secret yönetimi: Doppler veya Infisical entegrasyonu, GitHub Actions secret rotation runbook | secret leak audit clean |
| **D.7** | Per-tenant Grafana dashboard (template variable tenant_id) | her tenant kendi paneli URL'i |
| **D.8** | Runbook'lar: `docs/runbooks/{onboard-tenant,rotate-api-key,worker-down,db-failover,incident-response}.md` | her runbook 1 senaryoyu adım adım açıklar |
| **D.9** | 4 client onboarding doc: `docs/onboarding/clients/{neeko,niva,neurocourse,naro}.md` — her birinde API key + voice catalog snippet + Python + JS örnek kod | client team tek tıklama ile başlar |
| **D.10** | SLO + SLA dokümantasyonu: `docs/sla/v1.md` — uptime 99.5%, p95 TTFB ≤ 1.5 s, support response 4h business | imzalı dahili dokuman |

**Checkpoint D:**
> "Production live. 4 tenant kendi key'leriyle bağlı. CI/CD her merge'de deploy. Aylık usage report otomatik. Pager rotation OK. Runbook'lar oturmuş."

---

## 14. Test stratejisi

| Katman | Araç | Kapsam | Faz'da |
|---|---|---|---|
| **Unit** | pytest + pytest-asyncio | Pure functions (frontend, normalize, segment, hash, validate) | A başlangıç |
| **Integration** | pytest + testcontainers (Postgres + Redis) | Repository CRUD, auth flow, queue submit/consume, idempotency | A |
| **Contract** | schemathesis veya FastAPI's TestClient + Pydantic | OpenAPI schema'sı backwards-compat (her PR'da check) | B |
| **E2E** | pytest + httpx + websockets (Python) | Client → gateway → worker → response, WebSocket + chunked path | B-C |
| **Load** | k6 (Grafana) | 20 vu (Faz C), 200 vu (Faz D); p95 latency + error rate hedefleri | C |
| **Chaos** | toxiproxy (network latency, packet loss), `kill -9` worker | At-least-once delivery, gateway timeout, DB connection loss | C |
| **Security** | OWASP ZAP + custom auth fuzz | API key brute-force, JWT tampering, RLS bypass attempt | D |
| **Audio quality** | Whisper-TR-WER + UTMOSv2 + WavLM-SECS (regression CI) | Eval set v0.1-mini'de baseline'dan sapma > %5 → fail | C-D |

---

## 15. Security defaults (FYI)

- TLS 1.3 only, HSTS preload, secure cookies (`SameSite=Strict`)
- API key 14-char prefix + 40-char secret base62 (~238 bit entropy), argon2id (m=64MB, t=3, p=4)
- JWT HS256 with key in env (Faz D'de RS256 + rotated keys)
- Rate limit per-key (default 60/min, tenant override), per-tenant (default 600/min), per-IP (DDoS — Cloudflare edge)
- CORS origins zorunlu allowlist (no `*` in prod)
- Audit log her auth deneme + her admin işlem + her voice CRUD + her usage record
- Secret leak detection: GitHub secret scanning + pre-commit hook (detect-secrets)
- Dependency vuln: Dependabot + `pip-audit` CI'da fail-on-critical

---

## 16. Karar matrisi (özet)

Tek satır cevaplar, detay → bileşen matrisi (§4) veya zorunlu kararlar (§6).

| Soru | Cevap | Doğal şekilde değişebilir mi |
|---|---|---|
| **DB?** | PostgreSQL 16 self-hosted (start), managed (Faz D) | Faz E multi-region için YugabyteDB |
| **Queue?** | Redis Streams + FastStream | Faz E NATS JetStream (multi-region için) |
| **Object storage?** | Cloudflare R2 (zero egress fee) | Backblaze B2 alternatif |
| **Inference runtime?** | `voxcpm` (Faz A-B) → `nano-vllm-voxcpm` (Faz C+) | Triton Faz D B2B SDK için |
| **Auth?** | API key + argon2id + DB scope + Redis rate limit | Yok — bu pattern oturuyor |
| **Admin UI?** | FastAPI + Jinja2 + HTMX (server-side) | Next.js Faz E ekip büyürse |
| **Streaming?** | HTTP chunked primary (`/v1/tts/stream`) + async jobs (`/v1/tts/jobs`) + sync deprecated proxy | Opsiyonel WS (`/v1/tts/ws`) Faz B.2'de; duplex voice-agent ayrı product surface |
| **Container?** | Docker Compose (dev) → Kubernetes (prod, Faz D+) | Nomad alternatif (küçük ekip için daha basit) |
| **GPU cloud?** | RunPod (start) → Modal/Lambda (Faz D karşılaştırma) | self-managed Faz E (>$5000/ay GPU spend'de) |
| **Observability?** | Prometheus + Grafana + Loki + OTel | Honeycomb traces için Faz D'de değerlendirilir |
| **CI/CD?** | GitHub Actions + GHCR | GitLab CI Faz E |
| **TLS + edge?** | Cloudflare (free → pro) | Direkt Let's Encrypt + nginx (Cloudflare bağımlılığı istenmezse) |

---

## 17. Açık riskler + bilinmeyenler

| # | Risk | Etki | Azaltma |
|---|---|---|---|
| **R-01** | Nano-vLLM voxcpm port'u single-maintainer (a710128) — uzun vade sürdürülebilirlik? | Faz C performans hedefi | Triton fallback hazır (Faz D); fork option |
| **R-02** | VoxCPM2 Türkçe child-directed kalite henüz saha-test değil | Premium hedefe sapma | Faz 1 eval suite (`v1.0-full` 120 cümle) sayısal ölçer; gerekirse LoRA hattı (zaten Faz 3'te) |
| **R-03** | Cloudflare R2 free tier 10 GB + 1M Class A op/ay — büyük dataset için yetmez | Faz D'de upgrade | Backblaze B2 alternatif hazır; aylık $5 / 50 GB |
| **R-04** | Postgres connection pool exhaustion — yüksek concurrency'de sık görülen prod problem | Outage | pgBouncer transaction mode Faz C; max_connections + pool size load test ile kalibre |
| **R-05** | WebSocket connection limit (per IP) — Cloudflare 1k/IP/min | 200 user için yetersiz değil ama dikkat | enterprise plan veya self-managed reverse proxy alt opsiyon |
| **R-06** | VoxCPM2 model file 4 GB — worker cold-start 90-120 s | İlk pod ramp-up gecikmesi | model cache PVC (Kubernetes) veya R2 → local disk pre-pull init container |
| **R-07** | GPU node spot eviction (RunPod) — anlık worker kaybı | Throughput dalgalanması | At-least-once + on-demand backup pool (paralı, %20 mix) |
| **R-08** | Multi-tenant DB partition stratejisi — 4 tenant şimdi OK, 50+ tenant'ta partition? | Faz E + ölçekte | TimescaleDB hypertable for usage_records, RLS for hot tables |

---

## 18. Sözlük

- **Tenant:** account/workspace — billing + auth + voice ownership birimi. Genellikle bir organizasyon veya isolated bir geliştirme ortamı (`erdal-dev`, `neeko-prod`, `partner-x-staging`). Ürünle 1:1 değil
- **App / Product:** tenant'ın hizmet ettiği logical client (NEEKO toy, NIVA, NeuroCourse, NARO). `X-NQAI-App` header ile `usage_records.app_label` üzerinden tracked. Auth/billing birimi değil
- **API key:** tenant'a ait Bearer credential — bir tenant N anahtar üretebilir
- **Operator:** admin UI'a giren NQAI çalışanı (JWT'li ayrı auth)
- **Voice:** referans audio + manifest = bir karakter sesi. `owner_tenant_id` sahibi tenant'ı belirler
- **Voice visibility:** `private` (sadece owner), `shared` (owner + voice_access grants), `public` (her tenant)
- **Voice access:** `voice_access` tablosundaki explicit cross-tenant grant (sharing için)
- **Request:** istemcinin tek bir `POST /v1/tts` çağrısı
- **Job:** queue'da bir request'in temsili (TtsJob struct)
- **Chunk:** worker'ın gateway'e gönderdiği bir cümle PCM'i
- **TTFB:** Time To First Byte — istek başlangıcından ilk audio chunk'a kadar
- **RTF:** Real-Time Factor — `elapsed_time / audio_duration`; 1.0 = real-time, < 1.0 = faster than real-time
- **SLO:** Service Level Objective (içeride hedef)
- **SLA:** Service Level Agreement (client'a verilen taahhüt)
- **DoD:** Definition of Done

---

## 19. İlgili belgeler (yazılacak)

- [`data-model.md`](data-model.md) — Postgres schema tam DDL + indices + RLS + migration policy
- [`streaming-protocol.md`](streaming-protocol.md) — WebSocket message types + chunked WAV detayı + SDK örneği
- [`auth-multi-tenant.md`](auth-multi-tenant.md) — API key format + argon2id detayı + JWT + rate-limit Lua + audit log
- [`observability.md`](observability.md) — Prometheus metric tanımları + Grafana dashboard JSON + OTel attribute keys + alert rule'ları
- [`runbooks/`](../runbooks/) — operasyon prosedürleri (Faz D)
- [`onboarding/clients/`](../onboarding/clients/) — client başına onboarding (Faz D)
- [`sla/v1.md`](../sla/v1.md) — SLA dokümantasyonu (Faz D)
