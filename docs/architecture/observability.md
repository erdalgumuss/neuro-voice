# Observability (kanonik)

**Doc owner:** DevOps lead · **Bağlı:** [scale-roadmap.md §12](scale-roadmap.md)
**Sürüm:** v1 · **Stack:** Prometheus + Grafana + Loki + OpenTelemetry (→ Tempo veya Honeycomb)

> "Üretemediğini ölçemezsin" — bu doc her metrik, log ve trace'in adını, formatını ve nedenini tanımlar. Ekibin her servis ekleme/refactor'unda buraya satır eklemesi şart (D-15).

---

## 0. Felsefe

**RED method** (Rate, Errors, Duration) her servis için zorunlu.
**USE method** (Utilization, Saturation, Errors) her infra component için.
**Trace exemplar**'ları metric'lere bağlı — bir p99 spike görünce o trace'i tek tıkla aç.

Metric cardinality budget: **her metric ≤ 10k unique label combo** (cluster genelinde). Sınırsız label (request_id, voice_id raw) **yasak** — trace olarak alınır.

---

## 1. Prometheus metric kataloğu

Her metric `nqai_` prefix'i ile. Naming: [Prometheus best practices](https://prometheus.io/docs/practices/naming/) takip eder.

### 1.1 Gateway metrics

| Metric | Type | Labels | Tanım |
|---|---|---|---|
| `nqai_gateway_requests_total` | Counter | `method`, `endpoint`, `status_code`, `tenant_slug` | Toplam HTTP istek sayısı |
| `nqai_gateway_request_duration_seconds` | Histogram | `endpoint`, `tenant_slug`, `status_class` | End-to-end gateway latency; buckets: 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10 |
| `nqai_gateway_active_connections` | Gauge | `protocol` (`http`,`ws`) | Aktif TCP/WS connection sayısı |
| `nqai_gateway_auth_attempts_total` | Counter | `result` (`success`,`fail`), `reason` | Auth doğrulama sonuçları |
| `nqai_gateway_rate_limited_total` | Counter | `tenant_slug`, `key_prefix_hash` | Rate-limited istek sayısı |
| `nqai_gateway_backpressure_total` | Counter | `reason` (`queue_full`,`worker_unavailable`) | 503 backpressure event'leri |

### 1.2 TTS pipeline metrics (gateway + worker)

| Metric | Type | Labels | Tanım |
|---|---|---|---|
| `nqai_tts_requests_total` | Counter | `tenant_slug`, `voice_id`, `mode`, `status` | TTS istek sayısı |
| `nqai_tts_ttfb_seconds` | Histogram | `tenant_slug`, `voice_id` | Time-to-first-byte; buckets: 0.1, 0.25, 0.5, 0.8, 1.5, 3, 5, 10 |
| `nqai_tts_e2e_duration_seconds` | Histogram | `tenant_slug`, `voice_id` | Tüm sentez bitirme süresi; buckets: 0.5, 1, 2, 5, 10, 20, 30 |
| `nqai_tts_audio_duration_seconds` | Histogram | `tenant_slug`, `voice_id` | Üretilen ses süresi; buckets: 0.5, 1, 2, 5, 10, 30, 60 |
| `nqai_tts_rtf` | Histogram | `tenant_slug`, `voice_id`, `worker_id` | Real-time factor; buckets: 0.05, 0.1, 0.2, 0.5, 1, 2, 5 |
| `nqai_tts_chars_total` | Counter | `tenant_slug`, `voice_id` | Sentezlenen karakter sayısı (billing) |
| `nqai_tts_sentences_per_request` | Histogram | `tenant_slug` | Cümle sayısı dağılımı; buckets: 1, 2, 5, 10, 20, 50 |

### 1.3 Queue metrics (Redis Streams)

| Metric | Type | Labels | Tanım |
|---|---|---|---|
| `nqai_queue_depth` | Gauge | `stream` (`nqai.tts.jobs`) | XLEN — bekleyen iş sayısı |
| `nqai_queue_pending_count` | Gauge | `stream`, `consumer_group` | XPENDING — claim'lenmiş ama ack'lenmemiş |
| `nqai_queue_pickup_seconds` | Histogram | `stream` | XADD'den XREADGROUP'a süre |
| `nqai_queue_ack_duration_seconds` | Histogram | `stream` | İlk XADD'den XACK'e (job lifetime) |
| `nqai_queue_autoclaim_total` | Counter | `stream`, `consumer_group`, `from_consumer`, `to_consumer` | Crash recovery devralma sayısı |
| `nqai_queue_dlq_total` | Counter | `stream`, `reason` | Dead-letter queue'ya yazılan iş (max retry) |

