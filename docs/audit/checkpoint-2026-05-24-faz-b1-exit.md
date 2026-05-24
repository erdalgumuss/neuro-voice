# Checkpoint — Faz B.1 çıkışı: gateway/worker süreç ayrımı tamam

**Tarih:** 2026-05-24 · **Range:** `99a010d`..`5cd83d0` (6 commit) · **Suite:** 271/271 · **Lint:** clean

> **⚠️ Pre-hardening snapshot.** Bu doc Faz B.1'in **ilk** çıkışını (commit `5cd83d0`) belgeler. Sonradan eklenen **B.1 hardening** turu (commit `6d30a4a`) — DLQ + retry budget + sync/stream/jobs ortak backpressure + result-stream seq dedupe + R2 cache LRU + usage latency kolonları — burada "yapılmadı" işaretli olarak görünür ama bugün **tamam**. Aşağıdaki §6 ("yapmadıklarımız") tablosunda B.1.5 olarak listelenen DLQ/retry/backpressure satırları artık kapatılmış durumda. Güncel resim için: [`checkpoint-2026-05-24-faz-b1.5-exit.md`](checkpoint-2026-05-24-faz-b1.5-exit.md).
>
> Ayrıca §7'deki "B.1.5 hedef tanımı" WebSocket'i ima eder; bu yön Faz B.1.5'te HTTP chunked + frame-by-frame bridge'e doğru kaydı (WebRTC scaffold reverted, WS opsiyonel hâle düştü) — bkz. 2026-05-24 decision log üst satırı.

Bu doc Faz B.1'in fiilen tamamlandığını belgeler ve **Faz B.1.5 (latency)** + **B.2 (live transports)** scope ayrımını netleştirir.

> Önceki checkpoint: [`checkpoint-2026-05-24-faz-a-exit.md`](checkpoint-2026-05-24-faz-a-exit.md) (Faz A bitiş + B planı).
> Faz B.1 spec: [`../architecture/worker-process.md`](../architecture/worker-process.md).
> Sonraki checkpoint: [`checkpoint-2026-05-24-faz-b1.5-exit.md`](checkpoint-2026-05-24-faz-b1.5-exit.md) (B.1 hardening kapanışı + frame-by-frame bridge + WebRTC reversal).

---

## 1. Bir cümle ile

Tek-process inference'tan **gateway (CPU/I/O) + worker (GPU/inference)** süreç ayrımına geçildi; async TTS jobs uçtan uca çalışıyor, sync `/v1/tts` aynı queue üzerinden backward-compat proxy. 80-100ms first-audio hedefi B.1.5'in işi — burada hedef "distributed correctness".

---

## 2. Faz B.1 boyunca yapılanlar (6 commit)

| Adım | Commit | Konu |
|---|---|---|
| 1 — audio extract | `f04a935` | `src/audio/wav.py` — PCM/WAV helper'lar engine'den ayrıştı (gateway+worker shared, GPU-bağımsız) |
| 2 — worker skeleton | `22886fe` | `src/worker/` paket iskeleti; `python -m worker.main` çalışır placeholder |
| 3 — mv engine/streaming | `21ce711` | `git mv src/server/engine.py → src/worker/engine.py` + streaming; gateway one-way `from worker import ...` (single source of truth) |
| 4a — pipeline + audit fixes | `1660bce` + `99a010d` | `worker/pipeline.py` — `process_one_job` + result-stream publishers + 6 audit fix (TransientFailure silent, commit-before-final, archive required, etc.) + consumer 4b |
| 1' — real consumer wire | `2b95c64*` | `worker/main.py` artık gerçek consumer boot eder; `worker/runtime.py` factories; SIGTERM handler; periyodik XAUTOCLAIM |
| 2 — minimal E2E | `b5dfef3` | POST → worker → GET complete (gateway = real FastAPI, worker = real consumer, fakeredis + aiosqlite + local archive) |
| 3 — sync proxy | `c380ba0` | Gateway `worker.engine` import'u tamamen koptu; `/v1/tts` ve `/v1/tts/stream` queue proxy; `/health` engine-bağımsız; `_load_voice_or_404` → R2 trigger eden reference-resolve adımı düştü; RFC 8594 `Deprecation`+`Sunset`+`Link` headers |
| 4 — XAUTOCLAIM chaos + 409 E2E | `5cd83d0` | Flaky engine + retry path + body_hash conflict E2E |

\* `2b95c64` ≈ commit hash; gerçek log için `git log --oneline 99a010d..HEAD`.

