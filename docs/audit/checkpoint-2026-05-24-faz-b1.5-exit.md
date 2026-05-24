# Checkpoint — Faz B.1.5 çıkışı: streaming TTS API + frame-by-frame bridge

**Tarih:** 2026-05-24 · **Range:** `5cd83d0`..`df4302f` (6 commit) · **Suite:** 284/284 · **Lint:** clean

Bu doc Faz B.1.5'in fiilen tamamlandığını belgeler ve **B.2 (codec + opsiyonel transports)** scope ayrımını netleştirir.

> Önceki checkpoint: [`checkpoint-2026-05-24-faz-b1-exit.md`](checkpoint-2026-05-24-faz-b1-exit.md) (Faz B.1 bitiş + B.1.5 hedef tanımı).
> Streaming protokol kanonik spec: [`../architecture/streaming-protocol.md`](../architecture/streaming-protocol.md).
> Yön düzeltmesi karar satırı: [`../decisions/README.md`](../decisions/README.md) (2026-05-24, "Faz B.1.5 — streaming TTS API (HTTP chunked + async jobs, NO WebRTC)").

---

## 1. Bir cümle ile

Pipeline'daki **drain-then-emit** anti-pattern'ı söküldü; engine cümle yield ettikçe gateway result stream üzerinden HTTP chunked WAV olarak client'a aktarılıyor. Ürün şekli **endüstri-standardı tek-yönlü streaming TTS API** olarak kilitlendi — ElevenLabs / OpenAI Audio / Cartesia mental model. Duplex voice-agent (WebRTC) bilinçli olarak scope dışı; ayrı transport gerektirdiğinde ayrı product surface olarak gelir.

---

## 2. Faz B.1.5 boyunca yapılanlar (6 commit)