### 1.4 Worker metrics (GPU)

| Metric | Type | Labels | Tanım |
|---|---|---|---|
| `nqai_worker_jobs_total` | Counter | `worker_id`, `status` (`ok`,`error`,`timeout`,`cancelled`) | İşlenen job sayısı |
| `nqai_worker_inference_seconds` | Histogram | `worker_id`, `voice_id` | Model.generate() süresi |
| `nqai_worker_frontend_seconds` | Histogram | `worker_id` | normalize_text + segment_sentences süresi |
| `nqai_worker_active_jobs` | Gauge | `worker_id` | Şu an işlenen iş (genelde 0 veya 1) |
| `nqai_worker_model_loaded` | Gauge | `worker_id`, `model_id`, `version_sha` | 1 = yüklü, 0 = lazy/cold |
| `nqai_worker_model_load_seconds` | Histogram | `worker_id` | Cold-load süresi |
| `nqai_worker_gpu_memory_bytes` | Gauge | `worker_id`, `gpu_index`, `state` (`used`,`free`) | `nvidia-smi` exporter |
| `nqai_worker_gpu_utilization_ratio` | Gauge | `worker_id`, `gpu_index` | 0.0-1.0 |
| `nqai_worker_inference_retry_total` | Counter | `worker_id`, `reason` (`bad_case`,`oom`,`timeout`) | VoxCPM2 retry_badcase trigger'ları |

### 1.5 Database + Redis metrics

Standart exporter'lar:
- **postgres_exporter** (Wrouesnel) — connection count, query duration, replication lag, lock waits, cache hit ratio
- **redis_exporter** (oliver006) — commands/sec, memory, eviction, stream lengths, pubsub channels

NQAI özel:

| Metric | Type | Labels | Tanım |
|---|---|---|---|
| `nqai_db_repo_query_seconds` | Histogram | `repo`, `op` (`select`,`insert`,`update`) | Application-level query latency |
| `nqai_db_pool_in_use` | Gauge | `pool` | Active DB connection sayısı |

### 1.6 Object storage metrics

| Metric | Type | Labels | Tanım |
|---|---|---|---|
| `nqai_storage_operations_total` | Counter | `op` (`get`,`put`,`delete`), `bucket`, `status` | R2 operasyon sayısı |
| `nqai_storage_bytes_transferred_total` | Counter | `op`, `direction` (`in`,`out`) | Veri transferi (billing kontrol) |
| `nqai_storage_operation_duration_seconds` | Histogram | `op` | R2 API latency |

---

## 2. SLI + SLO + alert rules

### 2.1 SLO tablosu (Faz C exit hedef)

| Service | SLI | SLO target | Measurement window |
|---|---|---|---|
| TTS endpoint availability | (1 - 5xx_rate) | ≥ 99.5% | 30-day rolling |
| TTS TTFB | p95 of `nqai_tts_ttfb_seconds` | ≤ 1.5 s | 1h sliding |
| TTS E2E latency | p95 of `nqai_tts_e2e_duration_seconds` | ≤ 5 s | 1h sliding |
| Auth latency | p95 of auth endpoints | ≤ 200 ms | 1h sliding |
| Queue pickup | p95 of `nqai_queue_pickup_seconds` | ≤ 100 ms | 1h sliding |
| Worker availability | (1 - worker_error_rate) | ≥ 99% | 30-day rolling |

### 2.2 Alert rules (Prometheus)

