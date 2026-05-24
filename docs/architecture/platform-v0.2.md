# Platform v0.2 — Mimari (VoxCPM2)

**Tarih:** 2026-05-24 · **Durum:** Tek-process MVP referansı — kısmen superseded

> ⚠️ **Bu doc historical referanstır.** Canlı kanonik mimari **[scale-roadmap.md](scale-roadmap.md)** — 4-tenant × 5-concurrent multi-tenant omurga. Faz A'da Postgres veri planı + DB-backed auth + R2 storage + async job queue eklendi (bkz. [../audit/checkpoint-2026-05-24-faz-a-exit.md](../audit/checkpoint-2026-05-24-faz-a-exit.md)).
>
> Bu doc'un **hâlâ doğru** bölümleri: §Bileşenler (frontend, registry, engine adapter), §Voice manifest şeması, §Performans bütçesi, §Güvenlik notları (auth + reference cap dışında), §Bilinen sınırlar (1-5, 7-8).
>
> Bu doc'un **superseded** bölümleri: §Çağrı yolu (DB auth + async jobs eklendi), §Faz hattı (scale-roadmap §10-13 ile değişti), §Bilinen sınırlar madde 6 (filesystem catalog A.6'da DB'ye geçti).

NQAI Voice'un birincil release'i. Tek bir **VoxCPM2 (Apache 2.0, OpenBMB, 2B param)** instance'ı üzerine voice catalog + HTTP/streaming API + auth ekler. Üstüne **Türkçe SFT yok**, **per-character LoRA yok** — bunlar Faz 2-3'te gelir. v0.2 zero-shot voice cloning kapasitesini canlı bir geliştirici platformuna dönüştürür.

> Detaylı VoxCPM2 API yüzeyi + parametre tuning + LoRA hattı: [voxcpm2-integration.md](voxcpm2-integration.md).

## Çağrı yolu

```
istemci
   │  POST /v1/tts        (text + voice_id + audio_format)
   ▼
FastAPI (src/server/main.py)
   │  Bearer auth (src/server/auth.py)
   ▼
VoiceRegistry.get(voice_id)              → Voice (manifest)
   │
   ▼
VoxCPM2Engine.synthesize(...)
   │  ① frontend.normalize_text(text)        # NFKC + abbr + numbers + symbols + code-mix
   │  ② frontend.segment_sentences(...)      # Türkçe-aware cümle bölme
   │  ③ for each segment:                    # _inference_lock serialize
   │       model.generate(text=seg,
   │                      reference_wav_path=voice.reference,
   │                      cfg_value=2.0, inference_timesteps=10,
   │                      normalize=False, denoise=False)
   │  ④ float32 → int16 PCM (48 kHz)
   ▼
HTTP response
   │  Content-Type: audio/wav  (header + concat PCM + 200 ms inter-silence)
   │  X-NQAI-Sample-Rate, X-NQAI-Voice-Id, X-NQAI-Sentences,
   │  X-NQAI-Duration-Seconds, X-NQAI-Elapsed-Seconds, X-NQAI-RTF
   ▼
istemci
```