| Adım | Commit | Konu |
|---|---|---|
| 1 — B.1 hardening | `6d30a4a` | Latency işine girmeden önce B.1 omurgasında ek dayanıklılık: retry/DLQ rafine, usage_records latency metric kolonları, R2 cache LRU, result-stream seq dedupe + cancellation cleanup, ortak backpressure helper |
| 2 — WebRTC scaffold (sonra reverted) | `bf03685` | Codex turu: LiveKit token + room + session store + worker live heartbeat + `LiveKitMediaSink` + `POST /v1/tts/live/sessions`. Karar reversed |
| 3 — Scaffold revert | `39aecbe` | LiveKit + live sessions + worker heartbeat-for-admission tamamen söküldü; transport-agnostic primitives korundu (`AudioFrame`, `LatencyWaterfall`, `split_pcm16_frames`, `iter_live_audio_frames`, `InMemoryLiveMediaSink`) |
| 4 — frame-by-frame bridge | `8edfe44` | `worker.live.iter_engine_chunks` thread→asyncio.Queue köprüsü; pipeline `list(engine.synthesize_stream(...))` drain'i öldü, her cümle üretilir üretilmez result stream'e XADD; `tests/test_worker_pipeline.py::test_pipeline_publishes_first_chunk_before_engine_finishes` invariant'i pinler |
| 5 — E2E first-chunk assertion | `c470b2d` | `tests/test_async_e2e.py::test_first_chunk_in_result_stream_before_engine_finishes` — HTTP wire seviyesi değil result-stream seviyesinde ölçer (httpx ASGITransport buffer'ını bypass eder); drain-then-emit floor (~900ms) altında, bridge ile ~50-200ms first chunk |
| 6 — Docs alignment | `df4302f` | `streaming-protocol.md` baştan yazıldı (HTTP chunked primary + async jobs + sync deprecated proxy + SDK örnekleri); README + AGENTS.md WebRTC referanslarından temizlendi; decision log üst sırasına yön düzeltme satırı |

**Toplam:** 284 test (Faz B.1 sonu 271'den +13), ruff clean, API contract korunuyor (sync `/v1/tts` davranışı + Deprecation header aynı).

---

## 3. Streaming yüzeyi artık net

```
┌──────────────────────────────────────────────────────────────┐
│  Client (curl, httpx, SDK)                                   │
└──────────────────────────────────────────────────────────────┘
                       │ POST /v1/tts/stream (chunked)
                       ▼
┌─────────────────────────┐         ┌─────────────────────────────────┐
│  Gateway (CPU node)     │         │  Worker (GPU node)              │
│  src/server/            │         │  src/worker/                    │
│                         │         │                                 │
│  • auth + voice access  │         │  • XREADGROUP                   │
│  • XADD jobs stream     │  Redis  │  • iter_engine_chunks (bridge)  │
│  • consume_result_stream│ Streams │  • publish_chunked per sentence │
│    (block_ms=100,       │ ──────→ │  • R2 archive AFTER chunks      │
│     seen_seq dedupe,    │ ←────── │  • IdempotencyRepo.complete +   │
│     cancel→DEL stream)  │ results │    UsageRepo.create (one TX)    │
│  • StreamingResponse    │         │  • XACK only at safe terminal   │
│    audio/wav chunked    │         │  • periodic XAUTOCLAIM          │
└─────────────────────────┘         └─────────────────────────────────┘
```

**Doğrulama:**
- `grep -rn 'list(engine.synthesize_stream' src/` → boş çıktı (drain pattern dead)
- `grep -rn 'livekit\|LiveKit' src/ tests/ docs/architecture/` → sadece historical decision log + streaming-protocol §5'te "abandoned scaffold" referansı
- `iter_engine_chunks` test'i sentence yield'ler arasında 50ms sleep ile koşar; ilk `publish_chunk` çağrısı **ikinci** yield gelmeden olur

---

## 4. Latency invariant — gerçek doğrulama

| Path | Davranış | Test |
|---|---|---|
| Pipeline frame-by-frame | İlk cümle yield'inden sonra XADD; engine ikinci cümleyi yield etmeden gateway ilk byte'ı görür | `test_pipeline_publishes_first_chunk_before_engine_finishes` |
| E2E first-chunk timing | Result stream `xlen > 0` while `consumer.acked == 0`; 600ms içinde (vs 900ms drain floor) | `test_first_chunk_in_result_stream_before_engine_finishes` |
| Bridge cancellation | `cancel_event` set → producer thread çıkar, queue temizlenir, exception leak yok | `tests/test_worker_live.py::test_iter_engine_chunks_*` |
| Bridge exception propagation | Engine içinde raise → consumer tarafında aynı exception | `tests/test_worker_live.py::test_iter_engine_chunks_propagates_engine_error` |
| Result-stream seq dedupe | Worker retry → duplicate `seq` → client tarafında ignore edilir | `tests/test_result_stream.py::test_consume_skips_duplicate_seq` |
| Result-stream cancellation cleanup | Client disconnect → try/finally `DEL nqai.tts.results.{rid}` | `tests/test_result_stream.py::test_consume_cleans_stream_on_cancellation` |
| Usage latency cols | `queue_wait_ms`, `inference_ms` doluyor; usage row B.1.5 waterfall analizine yeter | `tests/test_async_e2e.py::test_async_job_writes_usage_metrics` |

---

## 5. DoD checklist — Faz B.1.5

AGENTS.md "Definition of Done" listesinden:

- [x] First audio is emitted to the result stream **before full generation completes** — kanıt: `test_pipeline_publishes_first_chunk_before_engine_finishes` + `test_first_chunk_in_result_stream_before_engine_finishes`
- [x] `POST /v1/tts/stream` chunked HTTP is the primary live path — sync proxy aynı queue üzerinden çalışır, WebRTC scaffold dropped
- [x] `worker.live.iter_engine_chunks` thread→asyncio queue bridge — `list(engine.synthesize_stream(...))` öldü
- [x] Warm worker / reference / adapter cache behavior is explicit (bounded LRU, env-tunable) — `src/storage/r2.py` LRU + `worker/runtime.py` warmup-on-boot + LoRA cache
- [x] R2 artifact finalization happens **AFTER** live chunks have been published — pipeline `publish_chunked` → `_drain_to_pcm` → archive_callable → `IdempotencyRepo.complete` sırası
- [x] Measured latency waterfall reachable from `usage_records` — `queue_wait_ms` + `inference_ms` kolonları doldu (migration `0003`); first_audio_ms takip ediliyor pipeline içinde

Ek invariant'lar (yön düzeltmesi sonrası eklendi):

- [x] `grep livekit src/` boş — WebRTC referansı kalmadı
- [x] `src/live/__init__.py` sadece transport-agnostic primitives export eder
- [x] `streaming-protocol.md` ElevenLabs / OpenAI Audio / Cartesia SDK kullanan bir geliştirici için neredeyse farksız okunur

**B.1.5 tamamlandı.**

---

## 6. Faz B.1.5'te **yapmadıklarımız** — neden ve nereye

| İş | Neden B.1.5 değil | Hedef |
|---|---|---|
| Frame-level (PCM frame, ~20ms) yield — sentence-level değil | Engine adapter `synthesize_stream` cümle yield ediyor; gerçek 20ms frame için `model.generate_streaming()` doğrudan + bridge. Şu an ilk byte zaten ilk cümle yield anında ulaşıyor; 20ms granularity end-to-end perf'e fark katmıyor (network MTU + chunked encoding zaten frame boyutu kadar buffer'lar) | **B.2** (codec katmanı ile beraber) |
| Opus / mp3 encoder | `audio_format=wav` tek desteklenen; mp3/opus için on-the-fly encoder + buffer + frame boundary discipline gerek | **B.2** |
| WebSocket transport | HTTP chunked yeterli ve ElevenLabs/OpenAI/Cartesia standardı; WS opsiyonel olarak gelir (`/v1/tts/ws`) — ama low-latency win'i HTTP chunked üzerinde zaten alındı | **B.2** veya opsiyonel |
| Warm-worker sticky routing (per-voice) | Şu an worker tek voice cold-load'u boot'ta yapıyor; çoklu voice/LoRA için hash-slot routing | **B.1.6** veya **Faz C** |
| Prometheus latency exporter (p50/p95) | Latency veri usage_records'da kullanılabilir durumda; Prometheus exporter + dashboard Faz C observability'de | **Faz C** |
| Backpressure: XLEN değil pending+lag tabanlı | Şu an gateway XLEN bakıyor; worker heartbeat → capacity → smart admission ayrı tur | **Faz C** |
| Duplex voice-agent transport (WebRTC veya gRPC bidi) | NIVA call-center / voice-agent ürünleri ayrı product surface; bu API'a girmez | **Ayrı product line** (NIVA roadmap) |

---

## 7. Yön düzeltmesi notu (önemli)

B.1.5'in ilk turu Codex tarafından **WebRTC-first** olarak geliştirildi (commit `bf03685`). Kullanıcı geri bildirimi sonrası tam reversal yapıldı (commit `39aecbe`). Karar dayanağı:

- NQAI Voice ürünü = **tek-yönlü streaming TTS API** ("metni gönderirken aynı anda geri çek" — kullanıcı tanımı 2026-05-24)
- ElevenLabs, OpenAI Audio, Cartesia, MiniMax — hiçbiri TTS API'sında WebRTC sunmuyor; endüstri standardı HTTP chunked (+ opsiyonel WebSocket)
- WebRTC SFU hop'u 50-100ms latency ekler, vendor + ops kompleksitesi getirir, tek-yönlü TTS için değer üretmez
- Duplex voice-agent (NIVA call-center) ayrı bir ürün — gerektiğinde ayrı transport ile gelir, bu yüzeye girmez

**Korunan kazançlar:** `iter_engine_chunks` bridge, `LatencyWaterfall`, `AudioFrame`, `split_pcm16_frames`, `InMemoryLiveMediaSink`. Bunlar transport-agnostic; voice-agent ürünü açılırsa orada da yeniden kullanılabilir.

**Silinen kod:** `src/live/livekit.py`, `src/live/registry.py`, `src/live/sessions.py`, `src/worker/live_consumer.py` (uncommitted draft), `LiveKitMediaSink`, `POST /v1/tts/live/sessions`, `TTSLiveSessionCreate/Response`, worker `_live_heartbeat_loop`.

---

## 8. Şimdi durumun resmi

| Boyut | Değer |
|---|---|
| Test | **284** (+13 Faz B.1.5'te) |
| Ruff | clean |
| Endpoint sayısı | 11 (B.1 ile aynı; `/live/sessions` reverted edildi) |
| Worker süreç | gerçek (`python -m worker.main`) — değişmedi |
| Pipeline pattern | **frame-by-frame** (drain pattern dead) |
| First chunk floor (E2E test stub engine ile) | ≤ 600ms (300ms inter-yield engine ile); B.1 öncesi drain floor ≥ 900ms |
| Usage latency kolonları | `queue_wait_ms`, `inference_ms` (migration `0003`) |
| Transport | HTTP chunked (primary) + async jobs (long-running) + sync proxy (deprecated, sunset 2026-09-01) |
| WebRTC/LiveKit | yok (reverted); duplex product surface ayrı transport ile gelecek |

---

## 9. Sıradaki — B.2 / Faz C başlangıç notu

**B.2 (codec + opsiyonel transports) için aday iş:**
- Audio format genişlemesi: mp3, opus, ogg (on-the-fly encoder + frame boundary)
- Opsiyonel WS endpoint `/v1/tts/ws` — HTTP chunked'ın peer'i, aynı `TtsResult` schema'sı
- Per-voice cache pre-warm hot path'te değil boot'ta

**Faz C (production hardening) için aday iş:**
- Prometheus exporter (`nqai_first_audio_ms_p50/p95`, `nqai_inference_ms`, `nqai_queue_wait_ms`)
- Heartbeat-based backpressure (XLEN değil)
- pgBouncer + connection pool tuning (200-user için)
- Gateway lifespan SIGTERM drain (in-flight request graceful shutdown)
- Zero-downtime migration (pgroll)

**Voice-agent product line (NIVA, ayrı):**
- Duplex transport karar (WebRTC vs gRPC bidi)
- Wake-word + VAD + STT entegrasyonu
- Tek tenant call-center session model

---

## 10. Tek söz

Faz B.1.5 "first-audio latency" hedefini taşıdı — engine cümle yield ettikçe gateway client'a iletiyor, drain pattern öldü, transport endüstri-standardına oturdu (HTTP chunked + async jobs). **WebRTC scaffold yön sapmasıydı; düzeltildi, transport-agnostic primitives korundu. NQAI Voice TTS API artık ElevenLabs SDK'sına alışkın bir geliştirici için neredeyse farksız okunur.**