```yaml
groups:
- name: nqai_tts_slo
  rules:
  - alert: TTS_TTFB_p95_HIGH
    expr: |
      histogram_quantile(0.95,
        sum by (le) (rate(nqai_tts_ttfb_seconds_bucket[5m]))
      ) > 1.5
    for: 10m
    labels: {severity: warning, team: backend}
    annotations:
      summary: "TTS p95 TTFB > 1.5s for 10 min"
      runbook: "https://nqai.notion.site/runbook-tts-latency"

  - alert: TTS_ERROR_RATE_HIGH
    expr: |
      sum(rate(nqai_tts_requests_total{status="error"}[5m]))
        / sum(rate(nqai_tts_requests_total[5m])) > 0.02
    for: 5m
    labels: {severity: critical, team: backend}
    annotations:
      summary: "TTS error rate > 2% for 5 min"

  - alert: QUEUE_DEPTH_HIGH
    expr: nqai_queue_depth{stream="nqai.tts.jobs"} > 100
    for: 3m
    labels: {severity: warning}

  - alert: QUEUE_PENDING_BACKLOG
    expr: nqai_queue_pending_count > 50
    for: 5m
    labels: {severity: critical}
    annotations:
      summary: "Pending jobs not being acked — worker stuck?"

  - alert: WORKER_GPU_OOM
    expr: increase(nqai_worker_inference_retry_total{reason="oom"}[5m]) > 3
    for: 1m
    labels: {severity: critical}

  - alert: WORKER_GPU_MEM_EXHAUSTED
    expr: nqai_worker_gpu_memory_bytes{state="free"} < 1e9
    for: 5m
    labels: {severity: warning}

  - alert: AUTH_FAIL_SPIKE
    expr: rate(nqai_gateway_auth_attempts_total{result="fail"}[1m]) > 10
    for: 2m
    labels: {severity: warning, team: security}
    annotations:
      summary: "Possible brute-force on API keys"

  - alert: DB_CONNECTION_POOL_NEAR_LIMIT
    expr: nqai_db_pool_in_use / (nqai_db_pool_in_use + on() pg_settings_max_connections) > 0.85
    for: 5m
    labels: {severity: critical}

  - alert: TENANT_RATE_LIMIT_RUNAWAY
    expr: rate(nqai_gateway_rate_limited_total[5m]) > 5
    for: 10m
    labels: {severity: info, team: account_management}
    annotations:
      summary: "Tenant {{ $labels.tenant_slug }} consistently rate-limited — quota raise?"
```

Alertmanager routes → Slack `#nqai-alerts` (warning), PagerDuty `nqai-oncall` (critical).

---

## 3. Grafana dashboard'lar

### 3.1 NQAI Voice — Overview

**Top row (single-stat):**
- Request rate (TTS req/s)
- TTFB p50 / p95 / p99
- Error rate (%)
- Active concurrent jobs
- Queue depth
- GPU utilization avg

**Mid row (time-series):**
- Request rate per tenant (stacked)
- TTFB p50/p95 trend
- Error rate per tenant
- Queue depth + pending count

**Bottom row (heatmap + table):**
- TTFB latency heatmap (revealing tail spike pattern)
- Top 10 slowest voice IDs (table)
- Top 10 error codes (table)

### 3.2 NQAI Voice — Tenant Detail (template variable: tenant_slug)

Per-tenant view: aynı paneller ama tek tenant'a filtered.

### 3.3 NQAI Voice — Worker Health

- GPU memory used/free per worker
- Inference duration per voice_id
- Retry rate per worker
- Model load events (annotations)
- Pending jobs claimed by worker

### 3.4 NQAI Voice — DB + Queue

- pgBouncer pool stats
- Postgres query duration histogram (top slow queries)
- Connection count
- Replication lag (Faz C+)
- Redis Streams XLEN + XPENDING per stream
- Redis memory + eviction rate

### 3.5 Dashboard JSON repo path

```
deploy/grafana/dashboards/
├── nqai_voice_overview.json
├── nqai_voice_tenant_detail.json
├── nqai_voice_worker_health.json
└── nqai_voice_db_queue.json
```

Provisioning: `deploy/grafana/provisioning/dashboards/nqai.yaml` ile auto-load.

---

## 4. Structured logging (Loki)

### 4.1 Format

Tüm log'lar **JSON** + zorunlu alanlar:

```json
{
  "ts": "2026-05-24T03:42:18.123456Z",
  "level": "info",
  "service": "gateway",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "span_id": "b7ad6b7169203331",
  "tenant_slug": "neeko-prod",
  "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA",
  "voice_id": "neeko-v01",
  "msg": "tts request accepted",
  "queue_position": 2
}
```

Python: `structlog 24+` + `python-json-logger` veya pure `structlog.processors.JSONRenderer`.

### 4.2 Log level guidance

| Level | Ne için |
|---|---|
| `debug` | Geliştirme; production'da disabled |
| `info` | Anlamlı state geçişleri (auth success, job accepted, worker started) |
| `warning` | Recoverable hata, retry triggered, deprecation kullanımı |
| `error` | Request fail, exception caught and handled, customer-facing problem |
| `critical` | Service-impacting, immediate attention (DB down, worker pool empty) |

PII yasak — text içeriği, password, API secret asla log'a yazılmaz. `text_char_count` OK, `text` HASTA.

### 4.3 Loki query örnekleri

```logql
# Tenant'ın son 1 saatteki error'ları
{service="gateway", tenant_slug="neeko-prod", level="error"} |= ""

# Belirli trace_id'nin tüm log satırları (cross-service)
{service=~"gateway|worker"} | json | trace_id="0af7651916cd43dd8448eb211c80319c"

# Auth fail spike
sum by (tenant_slug) (rate({service="gateway", msg=~".*auth fail.*"}[5m]))
```

