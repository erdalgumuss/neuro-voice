# Platform v0.1 — Mimari

**Tarih:** 2026-05-24 · **Durum:** ilk release, tek-node MVP

NQAI Voice'un ilk çalışan release'i. Tek bir Chatterbox Multilingual instance'ı üzerine voice catalog + HTTP/streaming API + auth ekler. Üstüne **Türkçe SFT yok**, **per-speaker LoRA yok** — bunlar Faz-2 (bkz. [02-distilled-findings.md §7](../research/02-distilled-findings.md)). v0.1 zero-shot voice cloning kapasitesini bir geliştirici platformuna dönüştürür.

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
ChatterboxEngine.synthesize(...)
   │  ① frontend.normalize_text(text)        # NFKC + abbr + numbers + symbols
   │  ② frontend.segment_sentences(...)      # Türkçe-aware cümle bölme
   │  ③ for each segment:                    # _inference_lock serialize
   │       model.generate(seg, language_id="tr",
   │                      audio_prompt_path=voice.reference)
   │  ④ float32 → int16 PCM
   ▼
HTTP response
   │  Content-Type: audio/wav  (header + concat PCM + 200 ms inter-silence)
   │  X-NQAI-Sample-Rate, X-NQAI-Voice-Id, X-NQAI-Sentences,
   │  X-NQAI-Duration-Seconds, X-NQAI-Elapsed-Seconds, X-NQAI-RTF
   ▼
