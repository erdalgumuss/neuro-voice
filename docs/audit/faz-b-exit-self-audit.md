# Faz B Exit Self-Audit — B.1 + B.1.5 birleşik kapanış

**Tarih:** 2026-05-24 · **Range:** `99a010d`..`110ddb9` (B.1 girişinden bugüne) · **Suite:** 284/284 · **Lint:** clean

Bu doc Faz B'nin (B.1 + B.1.5 hardening + frame-by-frame bridge + WebRTC reversal + cleanup) **birleşik** çıkış denetimidir. AGENTS.md'nin "Definition of Done" listesini madde madde kod kanıtıyla karşılaştırır, gerçek açıkları işaretler, ve Faz C scope'unu net bırakır.

> İlgili checkpoint'ler:
> - [`checkpoint-2026-05-24-faz-a-exit.md`](checkpoint-2026-05-24-faz-a-exit.md) — Faz A bitiş + B planı
> - [`checkpoint-2026-05-24-faz-b1-exit.md`](checkpoint-2026-05-24-faz-b1-exit.md) — B.1 ilk çıkış (pre-hardening snapshot)
> - [`checkpoint-2026-05-24-faz-b1.5-exit.md`](checkpoint-2026-05-24-faz-b1.5-exit.md) — B.1.5 frame-by-frame + reversal kapanışı

---

## 1. AGENTS.md DoD — kod-kanıtlı durum tablosu

### 1.1 B.1 "distributed correctness" DoD

| DoD maddesi | Durum | Kanıt |
|---|---|---|
| `python -m worker.main` boots a real worker | ✅ | [`src/worker/main.py`](../../src/worker/main.py); periodic XAUTOCLAIM (`NQAI_WORKER_XAUTOCLAIM_INTERVAL_S=30`); warmup-on-boot (`NQAI_WARMUP_ON_BOOT=true`) |
| Gateway and worker are separately runnable | ✅ | Compose: gateway default profile, worker `--profile gpu`. `grep -rn 'from worker\.' src/server/` → **boş** |
| Gateway no longer needs model inference for `/v1/tts` | ✅ | `server/main.py` engine import etmiyor; sync `/v1/tts` queue üzerinden proxy ([streaming-protocol.md §3](../architecture/streaming-protocol.md)) |
| Async job E2E passes (gateway + worker + fake engine/R2) | ✅ | `tests/test_async_e2e.py` (8 test); 284 suite içinde |
| Sync `/v1/tts` queue-proxied + Deprecation header | ✅ | `server/main.py` POST `/v1/tts` queue+result drain; `Deprecation: true` + `Sunset: 2026-09-01` + `Link: </v1/tts/jobs>; rel="successor-version"` |
| Worker crash/retry semantics covered by tests | ✅ | `tests/test_worker_consumer.py` (9 test): success XACK, poison drain, transient no-XACK, unknown no-XACK, XAUTOCLAIM recovery |
| Transient/unknown failures bounded retry + DLQ + terminal side effects | ✅ | `consumer.py:140-144` `NQAI_WORKER_MAX_RETRIES=3` + `NQAI_WORKER_DLQ_STREAM=nqai.tts.jobs.dlq`; `_maybe_dlq_and_ack` PEL `times_delivered` üzerinden okur; max sonrası DLQ XADD + idem.fail + usage error + XACK |
| Result stream retry dedupe (`seq` + cleanup) | ✅ | `result_stream.py:69` `seen_seq: set[int]`; line 112-119 `if chunk.seq in seen_seq and not is_terminal: continue`; cancel/finish'te `DEL` (line 72-78) |
| Queue backpressure shared across sync/stream/jobs | ✅ | `_check_queue_depth_or_503` helper `server/main.py:697`; çağrı: line 481, 592, 790 (sync, stream, jobs) |
| Usage rows include latency metadata for B.1.5 waterfall | 🟡 | `queue_wait_ms` + `inference_ms` persisted ([db/models.py:401-402](../../src/db/models.py), migration `0003`). Diğer waterfall noktaları (`first_audio_ms`, `worker_pickup_ms`, `reference_resolve_ms`, `gateway_first_byte_ms`) **computed but not persisted** (`pipeline.py:336-347` `first_audio_ms` lokal değişken) |
| R2 reference cache bounded eviction | ✅ | `storage/r2.py:256-288` `_evict_if_needed` size-based LRU; LoRA cache `worker/engine.py:110` `NQAI_LORA_CACHE_SIZE=3` |

**B.1 sonuç:** 11/11 madde ✅ veya 🟡 (sadece waterfall persistence yarım). Distributed correctness omurgası sağlam.