### 4.4 Retention

- Hot (Loki object store): 30 gün, query-friendly
- Cold (R2 archive): 1 yıl, compliance için, query yavaş

---

## 5. Distributed tracing (OpenTelemetry)

### 5.1 Auto-instrumentation

```python
# src/server/instrumentation.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

def setup_otel(app, service_name: str):
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
    )))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    AsyncPGInstrumentor().instrument()
    RedisInstrumentor().instrument()
```

### 5.2 Manual span'ler (önemli iş yolları)

```python
tracer = trace.get_tracer("nqai-voice")

with tracer.start_as_current_span("tts.synthesize") as span:
    span.set_attribute("nqai.tenant_slug", tenant.slug)
    span.set_attribute("nqai.voice_id", voice.voice_id)
    span.set_attribute("nqai.request_id", request_id)
    span.set_attribute("nqai.text_chars", len(text))
    # ...
    span.set_attribute("nqai.audio_duration_ms", result.duration_ms)
    span.set_attribute("nqai.rtf", result.rtf)
```

### 5.3 Standart span attribute'leri

| Attribute | Tip | Açıklama |
|---|---|---|
| `nqai.tenant_slug` | string | Tenant identifier |
| `nqai.request_id` | string | Client veya gateway tarafından üretilen UUID |
| `nqai.voice_id` | string | Voice catalog key |
| `nqai.mode` | string | TTS mode |
| `nqai.text_chars` | int | Karakter sayısı |
| `nqai.sentence_count` | int | Cümle sayısı |
| `nqai.audio_duration_ms` | int | Üretilen ses süresi |
| `nqai.rtf` | float | Real-time factor |
| `nqai.worker_id` | string | Hangi worker process'i tarafından işlendi |
| `nqai.queue_wait_ms` | int | Queue'da bekleme süresi |
| `nqai.cache_hit` | bool | Idempotency cache hit'i |

### 5.4 Sampling

- **Always-sample** (100%): error span'ler, slow span'ler (> 2× p95)
- **Probabilistic** (1%): normal trafik
- **Faz D**: tail-based sampling Tempo/Honeycomb tarafında

### 5.5 Backend seçimi

| Faz | Backend | Sebep |
|---|---|---|
| Faz A-B | Yok (sadece log'lar) | Setup overhead yok |
| Faz C | Tempo (Grafana self-hosted) | OSS, tek Grafana stack |
| Faz D opsiyonel | Honeycomb managed | BubbleUp + query speed önemliyse |

---

## 6. Health checks

### 6.1 `/health/live` (liveness)

```python
@app.get("/health/live")
async def liveness():
    return {"status": "alive", "ts": time.time()}
```

Sadece process ayakta mı kontrol. K8s liveness probe.

### 6.2 `/health/ready` (readiness)

```python
@app.get("/health/ready")
async def readiness():
    checks = {
        "db": await check_db_ping(),
        "redis": await check_redis_ping(),
        "worker_pool": await check_worker_heartbeat_count(),
    }
    healthy = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not_ready", "checks": checks}
    )
```

K8s readiness probe — fail → load balancer'dan çıkarılır, traffic almaz.

### 6.3 `/health/deps` (operatör için, auth'lu)

Detaylı dep durumu: DB version, Redis info, worker pool size + last heartbeat, model loaded versions.

---

## 7. Cost monitoring

Observability sadece SLO için değil, finansal cost için de:

| Metric | Anlam |
|---|---|
| GPU saatleri (RunPod billing API → Prometheus) | $/saat × usage |
| R2 storage + operations | Free tier'a yakınlık |
| Bandwidth (Cloudflare analytics → Prometheus exporter) | Egress monitoring |
| `nqai_tts_chars_total` per tenant | Billing source-of-truth |

Aylık cost report Grafana dashboard'ında, anomaly alert (cost > %20 ay-üstü).

---

## 8. Incident response integration

Alert → Slack/PagerDuty → on-call engineer. Runbook her alert'in annotation'ında. Postmortem template `docs/runbooks/postmortem-template.md`.

SLO budget tracking: Grafana SLO panel (error budget kalan %), her ay sprint planning'de gözden geçirilir.

---

## 9. Yapılacaklar (Faz takvimine bağlı)

| Faz | Aksiyon |
|---|---|
| **A** | Structured logging (structlog JSON), basic `/health/live` |
| **B** | Worker'da log emit, basic latency log |
| **C** | Full Prometheus + Grafana + alertmanager + load test |
| **D** | OTel traces + Loki + Honeycomb opsiyonel + cost dashboard + on-call rotation |
