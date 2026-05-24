# Worker Process — `src/worker/` (Faz B.1 spec)

**Doc owner:** Tech Lead · **Karar tarihi:** 2026-05-24 · **Implementation:** Faz B.1 PR
**Karar log satırı:** "Worker süreç ayrımı (Faz B.0 Ö1)" — [decisions/README.md](../decisions/README.md)

Bu doc Faz B.1'de yazılacak GPU worker süreci için kanonik spec'tir. Implementation PR'ı bu doc'a karşı doğrulanır.

> İlgili: [scale-roadmap.md §3 + §11](scale-roadmap.md) (mimari diyagram + Faz B adımları), [streaming-protocol.md](streaming-protocol.md) (WS + chunked output formatı).

---

## 1. Sorumluluk sınırı

| Bileşen | Yapar | Yapmaz |
|---|---|---|
| **Gateway** (`src/server/`) | Auth, validate, XADD job, XREAD result stream, WS/HTTP'ye chunk push | Model load etmez, GPU bilmez, ses üretmez |
| **Worker** (`src/worker/`) | XREADGROUP job, frontend normalize, engine generate, XADD result stream, idempotency complete, usage record | HTTP endpoint expose etmez, client'a doğrudan dokunmaz, auth check yapmaz |
| **Shared** (`src/frontend/`, `src/registry/`, `src/repos/`, `src/db/`, `src/storage/`) | Hem gateway hem worker import eder | Birinden diğerine çağrı yapılmaz |

İki süreç **DB + Redis + R2 üzerinden konuşur**. Doğrudan IPC yok.

---

## 2. Süreç çalıştırma

```bash
# Yerel dev — CPU-only mock model (Faz B.2'de eklenir)
NQAI_WORKER_MODE=stub python -m worker.main

# Production — gerçek GPU
NQAI_DATABASE_URL=postgresql+asyncpg://... \
NQAI_REDIS_URL=redis://... \
NQAI_R2_BUCKET=... \
NQAI_DEVICE=cuda \
python -m worker.main
```

### Lifecycle

```
boot → DB pool ready → Redis ready → R2 client ready
     → model eager-load (NQAI_WARMUP_ON_BOOT=true default)
     → register consumer (XGROUP CREATE MKSTREAM if needed)
     → XREADGROUP loop
     → SIGTERM → drain current job → XACK → exit
```

Cold-load süresi: VoxCPM2 ~30-60 s (RunPod). `NQAI_WARMUP_ON_BOOT` ile başlangıçta yüklenir → ilk iş gecikmesi yok. K8s readiness probe model yüklenene kadar `503` döner.

---

## 3. Klasör yapısı

```
src/worker/
├── __init__.py
├── main.py              # entry point, asyncio event loop, signal handling
├── consumer.py          # Redis Streams XREADGROUP + XAUTOCLAIM loop
├── engine.py            # ← TAŞINAN src/server/engine.py (VoxCPM2Engine + LRU cache)
├── streaming.py         # ← TAŞINAN src/server/streaming.py (sentence-chunk yield)
├── pipeline.py          # tek job'ı işleyen async fn — fetch ref → normalize → generate → XADD chunks
├── results.py           # TtsResult XADD + EXPIRE + final marker
└── shutdown.py          # graceful drain (XACK in-flight, sonra exit)
```

`src/server/engine.py` ve `src/server/streaming.py` **fiziksel olarak taşınır** — kopya değil, mv. Gateway'in `src/server/main.py`'sinde bunları import eden tek yer kalan sync `/v1/tts` endpoint'i; o da aynı PR'da internal proxy haline gelir (decision log "Sync TTS deprecation politikası").

---

## 4. Job tüketim akışı

```
worker boot
  └─ XGROUP CREATE nqai.tts.jobs tts-workers $ MKSTREAM
  └─ asyncio loop:
       while not shutdown:
         msgs = await XREADGROUP(
             group="tts-workers",
             consumer="worker-<hostname>",
             streams={"nqai.tts.jobs": ">"},
             count=1,
             block=5000,  # 5s
         )
         if not msgs:
             # Try claiming any stale messages from dead workers
             await XAUTOCLAIM("nqai.tts.jobs", group="tts-workers",
                              consumer="worker-<hostname>",
                              min-idle-time=30000)
             continue
         for stream_id, fields in msgs:
             job = TtsJobPayload.decode(fields)
             try:
                 await process_job(job)
                 await XACK("nqai.tts.jobs", "tts-workers", stream_id)
             except IdempotencyConflict:
                 # Body hash mismatch — log + ACK to prevent retry loop
                 await XACK(...)
             except WorkerError as e:
                 # Crash safety: don't ACK, XAUTOCLAIM will retry
                 await idem_repo.fail(job.request_id)
                 raise
```

**Critical invariant (D-06):** `XACK` sadece TÜM result chunk'ları `nqai.tts.results.{rid}` stream'ine başarıyla XADD edildikten **sonra** çağrılır. Aksi takdirde worker crash'inde XAUTOCLAIM başka worker'a iş verir → duplicate inference + duplicate audio.

---

## 5. Job pipeline (worker.pipeline.process_job)