### 1.2 B.1.5 "latency" DoD

| DoD maddesi | Durum | Kanıt |
|---|---|---|
| First audio result stream'e full generation bitmeden XADD'lensin | ✅ | `tests/test_worker_pipeline.py::test_pipeline_publishes_first_chunk_before_engine_finishes` (stub engine 50ms sleep ile, ilk publish ikinci yield'den önce); `tests/test_async_e2e.py::test_first_chunk_in_result_stream_before_engine_finishes` (result-stream seviyesinde `xlen > 0` while `acked == 0`) |
| `POST /v1/tts/stream` chunked HTTP primary live path | ✅ | `server/main.py` `/v1/tts/stream` queue proxy + `StreamingResponse(audio/wav, chunked)`; `consume_result_stream(block_ms=100)`. LiveKit/WebRTC scaffold yok (`grep -rn 'livekit\|LiveKit' src/` sadece historical decision/comment satırları) |
| `iter_engine_chunks` thread→asyncio bridge | ✅ | `worker/live.py` `iter_engine_chunks` async generator; `grep -rn 'list(engine.synthesize_stream\|list(.*synthesize_stream' src/` → boş (sadece `worker/live.py:3` docstring'de historical referans) |
| Warm worker / reference / adapter cache (bounded LRU, env-tunable) | 🟡 | R2 cache: ✅ `storage/r2.py` size-based LRU. LoRA cache: ✅ `NQAI_LORA_CACHE_SIZE=3`. Per-voice sticky routing: ❌ — çoklu voice worker'da cold-load hot path'te kalır. Warmup-on-boot tek voice için |
| R2 artifact finalization chunks AFTER published | ✅ | `pipeline.py:369-390` archive adımı publish_chunk loop'undan **sonra**; commit-before-final pattern (`pipeline.py:21` "ONLY AFTER commit succeeds: publish_final") |
| Measured latency waterfall reachable from `usage_records` | 🟡 | Persisted: `queue_wait_ms`, `inference_ms`. **Eksik**: `first_audio_ms`, `worker_pickup_ms`, `reference_resolve_ms`, `first_pcm_ms`, `gateway_first_byte_ms`. Bunlar pipeline içinde computed ama DB'ye yazılmıyor — usage_records'tan tek-query ile waterfall okunamıyor |

**B.1.5 sonuç:** 4/6 madde ✅, 2/6 🟡. Latency anti-pattern öldü, transport endüstri standardına oturdu, ama waterfall persistence yarım + per-voice cache stratejisi yok.

---

## 2. Stub engine ölçümü vs gerçek ölçüm — kritik kabul

**Doğrulanan invariants (stub engine ile):**
- Pipeline drain pattern öldü
- İlk chunk ikinci sentence yield gelmeden publish ediliyor
- HTTP wire seviyesi httpx ASGITransport buffer'ı yüzünden ölçüm yapamıyor; result-stream seviyesinde ölçüm yapıldı

**Henüz doğrulanmamış (gerçek model gerekli):**
- VoxCPM2 ile ilk PCM frame ne zaman çıkıyor? (Faz B.1.5 hedef 80-100ms)
- Reference audio resolve (R2 download + librosa) ne kadar sürüyor warm/cold?
- VoxCPM2 "first sentence inference" L4'te gerçekten 500ms mi?
- Gateway HTTP chunked first-byte latency'si Redis XADD'den ne kadar sonra?

**Bu açık Faz B'yi "stub engine ile invariant'ı pinleyen" boyutta yapıyor — ürün metriği boyutunda değil.** Gerçek waterfall sayıları olmadan Faz C'nin Prometheus exporter + SLO hedef belirleme işi temelsiz olur.

---

## 3. Faz B'de yapılmayan + Faz C'ye taşınanlar (net liste)

### 3.1 Latency waterfall persistence (Faz B → Faz C taşıma)

**Eksik kolonlar (usage_records'a eklenecek):**
- `first_audio_ms` — XADD ilk chunk'a kadar geçen süre (pipeline'da var, persisted değil)
- `worker_pickup_ms` — XADD'den XREADGROUP'a
- `reference_resolve_ms` — R2/local lookup + librosa resample
- `first_pcm_ms` — engine ilk PCM frame yield
- `gateway_first_byte_ms` — gateway client'a ilk byte'ı yazana kadar (gateway-side tracking gerek)

**Çözüm:** `migrations/versions/2026_XX_XX_000X_usage_waterfall_metrics.py` — 5 nullable int column ekle + `repos/usage.py::create()` parametreleri + `pipeline.py` + `result_stream.py` (gateway-side) bunları doldursun.

### 3.2 Real-model first-audio benchmark (Faz B doğrulama)

L4 / A100 GPU'da gerçek VoxCPM2 ile `POST /v1/tts/stream` end-to-end ölçüm — sayı `docs/audit/checkpoint-2026-05-24-faz-b1.5-exit.md` Section 8'e gerçek rakam olarak girer. Şu an "stub engine ile kanıtlı" notu var; gerçek model ile p50/p95 first_audio_ms ölçüldükten sonra "VoxCPM2 ile p50=Xms p95=Yms" satırı eklenir.

### 3.3 Per-voice sticky routing + cache pre-warm (Faz C)

Şu an worker boot'ta tek voice warmup. Çoklu voice/LoRA için:
- Consumer group içinde voice_id-aware routing (Redis hash slot veya separate streams)
- Worker `voice_pool` bounded LRU — VoxCPM2 + LoRA + reference cached
- Cold-load metric: `nqai_worker_cold_load_seconds{voice_id}` Prometheus counter

### 3.4 Heartbeat-based backpressure (Faz C)

Şu an `XLEN` üzerinden basit backpressure. Worker capacity-aware için:
- Worker → Redis HSET TTL heartbeat (`nqai.worker.heartbeat.{worker_id}` → `{capacity, in_flight, last_pickup_ms}`)
- Gateway admission: sum(capacity) - sum(in_flight) < threshold → 503
- XLEN fallback olarak korunur (heartbeat yoksa)

### 3.5 Production hardening (Faz C — orchestration)

- pgBouncer transaction mode (200-user için max_connections + pool size kalibrasyonu)
- Gateway SIGTERM graceful drain (uvicorn lifespan + asyncio.gather inflight)
- Zero-downtime migration (pgroll integration)
- TLS uçtan uca (Cloudflare + cert pinning)

### 3.6 Observability (Faz C — kanonik)

[observability.md](../architecture/observability.md) spec'i hâlâ "Faz C target" durumunda. Gerçek implementasyon:
- Prometheus exporter (`/metrics` gateway + worker)
- OTel tracing (gateway → worker span propagation)
- RED metrics: rate (`nqai_tts_requests_total`), errors (`nqai_tts_errors_total{type}`), duration (`nqai_tts_first_audio_seconds`, `nqai_tts_inference_seconds`, `nqai_tts_total_seconds`)
- Grafana dashboard: per-tenant + per-voice + per-app_label

---

## 4. Faz B'de yapılıp B.1/B.1.5 DoD'larında **olmayan** ekstralar

| Madde | Neden eklendi | Faz C'de tekrar dokunulacak mı |
|---|---|---|
| RFC 8594 Deprecation + Sunset + Link header (sync `/v1/tts`) | API maturity sinyali | Hayır — 2026-09-01'da `410 Gone` eklenir, o ayrı bir 5-satır PR |
| Result stream cancellation cleanup (client disconnect → DEL stream) | Memory leak prevention | Hayır — stable |
| Idempotency race-safe `reserve_or_get` (IntegrityError catch) | Concurrent submit safety | Hayır — stable |
| `X-NQAI-App` product attribution → `usage_records.app_label` | Multi-product billing/analytics | Faz C billing dashboard'da görünecek |
| R2 cache size-based LRU eviction | Disk dolma prevention | Hayır — `NQAI_R2_CACHE_MAX_BYTES` env-tunable |
| LoRA cache `NQAI_LORA_CACHE_SIZE` LRU | GPU VRAM bounded | Per-voice sticky routing geldiğinde co-evolve |

---

## 5. Test invariants kapsama haritası

| Invariant | Test dosyası | Test sayısı |
|---|---|---|
| Worker consumer XACK matrix | `tests/test_worker_consumer.py` | 9 |
| Pipeline (publish order + first_audio_ms + archive AFTER) | `tests/test_worker_pipeline.py` | 11 |
| Result stream consumer (seq dedupe + cancel cleanup + retry) | `tests/test_result_stream.py` | 11 |
| Async E2E (gateway + worker + first chunk timing) | `tests/test_async_e2e.py` | 8 |
| Async jobs API (idempotency + replay + 409 body_hash conflict + backpressure) | `tests/test_async_jobs.py` | 24 |
| Worker live primitives (iter_engine_chunks + InMemoryLiveMediaSink + bridge errors) | `tests/test_worker_live.py` | 5+ |
| Latency waterfall dataclass | `tests/test_latency_waterfall.py` | 3 |
| **Faz B toplam** | — | **~70+ test** |

Suite genelde **284 test**, bunların ~%25'i Faz B kazanımı.

---

## 6. Faz B kapanış notu — tek paragraf

Faz B "tek-process VoxCPM2 prototipi"nden "stateless gateway + Redis Streams queue + GPU worker pool + durable artifact" omurgasına geçişi tamamladı. Async TTS jobs uçtan uca, sync `/v1/tts` queue üzerinden geriye uyumlu proxy (sunset 2026-09-01), at-least-once delivery XAUTOCLAIM + bounded retry + DLQ ile dağıtık correctness'ı kanıtlandı; B.1.5'te frame-by-frame streaming bridge (`iter_engine_chunks`) drain pattern'i killed, transport endüstri-standardı tek-yönlü TTS API olarak kilitlendi (ElevenLabs / OpenAI Audio / Cartesia / MiniMax mental model). WebRTC scaffold yön sapmasıydı, reverted edildi; transport-agnostic primitives (AudioFrame, LatencyWaterfall, split_pcm16_frames, InMemoryLiveMediaSink) duplex voice-agent ürünü açılırsa orada yeniden kullanılabilir korundu. **Bilinçli boşluklar:** latency waterfall persistence yarım (5 kolon eksik), per-voice sticky routing yok, gerçek-model first-audio benchmark stub engine ile yapıldı. Bunlar Faz C scope'unun ilk işleri.

---

## 7. Faz C kick-off — somut plan

### 7.1 Hedef tek cümle

> **Faz C = ürün metriği görünürlük + production hardening + capacity-aware ops.** Distributed correctness (B.1) ve frame-by-frame streaming (B.1.5) artık var; Faz C bunu **ölçülebilir, dayanıklı, ve real-traffic-ready** hâle getirir.

### 7.2 DoD adayları (Faz C başlangıcında karara döner)

- [ ] `usage_records` 5 waterfall kolonu (first_audio_ms, worker_pickup_ms, reference_resolve_ms, first_pcm_ms, gateway_first_byte_ms) persisted; tek-query waterfall okunur
- [ ] Gerçek VoxCPM2 ile L4 üzerinde p50/p95 first_audio_ms ölçümü; sayılar B.1.5 closure doc'una düşer
- [ ] Prometheus `/metrics` endpoint (gateway + worker); RED metrics + waterfall histogramları; cardinality bounded (tenant_id, voice_id, status — request_id label DEĞİL)
- [ ] OTel tracing gateway→worker span propagation (request_id correlation)
- [ ] Grafana dashboard taslağı (per-tenant + per-voice + per-app_label panels)
- [ ] Heartbeat-based backpressure: worker `nqai.worker.heartbeat.{wid}` HSET TTL → gateway capacity-aware admission; XLEN fallback
- [ ] Per-voice sticky routing + cold-load metric (`nqai_worker_cold_load_seconds{voice_id}`)
- [ ] pgBouncer transaction mode + connection pool tuning (200-user load test ile kalibre)
- [ ] Gateway SIGTERM graceful drain (uvicorn lifespan + inflight gather)
- [ ] Zero-downtime migration tooling (pgroll) — şu an forward-only Alembic; live schema change'lerin write/read split'i

### 7.3 Faz C **OUT OF SCOPE** (Faz D+ veya ayrı product)

- Multi-region (Yugabyte / NATS JetStream) — Faz D/E
- Edge / on-device inference — Faz 3+
- Türkçe SFT + per-character LoRA fine-tune — Faz 2-3 ürün konusu (mimari değil)
- Voice fingerprint + AudioSeal watermark + KVKK rider — Faz 3 governance
- Duplex voice-agent (NIVA call-center) — ayrı product surface, ayrı transport (WebRTC veya gRPC bidi)
- Audio format genişlemesi (mp3, opus, ogg) — Faz B.2 (codec katmanı), Faz C scope'unda değil

### 7.4 İlk hamle önerisi

**Sıra (önem + bağımlılık):**
1. **Usage waterfall 5 kolon migration + persistence** (yarım kalan B.1.5 borcu; Prometheus exporter bunu pull edecek)
2. **Gerçek VoxCPM2 ölçüm + B.1.5 closure doc'a sayı yazımı** (L4'te smoke_test.py ile)
3. **Prometheus exporter + temel Grafana dashboard** (waterfall görünür hale gelir)
4. **Heartbeat-based backpressure** (XLEN'i bırakmak için heartbeat protokolü gerek)
5. **Per-voice sticky routing + cold-load metric** (çoklu voice production traffic'i için)
6. **pgBouncer + SIGTERM drain + pgroll** (production deployment readiness)

İlk 3 madde kümülatif ~1 hafta; sonraki 3 madde her biri 2-4 gün. **Faz C horizon = 4 hafta first-class observability + 200-user load test.**