istemci
```

Streaming variant (`POST /v1/tts/stream`) generator'ı boşaltır: ilk cümlenin PCM'i hazır olur olmaz `RIFF` header + ilk chunk gider, sonraki cümleler 200 ms sessizlik padding ile geri-yazılır. Tek seferde tüm metni sentezleyip beklemek yerine **TTFB ≈ ilk cümlenin generation süresi** (T4'te ~1-2 s, A100'da ~0.3-0.5 s).

## Bileşenler

| Yol | Sorumluluk | Bağımsızlık |
|---|---|---|
| `src/frontend/` | Türkçe NFKC + sayı/kısaltma/sembol açma + cümle segmentasyonu | Modelden bağımsız, deterministik, 32 birim test (`tests/test_*`) |
| `src/registry/` | Voice manifest YAML CRUD + reference audio trim/resample (15 sn, 24 kHz, mono) | Filesystem-backed, thread-safe (RLock) |
| `src/server/engine.py` | `BaseSynthEngine` protocol + `ChatterboxEngine` adapter + WAV/PCM helpers | Tek model instance, `_inference_lock` ile serialize edilmiş concurrent çağrılar |
| `src/server/streaming.py` | Streaming WAV header (RIFF "infinite size" trick) + sentence-chunked yield | Stateless |
| `src/server/auth.py` | `Authorization: Bearer <key>` → `NQAI_API_KEYS` env eşleşmesi (constant-time compare) | DB-backed key store v0.2'de gelir |
| `src/server/main.py` | FastAPI app, 12 route, CORS, lifecycle log | — |
| `scripts/bootstrap_voices.py` | `configs/seed_voices.yaml` → POST /v1/voices toplu enroll (idempotent) | — |
| `scripts/smoke_test.py` | Çalışan server'a 10-cümle × N-voice eval, per-call RTF + WAV dump | — |
| `notebooks/03-platform-server-colab.ipynb` | Colab T4/A100'da git clone → install → server boot → cloudflared tunnel → smoke | — |

## Voice manifest şeması (v0)

```yaml
voice_id: kebab-case-key      # primary key, regex [a-z0-9][a-z0-9-]{1,62}[a-z0-9]
display_name: "İnsan okur ismi"
language: tr                  # ISO 639-1
gender: neutral               # neutral | female | male
style_tags: [warm, child-directed, ...]
reference_audio: file.wav     # data/reference-audio/ içinde dosya adı
reference_seconds: 15.0       # trimmed length
source: elevenlabs | voice-talent | user-enroll | placeholder | bootstrap
license: internal-bridge | talent-contract:<id> | user-owned | ...
created_at: ISO-8601 UTC
created_by: api_key_prefix    # audit
```

Faz-2'de bu şema `voice-adapter-registry` (damıtma §4 Katman 2) tam manifest'ine doğru genişler: `base_model`, `adapter.*` (LoRA URI + checksum + encrypted), `watermark.*`, `fingerprint.*`, `eval.*`, `release.*`. v0.1 manifest'i schema-uyumlu — sadece doldurulmamış alanlar yok.

## Performans bütçesi

| Donanım | Cümle başı (5-8 sn ses) | RTF | TTFB (streaming) | Aynı anda istek |
|---|---|---|---|---|
| Kaggle T4 (15 GB) | 5-8 s | 1.2-1.4 | ~1.5-2.0 s | 1 (inference lock) |
| Colab A100 (40 GB) | ~1.5-2.5 s | 0.3-0.5 | ~0.5-0.8 s | 1-2 (lock hala var, T3 thread-unsafe) |
| RunPod L40S / H100 | < 1 s | < 0.2 | < 0.4 s | aynı, paralel için multi-process |

İnference lock T3 model'in concurrent `generate()` çağrıları altında saçma çıktı üretmesinden kaynaklı. Concurrency için **process-level paralelizm** (uvicorn `--workers N`, her worker kendi model instance'ı, ~3.5 GB VRAM × N) tek temiz yol. v0.2'de model server (Triton / vLLM-async) ile değiştirilecek.

## Güvenlik notları

- **Auth zorunlu** (`NQAI_REQUIRE_AUTH=true` default). API key olmayan istek 401.
- **Constant-time key compare** (`hmac.compare_digest`) — timing side-channel kapalı.
- **Reference audio upload size cap** (`NQAI_ENROLL_MAX_MB`, default 20 MB).
- **Text length cap** (`NQAI_MAX_CHARS`, default 4000).
- **CORS**: default `*` — production öncesi `NQAI_CORS_ORIGINS` dashboard host'una daraltılmalı.
- **Watermark + fingerprint YOK**. Damıtma §4 Katman 3 (`voice-governance-layer`) v0.2'de eklenir. Bu nedenle **v0.1 public clone değil**: enrollment auth'lu, her enroll edilen ses bir API key'e bağlı (`created_by` audit alanı).
- **KVKK / FSEK — v0.1 kapsamı dışı**: lansman seti operatörün kendi sesinin türev varyasyonları (ElevenLabs preset → reference audio); rıza içsel olduğu için kişisel veri / icracı sanatçı hak çerçevesi devreye girmiyor. Dışarıdan voice talent eklediğimiz an (Faz-1 hafta 5+) damıtma §5'teki 8-madde rider + KVKK aydınlatma metni şart olur.

## Bilinen sınırlar (v0.1)

1. **Türkçe SFT yok** — base Chatterbox Multilingual zero-shot kalitesi. Sayı/tarih/kısaltma TN katmanı bunu kısmen kapatır.
2. **Single-node**. Horizontal scale için load balancer + her node kendi disk-cache'i. Voice catalog tek-yazıcı (registry FS lock).
3. **Tek model serialize concurrent**. p99 latency yük altında bozulur.
4. **Streaming sadece tek istemciye linear**. Multi-tenant streaming için fan-out gerekir.
5. **Persist voice catalog filesystem'de**. Multi-node deploy'da Postgres veya S3-backed registry'e geçilir.
6. **Eval suite yok**. Smoke testi sentezliyor ama UTMOSv2 / NISQA / Whisper-WER ölçümleri Faz-1 eval (`src/eval/`) gelene kadar manuel dinleme.

## Faz-2'ye giden yol (sırayla)

1. **Türkçe SFT** — Chatterbox base'i ~10-50 saat etiketli Türkçe corpus ile fine-tune (RunPod A100 × 2-4 gün). Aynı API, daha iyi taban.
2. **Per-character LoRA bank** — 5 ses için ayrı LoRA, manifest `adapter.*` alanları dolar. Engine `load_adapter(voice.adapter)` hook'u ekler.
3. **Multi-process model serving** — uvicorn workers veya Triton; inference lock kalkar.
4. **Eval suite L1-L5** (damıtma §6). CI'da L1, haftalık L2-L4, aylık L5.
5. **Voice governance** — AudioSeal watermark + fingerprint registry + access scope + audit log.
6. **DB-backed key store + rate limit + usage metering** — geliştirici platformu açılınca şart.