```python
async def process_job(job: TtsJobPayload) -> None:
    rid = uuid.UUID(job.request_id)
    tenant_id = uuid.UUID(job.tenant_id)
    api_key_id = uuid.UUID(job.api_key_id)

    started = time.monotonic()
    result_stream = result_stream_name(rid)  # "nqai.tts.results.{rid}"

    # 1. Reference audio resolve (cache hit ise no-op)
    async with AsyncSessionLocal() as s:
        voice = await VoiceRepo(s, tenant_id).get_by_voice_id(job.voice_id)
        if voice is None:
            await _publish_error(result_stream, rid, "voice_not_found")
            return
    ref_path = await resolve_reference_uri(voice.reference_uri)

    # 2. Engine generate (chunk by chunk)
    seq = 0
    try:
        async for sentence_pcm, sentence_text in engine.synthesize_stream(
            text=job.text,
            reference_wav_path=ref_path,
            voice_id=job.voice_id,
            params=job.params or {},
        ):
            chunk = TtsResult(
                request_id=str(rid),
                seq=seq,
                pcm_bytes=sentence_pcm,
                sentence_text=sentence_text,
                final=False,
            )
            await results_xadd(result_stream, chunk)
            seq += 1
    except Exception as e:
        await _publish_error(result_stream, rid, str(e))
        async with AsyncSessionLocal() as s:
            await IdempotencyRepo(s, tenant_id).fail(rid)
            await s.commit()
        raise

    # 3. Final chunk + R2 archive (opsiyonel, scale-roadmap §10 A.8 hazır)
    final_uri = await _archive_to_r2(rid, sentence_buffer)
    final_chunk = TtsResult(
        request_id=str(rid), seq=seq, pcm_bytes=b"",
        sentence_text=None, final=True,
    )
    await results_xadd(result_stream, final_chunk)

    # 4. DB writes (idempotency complete + usage record) — TEK transaction
    elapsed_ms = int((time.monotonic() - started) * 1000)
    async with AsyncSessionLocal() as s:
        await IdempotencyRepo(s, tenant_id).complete(rid, response_uri=final_uri)
        await UsageRepo(s, tenant_id).record(
            api_key_id=api_key_id,
            voice_id=job.voice_id,
            request_id=rid,
            text_char_count=len(job.text),
            sentence_count=seq,
            duration_ms=audio_duration_ms,
            elapsed_ms=elapsed_ms,
            rtf=elapsed_ms / max(audio_duration_ms, 1),
            status="ok",
        )
        await s.commit()
```