**Toplam:** 271 test (Faz B.1 öncesi 232'den +39), ruff clean, hiçbir API contract kırılmadı.

---

## 3. Mimari sınır artık net

```
┌─────────────────────────┐         ┌─────────────────────────────────┐
│  Gateway (CPU node)     │         │  Worker (GPU node)              │
│  src/server/            │         │  src/worker/                    │
│  ───────────────        │         │  ───────────────                │
│  • FastAPI app          │         │  • XREADGROUP loop              │
│  • Auth pipeline        │         │  • VoxCPM2 engine               │
│  • Voice CRUD           │  Redis  │  • Sentence streaming           │
│  • POST /v1/tts/jobs    │ Streams │  • R2 archive                   │
│  • POST /v1/tts (proxy) │ ──────→ │  • Idempotency complete +       │
│  • GET  /v1/tts/jobs/{} │  jobs   │    Usage record (one TX)        │
│  • Result stream reader │ ←────── │  • Publish chunks + final       │
│                         │ results │  • Periodic XAUTOCLAIM          │
│  Import direction:      │         │                                 │
│  server.* → audio, db,  │         │  Import direction:              │
│  repos, storage,        │         │  worker.* → audio, db, repos,   │
│  server.queue (shared)  │         │  storage, frontend, registry,   │
│                         │         │  server.queue (shared leaf)     │
│  NEVER imports          │         │  NEVER imports server.main,     │
│  worker.engine /        │         │  server.result_stream, ...      │
│  worker.streaming       │         │                                 │
└─────────────────────────┘         └─────────────────────────────────┘
```

**Doğrulama:** `grep -n 'from worker\.' src/server/*.py` → boş çıktı. Gateway tamamen GPU'dan kopuk.

---

## 4. D-06 at-least-once delivery — gerçek doğrulama

Önceden iddia, şimdi test'le kanıtlı:

| Path | Davranış | Test |
|---|---|---|
| Worker success | XACK | `test_consumer_success_xacks_and_drains_pel` |
| Voice/ref missing (PoisonJob) | XACK (drain) + error chunk + idem.fail | `test_consumer_poison_job_xacks_to_drain` |
| Engine crash (TransientFailure) | **NO XACK** + silent (no error chunk, no idem.fail) | `test_consumer_transient_failure_does_not_xack` |
| Archive crash (TransientFailure) | **NO XACK** + silent | `test_pipeline_archive_failure_is_transient_and_silent` |
| DB commit crash (TransientFailure) | **NO XACK** + silent | `test_pipeline_publishes_final_only_after_db_commit` |
| Unknown exception | **NO XACK** + log | `test_consumer_unknown_exception_does_not_xack` |
| **XAUTOCLAIM retry path** | Stale PEL → reclaim → retry → complete | `test_xautoclaim_recovers_job_after_transient_failure` |
| Body_hash conflict | 409 (Stripe semantics) | `test_job_body_hash_conflict_returns_409_e2e` |
| Replay after complete | 202 + deduplicated=true + status=complete | `test_gateway_idempotency_complete_status_after_worker` |

---

## 5. DoD checklist — Faz B.1

- [x] Gateway `worker.engine` / `worker.streaming` / `worker.pipeline` import etmez (sadece `worker.pipeline.VoiceView` muafiyeti bile yok — koparıldı)
- [x] `worker.main` gerçek consumer boot eder, R2 archive bağlı, SIGTERM handler hazır, periyodik XAUTOCLAIM çalışır
- [x] `worker.pipeline.process_one_job` — commit-before-final, archive zorunlu, transient silent, poison loud
- [x] Consumer group `id="0"` ile açılır (önceden enqueue edilmiş mesajları kaçırmaz)
- [x] R2 upload `asyncio.to_thread` ile event loop'u bloke etmez; PCM → WAV (audio/wav) yazılır
- [x] `IdempotencyRepo.reserve_or_get` race-safe (IntegrityError catch + reclassify)
- [x] Gateway sync `/v1/tts` queue üzerinden proxy, API contract aynı, `Deprecation`+`Sunset` header
- [x] `/v1/tts/stream` chunked WAV proxy
- [x] Async job status `audio_url` presigned (s3:// leak yok)
- [x] Result stream `EXPIRE 600` safety net + gateway DEL on finish
- [x] End-to-end test: POST → worker → GET complete
- [x] Chaos test: engine crash → XAUTOCLAIM retry → complete
- [x] Idempotency replay test: same key/body → deduplicated; same key/different body → 409
- [x] Suite ≥ 270, ruff clean

**B.1 tamamlandı.**

---

## 6. Faz B.1'de **yapmadıklarımız** — neden ve nereye

Codex audit'inin "B.1'e sokmayalım, scope şişer" dediği iş listesinin kapsamı:

| İş | Neden B.1 değil | Hedef |
|---|---|---|
| True frame-level streaming (drain-then-emit → thread→asyncio queue bridge) | Engine sentence-level `synthesize_stream`; gerçek streaming için `model.generate_streaming()` doğrudan yield + bridge pattern. ~1 günlük iş, ayrı PR | **B.1.5** |
| WebSocket `/v1/tts/ws` endpoint | Live low-latency için durability'den ayrı transport. Result stream şu an HTTP chunked üzerinden. WS Starlette ile basit ama TTFB hedefi olmadan değer katmaz | **B.1.5** |
| WebRTC live audio | Mobile/web client'lar için en düşük TTFB. WS'den sonra sıralı tek adım | **B.2** |
| Warm worker affinity + sticky routing (LoRA + ref cache cold-load hot path'te değil) | Şu an worker boot'ta tek voice warm; çoklu voice/LoRA için per-voice worker pinning veya cache pre-warm gerek | **B.1.5** |
| `ttfb_ms` / `queue_wait_ms` / `first_pcm_ms` / `first_byte_sent_ms` metrikleri | Latency hedefi olmadan metric eklemek prematüre. Faz C observability'de Prometheus exporter ile birlikte | **Faz C** |
| Backpressure: XLEN değil pending+lag+heartbeat tabanlı | Şu an gateway sadece XLEN bakıyor (basit, fakat worker capacity'ye duyarlı değil). Worker heartbeat protokolü + pending lag = gerçek backpressure | **B.1.5** |
| DLQ — N retry sonrası `nqai.tts.jobs.dlq` | XAUTOCLAIM şu an sınırsız retry. DLQ + retry counter + alerting bir sonraki tur | **B.1.5** veya **Faz C** |
| `pgBouncer` + connection pool tuning | 20-user scale için default pool yeterli; 200-user'da gerekli | **Faz C** |
| Gateway lifespan shutdown drain (SIGTERM → in-flight requests drain) | Worker tarafı `stop_event` ile graceful; gateway tarafında `uvicorn --reload` zaten kapatır. Production K8s deploy ile birlikte sıkılaştırılır | **Faz C** |

---

## 7. B.1.5 hedef tanımı (latency-focused) — tek-cümle

> **B.1.5 = first-audio latency 80-100ms hedefini taşıyabilen omurga.** Pipeline drain-then-emit → frame-level streaming bridge; WebSocket endpoint + result-stream pub/sub gateway-side; warm-worker sticky routing; ttfb_ms / first_pcm_ms metrikleri; capacity-based backpressure (XLEN değil, worker pending/lag).

**DoD adayı (taslak — Faz B.1.5 başında netleşir):**
- Worker engine'in ilk PCM chunk'ı üretilir üretilmez (cümle bitmeden) result stream'e XADD'lenir
- Gateway WS endpoint açık; `Sec-WebSocket-Protocol: nqai.tts.v1`; per-chunk JSON frame
- Tek-tenant single-job: first_byte p50 ≤ 100ms, p95 ≤ 200ms (warm worker, L4)
- LoRA hot-cache: per-voice routing → ilk request'in cold-load'u sonraki request'lerde gizlenir
- `nqai_worker_pending_lag_seconds` Prometheus metric
- Backpressure: gateway worker heartbeat (Redis HSET TTL) görür, capacity hesaplar
- E2E test: WS POST → frame stream → p50 first_byte < 200ms (stub engine ile)

---

## 8. Şimdi durumun resmi

| Boyut | Değer |
|---|---|
| Test | **271** (+39 Faz B.1'de) |
| Ruff | clean |
| Endpoint sayısı | 11 (warmup düştü — worker auto-warm) |
| Worker süreç | gerçek (`python -m worker.main`) |
| Gateway worker import | **0** (`grep -n 'from worker\.' src/server/` → boş) |
| Idempotency race-safe | ✓ (`reserve_or_get` IntegrityError handler) |
| At-least-once kanıt | ✓ (XAUTOCLAIM E2E chaos test) |
| API contract | preserved (sync `/v1/tts` çıktı şekli aynı + Deprecation header) |
| Sync proxy fallback | 504 on no-worker (test'te doğrulandı) |
| Archive | required (no-R2 → TransientFailure, audio_url=null state yok) |

---

## 9. Sıradaki — B.1.5 başlangıç notu

**B.1.5'e geçmek için Faz B.1'den taşıdığımız kararlar:**
- Result stream protokolü (`TtsResult` schema) WS'de aynen kullanılabilir; sadece transport değişir
- Pipeline'daki TODO(faz-b1.5) işaretli `_drain_engine`'i değiştirip frame-level streaming'e geçirmek bridge için tek dosya değişikliği
- Consumer kazanımları (XACK matrix, periodic XAUTOCLAIM, idle yield) B.1.5'te dokunulmuyor

**Faz B.1.5 PR'ı açılmadan önce karar gerekenler:**
- Frame chunk boyutu: VoxCPM2 streaming output frame rate ne? (sample sayısı / frame)
- WS message envelope: JSON wrapper mı, binary frame + opcode mı?
- Per-voice sticky routing: Redis hash slot'u mu, consumer group filter mı?
- Capacity protokol: worker → gateway heartbeat formatı

Bunlar B.1.5 başlangıç turunda decision log satırı + spec doc'a düşer.

---

## 10. Tek söz

Faz B.1 "distributed correctness" hedefini taşıdı — gateway/worker ayrımı net, async omurga uçtan uca, at-least-once chaos test'iyle kanıtlandı, sync `/v1/tts` aynı queue üzerinden geriye uyumlu. **80-100ms first-audio Faz B.1'in işi değildi; B.1.5 ona dedicate olacak.**
