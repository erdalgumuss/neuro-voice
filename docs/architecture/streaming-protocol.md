# Streaming Protocol — WebRTC Live + Compatibility Streams (kanonik)

**Doc owner:** Backend lead · **Bağlı:** [scale-roadmap.md §7](scale-roadmap.md)
**Sürüm:** v1.5 · **Hat:** LiveKit/WebRTC primary, WebSocket/HTTP compatibility

Bu dokuman client'ların NQAI Voice TTS uç noktasıyla nasıl konuştuğunun normatif tanımıdır. SDK'lar bu spec'ten doğar; spec değişimi → breaking change → versiyon bump.

---

## 0. Tasarım çerçevesi

| Hedef | Çözüm |
|---|---|
| True low-latency media (browser, native, mobile) | LiveKit self-host + WebRTC audio track |
| Control plane | `POST /v1/tts/live/sessions` + LiveKit data channel protocol `nqai.tts.live.v1` |
| Basit curl / ffplay / Postman uyumluluğu | Deprecated `/v1/tts` and `/v1/tts/stream` queue proxies |
| Geriye uyumluluk | URL path versiyonlama (`/v1/tts/...`) + control protocol versiyonlama (`nqai.tts.live.v1`) |
| Backpressure | WebSocket close code 1013 (Try Again Later) + HTTP 429 + `Retry-After` |
| Crash recovery (worker) | At-least-once + idempotent request_id (D-05) |
| Network kesinti recovery (client) | `resume` mesajı + son alınan `chunk_seq` (planlı v2 — v1'de basit close) |

---

## 1. Live WebRTC protocol (primary, B.1.5)

### 1.1 Session creation

**URL:** `POST /v1/tts/live/sessions`

Gateway görevleri:

- Bearer API key auth
- tenant/workspace voice access policy
- warm worker admission via `nqai.worker.live.*` Redis heartbeat
- LiveKit room/token minting
- session metadata TTL store

Request:

```json
{
  "voice_id": "neeko-v01",
  "language": "tr",
  "client_request_created_ms": 1790000000000
}
```

Response:

```json
{
  "session_id": "<uuid>",
  "room_name": "nqai-tts-<uuid>",
  "livekit_url": "ws://localhost:7880",
  "participant_token": "<jwt>",
  "expires_at": "2026-05-24T12:00:00+00:00",
  "sample_rate": 48000,
  "audio_codec": "opus",
  "control_protocol": "nqai.tts.live.v1",
  "worker_id": "worker-a",
  "metrics": {
    "gateway_received_ms": 1790000000010,
    "session_admitted_ms": 1790000000025
  }
}
```

Admission failure is explicit: no warm live worker capacity returns `503` + `Retry-After`.
Live requests must not silently fall back into the durable job queue.

### 1.2 Media and control split

- Audio travels as a LiveKit/WebRTC audio track. Internally workers publish PCM16 mono 48 kHz frames; WebRTC/LiveKit carries Opus on the wire.
- JSON control travels on the LiveKit data channel under `nqai.tts.live.v1`.

Control messages:

| Yön | Type | Açıklama |
|---|---|---|
| C → S | `synthesize` | Aktif live session içinde yeni TTS isteği |
| C → S | `cancel` | Aktif sentezi best-effort iptal et |
| C → S | `ping` | Keepalive |
| S → C | `accepted` | Worker isteği aldı |
| S → C | `first_audio` | İlk audio frame media path'e verildi |
| S → C | `chunk_meta` | Audio frame dışı caption/seq metadata |
| S → C | `done` | Sentez bitti, metrics içerir |
| S → C | `error` | Terminal hata |
| S → C | `cancelled` | İstek iptal edildi |
| S → C | `pong` | Ping cevabı |

The older WebSocket design below is retained as compatibility/reference until
the SDK surface is fully WebRTC-native.

---

## 2. WebSocket protocol (compatibility/debug)

### 2.1 Bağlantı kurma

**URL:** `wss://api.nqai.voice/v1/tts/ws`

**Request headers:**

```
Authorization: Bearer nqai_prod_a1b2c3d4e5f6g7_<40-char-secret>
Sec-WebSocket-Protocol: nqai.tts.v1
Sec-WebSocket-Version: 13
```

**Server response:**

```
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: <accept-hash>
Sec-WebSocket-Protocol: nqai.tts.v1
X-NQAI-Request-Id: <connection-uuid>   ; connection-level trace id
```

**Hata durumları (connection setup):**

| Durum | HTTP code | Sebep |
|---|---|---|
| Auth header eksik / yanlış format | 401 | gateway hemen kapatır |
| Key geçersiz / revoked | 403 | DB lookup fail |
| Subprotocol mismatch | 426 Upgrade Required | server `nqai.tts.v1` desteklemiyorsa (gelecek versiyon için) |
| Rate limit aşıldı (per-key/min) | 429 + `Retry-After: 30` | gateway upgrade etmez |
| Queue depth threshold üstünde | 503 + `Retry-After: 5` | backpressure (D-14) |

### 2.2 Mesaj formatı (her iki yön)

JSON Text Frame (RFC 6455 Opcode 0x1). UTF-8 zorunlu.

```json
{
  "type": "<message_type>",
  "request_id": "<uuid-v7>",
  ...
}
```

**`type` enum** (extensible — bilinmeyen type ignore edilir + warning log):

| Yön | Type | Açıklama |
|---|---|---|
| C → S | `synthesize` | Yeni sentez isteği |
| C → S | `cancel` | Aktif sentezi iptal et |
| C → S | `ping` | Keepalive |
| S → C | `accepted` | İstek queue'ya alındı |
| S → C | `chunk` | Audio chunk (base64 PCM int16) |
| S → C | `sentence` | Cümle marker (caption / token alignment için) |
| S → C | `done` | Sentez tamamlandı, özet metrik |
| S → C | `error` | Hata + recovery hint |
| S → C | `pong` | Ping cevabı |

### 2.3 `synthesize` (C → S)

```json
{
  "type": "synthesize",
  "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA",
  "voice_id": "neeko-v01",
  "text": "Merhaba, bugün seninle ne oynayalım?",
  "mode": "play",
  "language": "tr",
  "audio_format": "pcm16",
  "sample_rate": 48000,
  "params": {
    "cfg_value": 2.0,
    "inference_timesteps": 10
  }
}
```

**Alanlar:**

| Alan | Tip | Zorunlu | Default | Notlar |
|---|---|---|---|---|
| `type` | string | ✓ | — | `"synthesize"` |
| `request_id` | string (UUIDv7) | ✓ | — | Client üretir; idempotency anahtarı (D-05); 24 saat içinde aynısı → cache hit |
| `voice_id` | string | ✓ | — | Tenant catalog'unda olmak zorunda; bilinmiyorsa `error.voice_not_found` |
| `text` | string | ✓ | — | UTF-8; max 4000 char (`NQAI_MAX_CHARS`); aşılırsa `error.text_too_long` |
| `mode` | string | ✗ | `"default"` | Voice manifest'inde tanımlı mod (storytelling, lesson, play, sleep, qa) |
| `language` | string | ✗ | `"tr"` | ISO 639-1 |
| `audio_format` | string | ✗ | `"pcm16"` | `pcm16` (raw little-endian) veya `opus` (Faz C+) |
| `sample_rate` | int | ✗ | `48000` | VoxCPM2 native; downsample sadece `audio_format=opus` ile |
| `params` | object | ✗ | server defaults | Per-request engine override (yalnızca whitelist alanlar) |

### 2.4 `accepted` (S → C)

```json
{
  "type": "accepted",
  "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA",
  "queue_position": 2,
  "estimated_start_ms": 800
}
```

Gateway queue'ya XADD ettiğinde hemen gönderilir. `queue_position` ve `estimated_start_ms` advisory — kesin değil.

### 2.5 `chunk` (S → C)

```json
{
  "type": "chunk",
  "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA",
  "chunk_seq": 0,
  "sentence_index": 0,
  "encoding": "pcm16",
  "sample_rate": 48000,
  "channels": 1,
  "data": "<base64-encoded PCM int16 bytes>"
}
```

**Notlar:**
- `chunk_seq` monotonik artan, 0'dan başlar
- `sentence_index` cümle bazlı; bir cümle birden fazla chunk'a yayılabilir (Nano-vLLM streaming Faz C+)
- `encoding=pcm16` → byte order little-endian, signed; client `Int16Array(bytes)` ile decode
- `encoding=opus` (Faz C+) → bytes Opus packet (RFC 6716)

**Binary frame alternatifi (Faz B sonu opt-in):** `chunk` mesajı için JSON yerine **Binary Frame** (RFC 6455 Opcode 0x2) — 16-byte header (`chunk_seq:u32 | sentence_index:u32 | flags:u32 | reserved:u32`) + raw PCM bytes. Base64 overhead'i (~33%) kalkar; client `Sec-WebSocket-Protocol: nqai.tts.v1+binary` ile opt-in eder.

### 2.6 `sentence` (S → C, opsiyonel)

```json
{
  "type": "sentence",
  "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA",
  "sentence_index": 0,
  "text": "Merhaba, bugün seninle ne oynayalım?",
  "start_ms_in_audio": 0,
  "duration_ms": 1820,
  "tokens": []
}
```

Cümle başında gönderilir (chunk'lardan önce). UI'da caption rendering / token alignment için. `tokens` v2'de doldurulur.

### 2.7 `done` (S → C)

```json
{
  "type": "done",
  "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA",
  "sentence_count": 3,
  "chunk_count": 17,
  "total_audio_ms": 4820,
  "elapsed_ms": 2350,
  "rtf": 0.487
}
```

Worker XACK öncesi son mesaj. Bu mesajdan sonra `cancel` ignore edilir.

### 2.8 `error` (S → C)

```json
{
  "type": "error",
  "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA",
  "code": "voice_not_found",
  "message": "voice 'foo-bar' is not in your catalog",
  "recoverable": false,
  "retry_after_ms": null
}
```

**Standart error code'ları:**

| Code | Recoverable | Açıklama |
|---|---|---|
| `voice_not_found` | false | Voice catalog'da yok |
| `text_too_long` | false | `NQAI_MAX_CHARS` aşıldı |
| `text_empty` | false | Normalize sonrası 0 char |
| `mode_unknown` | false | Voice manifest'inde mod tanımlı değil |
| `rate_limited` | true | Per-key veya per-tenant limit; `retry_after_ms` set |
| `queue_full` | true | Backpressure; `retry_after_ms=5000` |
| `worker_timeout` | true | Worker 60 sn içinde response vermedi |
| `inference_error` | true | Model crash, retry farklı worker'a düşer |
| `internal_error` | true | Bilinmeyen; trace_id ile bug report |

Error sonrası connection açık kalır (client başka istek atabilir). Auth fail'de bağlantı kapatılır (Close 4401).

### 2.9 `cancel` + `ping/pong`

```json
{"type": "cancel", "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA"}
{"type": "ping", "ts": 1716534000}
{"type": "pong", "ts": 1716534000}
```

`cancel`: worker o request'i mid-stream durdurur, `done` yerine `error.cancelled` gönderir. Already-acked chunk'lar geri alınmaz.

`ping/pong`: keepalive — istemci 30 sn'de bir ping, server 10 sn'de cevap. Heartbeat 90 sn alınmazsa server kapatır.

### 2.10 Close codes

| Code | Anlam |
|---|---|
| 1000 | Normal closure (client done) |
| 1001 | Server going away (shutdown) |
| 1011 | Server unrecoverable error |
| 1013 | Try again later (capacity) |
| 4401 | Auth fail (Bearer geçersiz) |
| 4403 | Forbidden (scope yetersiz) |
| 4429 | Rate limited (kalıcı; yeni connection bekleyelim) |

---

## 3. HTTP/2 chunked WAV fallback

WebSocket destekleyemeyen istemciler (curl, ffplay, basit IoT) için.

### 3.1 Request

```http
POST /v1/tts/stream HTTP/1.1
Host: api.nqai.voice
Authorization: Bearer nqai_prod_a1b2c3d4e5f6g7_<40-char-secret>
Content-Type: application/json
Accept: audio/wav
X-Request-Id: 01J5K3R7Z2QHDF8P9V6M2T1WAA

{
  "text": "Merhaba, bugün seninle ne oynayalım?",
  "voice_id": "neeko-v01",
  "mode": "play",
  "audio_format": "wav"
}
```

### 3.2 Response

```http
HTTP/1.1 200 OK
Content-Type: audio/wav
Transfer-Encoding: chunked
Cache-Control: no-store
X-NQAI-Request-Id: 01J5K3R7Z2QHDF8P9V6M2T1WAA
X-NQAI-Voice-Id: neeko-v01
X-NQAI-Sample-Rate: 48000
Trailer: X-NQAI-Sentences, X-NQAI-Duration-Seconds, X-NQAI-RTF

[chunk] RIFF header (44 bytes, data size = 0xFFFFFFFF — "infinite WAV")
[chunk] PCM cümle 1 (variable bytes)
[chunk] 200 ms silence (PCM zeros)
[chunk] PCM cümle 2
...

X-NQAI-Sentences: 3
X-NQAI-Duration-Seconds: 4.820
X-NQAI-RTF: 0.487
```

**Notlar:**
- RIFF header'ında `chunk_size = 0xFFFFFFFF - 8`, `data_size = 0xFFFFFFFF - 44` → player EOF'a kadar okur
- HTTP/2 frame-level multiplexing → daha hızlı first byte; HTTP/1.1 üzerinde de çalışır
- Trailer headers (`X-NQAI-Sentences`, `X-NQAI-Duration-Seconds`, `X-NQAI-RTF`) sonunda; bazı middleware drop edebilir, opsiyonel

### 3.3 Error response

```http
HTTP/1.1 404 Not Found
Content-Type: application/json
X-NQAI-Request-Id: 01J5K3R7Z2QHDF8P9V6M2T1WAA

{
  "error": {
    "code": "voice_not_found",
    "message": "voice 'foo-bar' is not in your catalog",
    "request_id": "01J5K3R7Z2QHDF8P9V6M2T1WAA"
  }
}
```

WebSocket error code → HTTP status mapping:
- `voice_not_found` → 404
- `text_too_long`, `text_empty`, `mode_unknown` → 400
- `rate_limited` → 429
- `queue_full` → 503 + `Retry-After`
- `worker_timeout`, `inference_error`, `internal_error` → 500/502

---

## 4. Latency budget (gerçekçi p95 hedefler)

[scale-roadmap.md §7.3](scale-roadmap.md) ile aynı, burada detaylı breakdown:

```
TIME (ms)   COMPONENT                            CUMULATIVE
─────────────────────────────────────────────────────────────
  0-100     TLS handshake + WS upgrade            100
100-130     Gateway: validate + auth + queue add  130
130-135     Redis XADD → worker pickup            135
135-145     Worker: frontend normalize text       145
145-150     Worker: inference setup (KV cache)    150
150-450     Worker: VoxCPM2 cümle 1 (L4 GPU)      450    ← TTFB ideal
                                                        (Nano-vLLM ile ~250-350)
450-460     Worker XADD result + gateway XREAD    460
460-470     Gateway WS send first chunk           470    ← TTFB user-perceived
470-1100    Worker: VoxCPM2 cümle 2 streamed      1100
                                                        (paralel, kullanıcı dinliyor)
```

SLO (Faz C exit):
- TTFB **p50 ≤ 800 ms**, **p95 ≤ 1500 ms** (Nano-vLLM + L4/A100)
- E2E (3 cümle, ~5 sn ses) **p95 ≤ 5 sn**

T4 ile (Faz A-B): TTFB p95 ~2.5 s, E2E p95 ~8 s — kabul edilebilir geliştirme baseline'ı.

---

## 5. Client SDK örnekleri

### 5.1 Python (httpx + websockets)

```python
import asyncio, json, base64
import websockets

async def synthesize_streaming(text: str, voice_id: str = "neeko-v01"):
    uri = "wss://api.nqai.voice/v1/tts/ws"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    pcm_chunks = []

    async with websockets.connect(uri, additional_headers=headers,
                                  subprotocols=["nqai.tts.v1"]) as ws:
        await ws.send(json.dumps({
            "type": "synthesize",
            "request_id": str(uuid7()),
            "voice_id": voice_id,
            "text": text,
            "mode": "default",
        }))

        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "chunk":
                pcm_chunks.append(base64.b64decode(msg["data"]))
            elif msg["type"] == "done":
                print(f"done: {msg['rtf']=:.2f} {msg['total_audio_ms']}ms audio")
                break
            elif msg["type"] == "error":
                raise RuntimeError(f"{msg['code']}: {msg['message']}")

    return b"".join(pcm_chunks)  # raw 48kHz int16 mono PCM
```

### 5.2 JavaScript (browser)

```javascript
const ws = new WebSocket('wss://api.nqai.voice/v1/tts/ws',
                        ['nqai.tts.v1']);
ws.binaryType = 'arraybuffer';

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: 'synthesize',
    request_id: crypto.randomUUID(),
    voice_id: 'neeko-v01',
    text: 'Merhaba!',
  }));
};

const audioCtx = new AudioContext({sampleRate: 48000});
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.type === 'chunk') {
    const bytes = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0));
    const int16 = new Int16Array(bytes.buffer);
    const float32 = Float32Array.from(int16, s => s / 32768);
    const buf = audioCtx.createBuffer(1, float32.length, 48000);
    buf.copyToChannel(float32, 0);
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(audioCtx.destination);
    src.start();
  }
  if (msg.type === 'done') ws.close(1000);
};
```

> **Browser auth uyarısı:** Bearer token tarayıcıda saklanırsa XSS riskli. Faz D'de **session token exchange** endpoint'i eklenir: client backend'i bizim `/v1/auth/session-token` ile kısa-ömürlü WS token alır, browser onunla bağlanır. v1'de doğrudan key OK (server-to-server tüketicilerde).

### 5.3 curl (HTTP chunked)

```bash
KEY="nqai_prod_..._..."
URL="https://api.nqai.voice"

curl -N -X POST $URL/v1/tts/stream \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Bir varmış, bir yokmuş.","voice_id":"neeko-v01"}' \
  | ffplay -nodisp -autoexit -
```

---

## 6. Backpressure ve flow control

### 6.1 Server-side

- Redis Streams queue length izlenir; `> N_workers × 4` → yeni istek 429 + `Retry-After: 5` (HTTP) veya close 1013 (WS)
- WebSocket: Starlette default `max_size=16MB`, `max_queue=32` mesaj backlog'u — server tarafında full olursa connection slow client'tı disconnect eder

### 6.2 Client-side

- WebSocket'te slow consumer durumu: server backpressure header gönderir (`{"type":"warning","code":"slow_consumer","message":"backlog growing"}`) — istemci `cancel` veya yavaşlatma yapmalı
- HTTP chunked'te TCP window full → server natural backpressure (yazma bloklanır), problem değil

---

## 7. Versiyonlama

- **URL versiyon:** `/v1/tts/...` — major breaking change yeni path (`/v2/...`)
- **Subprotocol versiyon:** `nqai.tts.v1`, `nqai.tts.v1+binary` (opt-in binary frames) — bilinmeyen subprotocol 426
- **Mesaj field eklemek = minor, geriye uyumlu** — client bilinmeyen field ignore eder
- **Mesaj field silmek veya semantic değiştirmek = major, yeni subprotocol versiyon**

Deprecation policy: v2 yayınlandığında v1 6 ay grace, sonra 410 Gone.

---

## 8. Test stratejisi

| Test | Araç | Kapsam |
|---|---|---|
| Live session admission | pytest + fakeredis | warm worker yoksa 503, varsa LiveKit token + session store |
| Worker live bridge | pytest + fake engine | first frame full generation bitmeden çıkar |
| LiveKit smoke | local LiveKit container | client room join + worker audio track publish |
| WS upgrade + auth | pytest + httpx + websockets | 401/403/426/429 path'leri |
| Mesaj round-trip | pytest + websockets | synthesize → chunks → done |
| Cancel mid-stream | pytest + asyncio | cancel sonrası worker stop'u doğrulanır |
| Binary frame opt-in | pytest + websockets | base64 vs binary mode |
| Chunked WAV | pytest + httpx + soundfile | response WAV valid + duration matches |
| Backpressure | k6 (load test) | queue full → 429 + Retry-After |
| Reconnect | manual + chaos | network drop → client reconnect + idempotent retry → cache hit |

---

## 9. Açık konular (v2'ye ertelendi)

- **Resumable streaming:** v1'de network drop = restart. v2'de `resume` mesajı + son `chunk_seq` ile mid-stream resume.
- **Voice cloning over WS:** v1'de voice enroll HTTP POST. v2'de WS üzerinden chunked upload + immediate use.
- **Streaming TTS-LLM interleave:** v2'de LLM token gelirken paralel TTS (sub-100 ms TTFB için).
- **Differential audio (silent gap detection):** server tarafı VAD ile sessizlikleri yutmak → bandwidth optimizasyon.
