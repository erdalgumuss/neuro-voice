# Streaming Protocol — HTTP chunked + async jobs (kanonik)

**Doc owner:** Backend lead · **Bağlı:** [scale-roadmap.md §7](scale-roadmap.md)
**Sürüm:** v1 · **Hat:** HTTP chunked primary, async polling, sync proxy (deprecated)

Bu doc client'ların NQAI Voice TTS uç noktasıyla nasıl konuştuğunun normatif tanımıdır. **Endüstri-standardı tek-yönlü streaming TTS API** — ElevenLabs / OpenAI Audio / Cartesia ile uyumlu mental model. WebRTC duplex (voice agent) bilinçli olarak scope dışıdır; o ürünler ayrı bir transport ile gelir (bkz. [decision log](../decisions/README.md)).

---

## 0. Tasarım çerçevesi

| Hedef | Çözüm |
|---|---|
| Tek-yönlü düşük gecikmeli streaming (text in → audio out) | `POST /v1/tts/stream` chunked HTTP, ilk cümle üretilir üretilmez ilk byte gider |
| Long-running iş + presigned artifact | `POST /v1/tts/jobs` (Stripe `Idempotency-Key`) + `GET /v1/tts/jobs/{id}` polling |
| Geriye uyumluluk (curl, ffplay, basit istemci) | `POST /v1/tts` sync queue proxy — `Deprecation: true` + `Sunset: 2026-09-01` |
| Backpressure | HTTP 503 + `Retry-After` (worker capacity için XLEN şu an; heartbeat-based Faz B.1.5+) |
| Crash recovery (worker) | At-least-once + Stripe-idempotent `request_id` (D-05) + DLQ |
| Versiyonlama | URL path (`/v1/...`) |

**Hedef:** ElevenLabs `/v1/text-to-speech/{voice_id}/stream`, OpenAI `/v1/audio/speech` veya Cartesia `/v1/tts/sse` SDK'ları kullanmaya alışkın bir geliştirici için **dökümanların neredeyse farksız** okunması.

---

## 1. Streaming (HTTP chunked) — primary

```
POST /v1/tts/stream
Authorization: Bearer nqai_<env>_<prefix>_<secret>
Content-Type: application/json
X-NQAI-App: <optional product slug>   ← usage attribution
X-Request-Id: <uuid optional>          ← trace correlation
Accept: audio/wav

{
  "text": "Merhaba dünya.",
  "voice_id": "neeko-v01",
  "language": "tr",
  "audio_format": "wav"
}
```

**Response (200):** `Transfer-Encoding: chunked` + `Content-Type: audio/wav`. RIFF header upfront (infinite-size trick), ardından PCM payload cümle cümle akar. İlk byte engine ilk cümleyi üretir üretmez gateway'e ulaşır (`worker.live.iter_engine_chunks` thread→asyncio bridge).

**Response headers:**
- `X-NQAI-Request-Id`: trace id
- `X-NQAI-Sample-Rate`: 48000
- `X-NQAI-Voice-Id`: voice slug
- `Deprecation` ve `Sunset`: SADECE sync `/v1/tts`'te (`/v1/tts/stream` deprecated değil)

**Hata yolları:**
- 401: bearer eksik/geçersiz
- 404: voice tenant tarafından erişilemez
- 429: rate limit
- 503: worker capacity yok, `Retry-After: 5`
- 504: worker timeout (`NQAI_SYNC_TIMEOUT_S`, default 30s)

**Wire format:** Worker → gateway aktarımı için Redis Streams (`nqai.tts.results.{request_id}`); her chunk `TtsResult` dataclass'ı: `request_id, seq, pcm_bytes, sentence_text, final, error`. Gateway client'a ham PCM yazıyor; encoder katmanı (mp3, opus) Faz B.2'de.

---

## 2. Async jobs — long-running + presigned URL

Streaming uygun değilse (mobile spotty connection, batch, webhook flow):