Streaming variant (`POST /v1/tts/stream`) generator'ı boşaltır: ilk cümlenin PCM'i hazır olur olmaz `RIFF` header + ilk chunk gider, sonraki cümleler 200 ms sessizlik padding ile geri-yazılır. **TTFB ≈ ilk cümlenin generation süresi** (T4'te ~1-2 s, A100'da ~0.5-0.8 s; Nano-vLLM accelerated path Faz 4'te < 300 ms hedefi).

## Bileşenler

| Yol | Sorumluluk | Bağımsızlık |
|---|---|---|
| `src/frontend/` | Türkçe NFKC + sayı/kısaltma/sembol açma + cümle segmentasyonu | Modelden bağımsız, deterministik, 32 birim test |
| `src/registry/` | Voice manifest YAML CRUD + reference audio trim/resample (15 sn, **16 kHz mono** — VoxCPM2 ref formatı) | Filesystem-backed, thread-safe (RLock) |
| `src/server/engine.py` | `BaseSynthEngine` protocol + `VoxCPM2Engine` adapter + WAV/PCM helpers | Tek model instance, `_inference_lock` ile serialize edilmiş concurrent çağrılar |
| `src/server/streaming.py` | Streaming WAV header (RIFF "infinite size" trick) + sentence-chunked yield | Stateless |
| `src/server/auth.py` | `Authorization: Bearer <key>` → `NQAI_API_KEYS` env eşleşmesi (constant-time compare) | DB-backed key store v0.3'te gelir |
| `src/server/main.py` | FastAPI app, 12 route, CORS, lifecycle log | — |
| `scripts/bootstrap_voices.py` | `configs/seed_voices.yaml` → POST /v1/voices toplu enroll (idempotent) | — |
| `scripts/smoke_test.py` | Çalışan server'a 10-cümle × N-voice eval, per-call RTF + WAV dump | — |
| `notebooks/03-platform-server-colab.ipynb` | Colab T4/A100'da git clone → voxcpm install → server boot → cloudflared tunnel → smoke | — |

## Voice manifest şeması (v0)

```yaml
voice_id: kebab-case-key      # primary key, regex [a-z0-9][a-z0-9-]{1,62}[a-z0-9]
display_name: "İnsan okur ismi"
language: tr                  # ISO 639-1
gender: neutral               # neutral | female | male
style_tags: [warm, child-directed, ...]
reference_audio: file.wav     # data/reference-audio/ içinde dosya adı (16 kHz mono)
reference_seconds: 15.0       # trimmed length
source: elevenlabs | voice-talent | user-enroll | placeholder | bootstrap
license: internal-bridge | talent-contract:<id> | user-owned | ...
created_at: ISO-8601 UTC
created_by: api_key_prefix    # audit
```

Faz 3'te `adapter.*` (LoRA URI + checksum), `watermark.*`, `fingerprint.*`, `eval.*`, `release.*` alanları eklenir. v0.2 manifest'i schema-uyumlu — sadece doldurulmamış alanlar yok.

## Performans bütçesi (VoxCPM2)

| Donanım | Cümle başı (5-8 sn ses) | RTF (standart) | RTF (Nano-vLLM) | TTFB streaming |
|---|---|---|---|---|
| Kaggle T4 (16 GB) | tahmini ~6-10 s | ~0.8-1.2 | n/a (CUDA cap) | ~2-3 s |
| Colab L4 (24 GB) | ~3-5 s | ~0.5-0.7 | ~0.3 | ~1-1.5 s |
| RunPod A100 (40 GB) | ~2-3 s | ~0.4-0.5 | ~0.2 | ~0.5-0.8 s |
| RunPod H100 / L40S | < 1.5 s | ~0.3 | ~0.13 | < 0.4 s |

OpenBMB raporu: RTX 4090 standart 0.30 RTF, Nano-vLLM accelerated 0.13 RTF. Bizim Faz 4 hedefimiz **TTFB p50 < 300 ms** → Nano-vLLM path + H100 / L40S.

Inference lock şu an concurrent çağrıları serialize ediyor (VoxCPM2 forward pass thread-safe değil). Faz 4'te:
- **Process-level paralelizm** (uvicorn `--workers N`, her worker ~8 GB VRAM × N) → 4× concurrent, 32 GB+ kart
- **Async batched inference** (Triton / vLLM serving) → tek model, in-flight batching

## Güvenlik notları

- **Auth zorunlu** (`NQAI_REQUIRE_AUTH=true` default). API key olmayan istek 401.
- **Constant-time key compare** (`hmac.compare_digest`) — timing side-channel kapalı.
- **Reference audio upload size cap** (`NQAI_ENROLL_MAX_MB`, default 20 MB).
- **Text length cap** (`NQAI_MAX_CHARS`, default 4000).
- **CORS**: default `*` — production öncesi `NQAI_CORS_ORIGINS` dashboard host'una daraltılmalı.
- **Watermark + fingerprint YOK**. Faz 3'te eklenir (`voice-governance-layer`).
- **KVKK / FSEK — v0.2 kapsamı dışı**: lansman seti operatörün kendi sesinin türev varyasyonları; rıza içsel. Voice talent eklendiği an damıtma §5'teki 8-madde rider + KVKK aydınlatma metni şart olur.
- **VoxCPM2 yasak kullanımlar** (OpenBMB ToS): kişi taklidi, dolandırıcılık, dezenformasyon. AI label zorunluluğu jurisdiction'a göre.

## Bilinen sınırlar (v0.2)

1. **Türkçe SFT yok** — base VoxCPM2 zero-shot Türkçe kalitesi. Internal WER iddiası %1.65 ama bizim domain'imizde (child-directed warm) saha-test gerekli.
2. **Per-character LoRA yok** — referans audio ile zero-shot clone. Karakter tutarlılığı uzun-form'da bozulabilir.
3. **Single-node**. Horizontal scale için load balancer + her node kendi disk-cache'i.
4. **Tek model serialize concurrent**. p99 latency yük altında bozulur.
5. **Streaming sadece tek istemciye linear**. Multi-tenant fan-out Faz 4.
6. **Persist voice catalog filesystem'de**. Multi-node deploy'da Postgres veya S3-backed registry.
7. **Eval suite yok**. Smoke testi sentezliyor ama UTMOSv2 / NISQA / Whisper-TR-WER ölçümleri Faz 1 eval gelene kadar manuel dinleme.
8. **Style mode tag'leri frontend'de henüz yok** — VoxCPM2 `(whispering, slow)` gibi parantez prompt'larını destekliyor, Faz 1 frontend olgunlaşmasında eklenir.

## Faz hattı (özet — detay: [voxcpm2-integration.md §10](voxcpm2-integration.md))

| Faz | Hedef | Hafta |
|---|---|---|
| 0 (bitti) | Platform v0.1 (Chatterbox) → v0.2 (VoxCPM2) refactor | bugün |
| 1 | Eval suite L1 + Türkçe baseline ölçüm + bench framework | 1 |
| 2 | Türkçe SFT (RunPod A100 × 2-4 gün) | 2-4 |
| 3 | Voice talent kayıt + per-character LoRA + governance | 3-6 |
| 4 | Multi-process + Nano-vLLM + observability + DB-backed key store | 5-7 |
| 5 | Production gate, family field test, B2B pilot | 7-8 |