**Hata politikası:**
- Voice missing → result stream'e `error` chunk + idempotency `fail` + XACK (retry'a gerek yok)
- Engine crash → fail + XACK YOK (XAUTOCLAIM retry'a alacak)
- DB crash final yazımında → retry queue'da kalır; çift complete riski yok çünkü `IdempotencyRepo.complete()` upsert semantiği yok (next reserve `IdempotencyConflict` döner — Faz B.0 Ö4)

---

## 6. Result stream protokolü

```
Stream: nqai.tts.results.{request_id}
TTL: 600 s (worker tarafından XADD sonrası EXPIRE)
Cleanup: gateway final chunk okuduktan sonra DEL

Her XADD entry'si:
  seq            int    0-indexed
  pcm_b64        str    base64-encoded int16 PCM (Redis stream'lerinde binary OK ama
                        cross-platform decode için base64 — Redis 7 raw bytes destekler,
                        Faz B.4'te ölçülür gerekirse swap)
  sentence_text  str    UTF-8, opsiyonel (final chunk'ta None)
  final          str    "true" veya "false" (Redis stream string-only)
  error          str    opsiyonel — varsa chunk error chunk'ıdır (pcm boş)
```

Gateway tarafında:

```python
async def stream_results_to_client(rid: uuid.UUID, send_fn):
    """send_fn — WebSocket.send_bytes veya HTTP chunked write."""
    stream = result_stream_name(rid)
    last_id = "0"  # start from beginning
    while True:
        msgs = await redis.xread(
            streams={stream: last_id},
            count=10,
            block=5000,
        )
        if not msgs:
            # No chunks in 5s — check if worker still alive via job_idempotency row
            row = await IdempotencyRepo(s, tenant_id).get(rid)
            if row and row.status == "failed":
                await send_error(send_fn, "worker failed")
                break
            continue
        for _stream, entries in msgs:
            for entry_id, fields in entries:
                last_id = entry_id
                chunk = TtsResult.decode(fields)
                if chunk.error:
                    await send_error(send_fn, chunk.error)
                    await redis.delete(stream)
                    return
                await send_fn(chunk.pcm_bytes)
                if chunk.final:
                    await redis.delete(stream)
                    return
```

---

## 7. Tenant isolation (D-08 uyumu)

Worker DB erişimi yapar ama tenant_id job payload'ından gelir. Repository'ler constructor'da tenant_id alır:

```python
# DOĞRU
await VoiceRepo(s, tenant_id=uuid.UUID(job.tenant_id)).get_by_voice_id(job.voice_id)

# YANLIŞ — pan-tenant query, D-08 ihlali
await s.execute(select(Voice).where(Voice.voice_id == job.voice_id))
```

Repo katmanı zaten `tenant_id: uuid.UUID` constructor-zorunlu; raw SQLAlchemy session ile cross-tenant query yazmak code review red.

---

## 8. Observability (Faz C entegrasyonu için hook'lar)

Worker tarafında Faz B.1'de structured log + temel metric eklenir; Faz C'de Prometheus exporter ile birleşir:

```python
logger.info("worker job start",
            extra={"request_id": str(rid), "tenant_id": str(tenant_id),
                   "voice_id": job.voice_id, "text_chars": len(job.text)})

# Faz C'de:
INFERENCE_DURATION.labels(model="voxcpm2", voice=job.voice_id).observe(elapsed_ms / 1000)
SENTENCES_GENERATED.labels(voice=job.voice_id).inc(seq)
```

Metric label cardinality D-15 sınırına uyar — voice_id bounded (tenant başına ~5), tenant_id bounded (4 başlangıçta).

---

## 9. Test stratejisi

| Test | Tip | Stub |
|---|---|---|
| `tests/test_worker_consumer.py` | unit | fakeredis stream + stub engine |
| `tests/test_worker_pipeline.py` | unit | fake VoxCPM (zeros), fake R2 (moto) |
| `tests/test_worker_idempotency.py` | unit | engine raises → fail() çağrılır, XACK yok |
| `tests/test_worker_xautoclaim.py` | integration | iki worker, ilki SIGKILL, ikinci stale message'ı devralır |
| `tests/test_end_to_end_async.py` | e2e | gateway POST /v1/tts/jobs → worker process → poll GET → complete |

---

## 10. Konfigürasyon (env)

| Env | Default | Açıklama |
|---|---|---|
| `NQAI_WORKER_CONSUMER_NAME` | `worker-{hostname}` | XREADGROUP consumer kimliği |
| `NQAI_WORKER_BLOCK_MS` | `5000` | XREADGROUP block timeout |
| `NQAI_WORKER_XAUTOCLAIM_INTERVAL_S` | `30` | dead-letter taraması sıklığı |
| `NQAI_WORKER_MAX_RETRIES` | `3` | XAUTOCLAIM count > N → DLQ |
| `NQAI_WORKER_DLQ_STREAM` | `nqai.tts.jobs.dlq` | poisoned message hedefi |
| `NQAI_WARMUP_ON_BOOT` | `true` | model eager load |
| `NQAI_DEVICE` | `cuda` | gateway'in CPU, worker'ın GPU olduğu kanonik |
| `NQAI_R2_*` | — | sonuç audio R2 upload için |
| `NQAI_RESULT_STREAM_TTL_S` | `600` | worker tarafı EXPIRE |

---

## 11. Açık uçlar (Faz B.1 PR'ında karar verilecek)

1. **DLQ semantiği:** `XAUTOCLAIM count > NQAI_WORKER_MAX_RETRIES` durumunda iş `nqai.tts.jobs.dlq`'ya XADD edilir + idempotency `fail` yazılır. Bu PR mı, sonraki PR mı?
2. **R2 upload zorunluluğu:** Sync proxy modunda gateway zaten chunk'ları concat ediyor, R2 archive opsiyonel. Async path için R2 zorunlu (presigned URL döner). Karar: **async'te R2 zorunlu, sync proxy'de skip** — `audio_format=pcm16` durumunda da R2'ye yazılır (debugability).
3. **Sentence buffer in worker:** Worker bütün cümleleri RAM'de mi buffer'lar (R2 single upload için) yoksa streaming upload mı (multipart) yapar? Karar: **single upload** (5-30 sn audio = 250 KB - 1.5 MB; multipart için çok küçük).
4. **Engine cache key:** Şu an `(model_id, adapter_key)` — adapter_key voice'tan derivere. Worker çoklu voice serve ederken cache hit oranı düşük olabilir. Faz C'de PEFT `set_adapter()` ile sub-second swap'a geçiş ölçüm sonrası.

---

## 12. Definition of Done (Faz B.1)

Bu doc'taki spec aşağıdaki gözlemlenebilir koşullara yerleştirilirse Faz B.1 kapatılır:

- [ ] `src/worker/main.py` ile worker tek başına `python -m worker.main` çalışır
- [ ] Gateway `POST /v1/tts/jobs` → worker tüketir → result stream'e chunk'lar düşer → `GET /v1/tts/jobs/{rid}` `complete` döner + presigned URL geri verir
- [ ] 5 concurrent async job kuyrukta sıralanır, worker tek tek tüketir (chaos test)
- [ ] Worker SIGKILL → 30 sn içinde XAUTOCLAIM başka worker'a devreder, iş tamamlanır (chaos test)
- [ ] Sync `POST /v1/tts` internal proxy olarak çalışır, eski response contract'ı korur, `Deprecation: true` header döner
- [ ] `docker compose -f docker-compose.dev.yaml --profile gpu up` ile worker servisi de ayağa kalkar
- [ ] Suite 182 → 200+ (worker test'leri eklenir), ruff temiz