```
POST /v1/tts/jobs
Authorization: Bearer nqai_<env>_<prefix>_<secret>
Idempotency-Key: <uuid>                ← Stripe convention, ZORUNLU
Content-Type: application/json

{
  "text": "...",
  "voice_id": "neeko-v01",
  "audio_format": "wav"
}

→ 202 Accepted
{
  "job_id": "<idempotency-key>",
  "status": "queued",
  "created_at": "..."
}
```

Aynı `Idempotency-Key` + aynı body → cached `job_id`. Aynı key + farklı body → 409 (Stripe semantiği).

Polling:

```
GET /v1/tts/jobs/{job_id}
→ 200
{
  "job_id": "...",
  "status": "queued" | "running" | "complete" | "failed",
  "output": {
    "audio_url": "https://r2.../signed-url...",      ← presigned, asla s3:// leak
    "content_type": "audio/wav",
    "expires_at": "..."
  },
  "metrics": {
    "inference_ms": 1820,
    "rtf": 0.31,
    "first_audio_ms": 240
  }
}
```

`audio_url` her zaman presigned (R2 signed GET, default 1h TTL). Worker iş bitirip artifact'i R2'ye yazınca `complete` döner. `failed` → DLQ veya N retry sonrası terminal hata.

---

## 3. Sync — backward-compat queue proxy (DEPRECATED)

```
POST /v1/tts
→ Deprecation: true
  Sunset: Mon, 01 Sep 2026 00:00:00 GMT
  Link: </v1/tts/jobs>; rel="successor-version"
```

Gateway XADD eder + result stream'i drain edip TEK WAV body döner. Latency: streaming'in toplam toplamı. Yeni kod kullanmamalı; `/v1/tts/stream` veya `/v1/tts/jobs` tercih edilir. 2026-09-01'da 410.

---

## 4. SDK örneği — curl + Python

**curl (streaming):**
```bash
curl -X POST https://api.nqai.voice/v1/tts/stream \
  -H "Authorization: Bearer $NQAI_KEY" \
  -H "X-NQAI-App: neeko-mobile" \
  -H "Content-Type: application/json" \
  -d '{"text":"Merhaba dünya.","voice_id":"neeko-v01"}' \
  --output - | ffplay -nodisp -autoexit -
```

**Python (httpx):**
```python
import httpx

async with httpx.AsyncClient() as client:
    async with client.stream(
        "POST", "https://api.nqai.voice/v1/tts/stream",
        headers={"Authorization": f"Bearer {NQAI_KEY}"},
        json={"text": "Merhaba.", "voice_id": "neeko-v01"},
    ) as r:
        async for chunk in r.aiter_bytes():
            speaker.write(chunk)  # any audio sink
```

**Python (async jobs):**
```python
import httpx, uuid, time

async with httpx.AsyncClient(
    base_url="https://api.nqai.voice",
    headers={"Authorization": f"Bearer {NQAI_KEY}"},
) as client:
    rid = str(uuid.uuid4())
    r = await client.post(
        "/v1/tts/jobs",
        headers={"Idempotency-Key": rid},
        json={"text": "...", "voice_id": "neeko-v01"},
    )
    assert r.status_code == 202

    while True:
        s = (await client.get(f"/v1/tts/jobs/{rid}")).json()
        if s["status"] in ("complete", "failed"):
            break
        await asyncio.sleep(0.5)

    audio = await client.get(s["output"]["audio_url"])
```

---

## 5. Karar (recap)

- **Tek-yönlü streaming TTS** = bu doc. HTTP chunked + async jobs.
- **Duplex voice agent** (NIVA call-center, future) = AYRI ürün surface. WebRTC veya gRPC oraya gelir, bu doc'a değil.
- **Frame-level / Opus encoding** = Faz B.2 (codec katmanı), bu surface üzerinde format genişlemesi olarak.

WebRTC + LiveKit + live sessions yönü Faz B.1.5'te denendi ve scope-misfit olduğu için reverted edildi (commit `99e62cd` cleanup, decision log "WebRTC/LiveKit scaffold drop").
