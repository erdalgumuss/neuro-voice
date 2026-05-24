# VoxCPM2 + LoRA Inference Serving Araştırma ve Referans Notu

**Durum:** Araştırma / referans notu, mimari karar belgesi değildir.  
**Tarih:** 2026-05-24  
**Kapsam:** VoxCPM2 + LoRA tabanlı Türkçe TTS/voice-clone servisinin nerede, nasıl ve hangi mühendislik sınırlarıyla dağıtılacağını araştırmak.  
**Kaynak bağlamı:** Gemini araştırması + mevcut repo durumu + Colab denemeleri.

Bu doküman nihai karar vermek için değil, sonraki mühendislik kararlarını beslemek için tutulur. Gemini'nin önerilerini reddetmiyoruz; ancak repo'yu bilmeden yazdığı yerleri "varsayım" olarak işaretliyoruz.

---

## 1. Yönetici Özeti

Araştırmanın ana yönü beklediğimiz gibi:

- API katmanı ile GPU inference aynı süreçte olmamalı.
- Public API hafif kalmalı; auth, quota, job state, voice catalog ve kullanıcı akışını taşımalı.
- VoxCPM2 + LoRA modeli sıcak tutulan ayrı GPU worker üzerinde çalışmalı.
- Uzun veya pahalı üretimler kuyruk üzerinden yürümeli.
- Üretilen ses dosyaları lokal diskte değil S3/R2 uyumlu object storage'da tutulmalı.
- Her üretimde model, LoRA, voice, cfg, timestep, süre ve RTF kaydedilmeli.
- Streaming ileride gerekli olacak; fakat ilk ürünleşme için async job + signed URL daha temiz başlangıç.

Mevcut repo açısından kritik not:

- Repo'da FastAPI tabanlı çalışan TTS API zaten var.
- LoRA runtime entegrasyonu mevcut branch'te çalışır hale getirildi.
- Repo'da ürün/playground arayüzü henüz yok; sadece admin template'leri var.
- `src/frontend/` web UI değil, Türkçe text normalization / segmentation katmanı.
- Mevcut `/v1/tts` doğrudan WAV dönen synchronous endpoint; async job mimarisi henüz uygulanmış değil.

---

## 2. Araştırmanın Doğruladığı Ana Mimari Desen

Araştırma aşağıdaki deseni öneriyor ve bu desen bizim kullanımımıza uyuyor:

```text
Frontend / Playground / Client SDK
   |
   | HTTPS
   v
Public API Gateway
   - auth
   - rate limit
   - quota / billing
   - voice catalog
   - job state
   |
   | enqueue
   v
Queue / Job Broker
   |
   | consume
   v
GPU Worker
   - VoxCPM2 base model
   - LoRA adapter
   - reference audio
   - inference params
   |
   | write
   v
Object Storage
   - WAV / MP3 outputs
   - reference audio
   - LoRA adapters
   |
   | signed URL / status
   v
Client
```

Bu mimarinin çalışma adı:

- **Decoupled inference serving**
- **Queue-based model serving**
- **Async TTS job serving**

Bu adlar araştırma ve vendor dokümanlarında aranacak doğru anahtar kelimeler.

---

## 3. Mevcut Repo Gerçekliği

Gemini araştırması repo'yu bilmeden yazıldığı için aşağıdaki düzeltmeler önemli.

### 3.1 Var Olanlar

Mevcut repo aşağıdaki parçaları içeriyor:

- FastAPI app: `src/server/main.py`
- VoxCPM2 runtime adapter: `src/server/engine.py`
- Voice registry ve manifest sistemi: `src/registry/catalog.py`
- Türkçe text frontend: `src/frontend/normalize.py`, `numbers.py`, `segment.py`
- Admin web template'leri: `src/server/admin/templates/`
- DB modelleri, tenant, API key ve usage repository katmanı
- Boto3, Redis, SQLAlchemy, Jinja2 bağımlılıkları
- HTTP TTS endpoint'leri:
  - `POST /v1/tts`
  - `POST /v1/tts/stream`
  - `GET /v1/voices`
  - `POST /v1/voices`
- LoRA runtime env desteği:
  - `NQAI_LORA_PATH`
  - `NQAI_LORA_CONFIG_PATH`
  - `NQAI_CFG_VALUE`
  - `NQAI_INFERENCE_TIMESTEPS`
  - `NQAI_OPTIMIZE`

### 3.2 Henüz Yok / Varsayım Olanlar

Gemini metnindeki bazı ifadeler mevcut repo için doğru değil; bunları hedef/hipotez olarak okuyacağız:

| Gemini ifadesi | Repo gerçekliği | Not |
|---|---|---|
| "Repo'ya kalıcı playground entegre edilmiştir" | Henüz ürün/playground UI yok | Sadece admin HTML template var |
| "İşler PostgreSQL + Redis'e yazılır" | TTS endpoint şu an direct response döner | Async job state henüz yok |
| "GPU worker ayrı Docker process" | Şu an API process modeli de yükleyebiliyor | Ayrıştırma hedef |
| "R2'ye çıktı yazılır" | Boto3 bağımlılığı var ama TTS output path'i direct response | Object storage entegrasyonu hedef |
| "Dinamik LoRA R2'den çekilir" | Global/env LoRA path destekleniyor | Per-job/per-voice remote adapter cache hedef |
| "RTF 0.13" | Bizim Colab ölçümümüz RTF ~2.7 | Provider benchmark şart |

Bu farklar araştırmayı geçersiz yapmıyor; sadece "mevcut durum" ile "hedef mimari" arasındaki mesafeyi gösteriyor.

---

## 4. Kabul Edilen Araştırma Sinyalleri

Bu sinyaller yön olarak doğru kabul ediliyor, ancak uygulama kararı ayrı karar dokümanına veya PR'a bağlanmalı.

### 4.1 API ve GPU Worker Ayrılmalı

API process'i GPU modeli taşırsa:

- cold start kullanıcıya yansır,
- CUDA/OOM çökmesi public API'yi düşürür,
- autoscale karmaşıklaşır,
- concurrent isteklerde latency kontrolden çıkar.

Referans desen:

```text
API: stateless, CPU, hızlı
Worker: stateful, GPU, sıcak model
Queue: backpressure ve retry
Storage: output artifact
```

### 4.2 Queue Olmadan Ürünleşme Riskli

TTS istekleri pahalı ve değişken süreli. Bu yüzden synchronous endpoint demo için yeterli olsa da, public ürün için job modeli daha güvenli.

Araştırmadaki adaylar:

- Dramatiq + Redis
- Redis Streams + FastStream
- Celery + Redis/RabbitMQ
- Temporal

Repo'nun mevcut `scale-roadmap.md` dokümanı Redis Streams + FastStream yönüne daha yakın duruyor. Gemini ise Dramatiq'i daha sade MVP adayı olarak öne çıkarıyor. Bu iki öneri çelişmek zorunda değil; benchmark/PoC ile seçilmeli.

### 4.3 Output Storage Gerekli

WAV çıktısını API process memory'sinde üretip doğrudan döndürmek demo için iyi. Ürün için şu akış daha doğru:

```text
worker generates audio
worker writes object storage
API returns signed URL
client downloads/plays
```

Cloudflare R2 iyi aday çünkü S3 uyumlu ve egress maliyeti avantajlı. AWS S3 daha olgun ama egress maliyeti yüksek olabilir.

### 4.4 Idempotency Zorunlu

TTS pahalı bir işlem olduğu için aynı request iki kez çalışmamalı.

Araştırma Stripe tarzı idempotency modelini öneriyor:

- `Idempotency-Key` veya `request_id`
- Redis/Postgres üzerinde 24 saatlik eşleşme
- processing/completed/failed state
- duplicate request'te aynı job veya sonuç döndürme

Bu bizim için doğru sinyal.

### 4.5 Streaming İkinci Faz

Realtime voice agent için WebSocket şart olabilir. Ama ilk ticari TTS/playground/voice generation ürünü için async job + signed URL yeterli ve daha temiz.

Streaming araştırması yine de değerli:

- `context_id`
- `continue: true/false`
- cancel / barge-in
- chunked PCM veya Opus
- TTFB ölçümü

Mevcut `/v1/tts/stream` gerçek token-level streaming değil; cümle bazlı generation tamamlandıkça WAV chunk döner. Bu fark not edilmeli.

---

## 5. Doğrulanması Gereken İddialar

Bu bölüm karar değil, test listesi.

### 5.1 RTF ve Maliyet

Gemini araştırması `RTF ~0.13` varsayımıyla maliyet modeli kuruyor. Bu ancak belirli hızlandırılmış runtime ve belirli GPU sınıfı için doğru olabilir.

Bizim ölçülen Colab API testimiz:

```text
generated_audio_duration_seconds: 5.000
elapsed_seconds: 13.836
RTF: 2.767
```

Bu nedenle gerçek maliyet hesabı için şu benchmarklar şart:

| Ortam | GPU | cfg | timesteps | optimize | Ölçüm |
|---|---|---:|---:|---|---|
| Colab | T4/L4/A100 ne geldiyse | 1.5 | 20 | false | baseline |
| RunPod | RTX 4090 | 1.5 | 12/16/20 | false/true | MVP maliyet |
| RunPod | L4 veya A10G | 1.5 | 12/16/20 | false/true | daha stabil seçenek |
| Lambda | A10/A6000 | 1.5 | 12/16/20 | false/true | prod alternatif |

Kaydedilecek metrikler:

- cold start seconds
- warm request elapsed seconds
- generated audio seconds
- RTF
- VRAM peak
- GPU utilization
- queue wait
- first byte time
- error rate
- output quality notu

### 5.2 Concurrency

Mevcut runtime tarafında inference lock serialize ediyor. Yani aynı process içinde concurrent request teoride gelse de model çağrıları sıraya giriyor.

Test edilmeden kabul edilmemesi gereken iddialar:

- "Tek GPU 4-12 paralel iş kaldırır"
- "Triton/Ray ile doğrudan yüksek concurrency gelir"
- "vLLM/Triton VoxCPM2 LoRA pipeline'a kolay oturur"

Önce process-level concurrency ve worker sayısı ölçülmeli.

### 5.3 Dynamic LoRA Adapter

Araştırma LoRA adapter'larının R2/S3'ten dinamik çekilmesini öneriyor. Bu doğru hedef, ama test edilmesi gereken konular var:

- LoRA load süresi
- adapter cache invalidation
- VRAM etkisi
- aynı base model üzerinde per-voice adapter switch maliyeti
- fallback ve checksum doğrulama

Şimdiki repo global LoRA path ile çalışıyor. Bu MVP için yeterli; multi-voice product için per-voice adapter manifest'i gerekir.

### 5.4 Provider Fiyatları ve Kapasite

RunPod, Modal, Lambda, AWS/GCP fiyatları ve stok durumu hızlı değişir. Araştırma notundaki fiyatlar karar tablosu değil, başlangıç varsayımıdır.

Her provider için ayrı güncel kontrol gerekir:

- saatlik GPU fiyatı
- disk/volume fiyatı
- cold start davranışı
- warm worker tutma maliyeti
- public endpoint desteği
- log/metric imkanı
- network egress
- kapasite garantisi

---

## 6. İncelenecek Referans Sistemler

### 6.1 Model Serving / Inference

- NVIDIA Triton Inference Server  
  https://docs.nvidia.com/deeplearning/triton-inference-server/

- KServe  
  https://kserve.github.io/website/docs/concepts

- Ray Serve  
  https://docs.ray.io/en/latest/serve/

- Baseten / Truss  
  https://docs.baseten.co/  
  https://docs.baseten.co/truss/overview

- Replicate Cog  
  https://github.com/replicate/cog

### 6.2 GPU Provider ve Deployment

- RunPod Serverless / Pods  
  https://docs.runpod.io/serverless/overview  
  https://www.runpod.io/pricing

- Modal GPU  
  https://modal.com/docs/guide/cold-start  
  https://modal.com/pricing

- Lambda Cloud  
  https://lambda.ai/instances

### 6.3 TTS API Ürün Referansları

- ElevenLabs API  
  https://elevenlabs.io/docs/eleven-api/quickstart

- Cartesia TTS WebSocket  
  https://docs.cartesia.ai/api-reference/tts/websocket

- Deepgram Aura streaming  
  https://deepgram.com/learn/aura-text-to-speech-adds-websocket-support-for-input-streaming

### 6.4 Queue / Workflow

- FastAPI BackgroundTasks  
  https://fastapi.tiangolo.com/tutorial/background-tasks/

- Dramatiq  
  https://dramatiq.io/

- Celery  
  https://docs.celeryq.dev/en/stable/

- Temporal  
  https://temporal.io/

### 6.5 Storage / Security / Governance

- Cloudflare R2 presigned URLs  
  https://developers.cloudflare.com/r2/api/s3/presigned-urls/

- Stripe idempotent requests  
  https://docs.stripe.com/api/idempotent_requests

- OWASP API Security Top 10  
  https://owasp.org/API-Security/editions/2023/en/0x00-header/

- EU AI Act  
  https://eur-lex.europa.eu/eli/reg/2024/1689/oj

- NIST AI Risk Management Framework  
  https://www.nist.gov/itl/ai-risk-management-framework

---

## 7. Araştırma İçin Karşılaştırma Matrisi

Her provider ve serving yaklaşımı için bu tablo doldurulmalı.

| Soru | Neden önemli? |
|---|---|
| Cold start kaç saniye? | Kullanıcı ilk istekte bekler mi? |
| Warm worker tutma maliyeti ne? | Demo ucuz, prod pahalı olabilir |
| Model dosyası nerede duruyor? | Image boyutu, volume, cache stratejisi |
| LoRA adapter nasıl yükleniyor? | Multi-voice ve karakter versiyonlama |
| Aynı GPU'da concurrency kaç? | Maliyet ve latency |
| RTF kaç? | Dakika başı maliyetin temeli |
| TTFB kaç? | Realtime/streaming deneyim |
| Output storage entegrasyonu kolay mı? | Artifact lifecycle |
| Logs/metrics/traces var mı? | Prod debug |
| GPU availability güvenilir mi? | Kullanıcıya SLA verme |
| Fiyat modeli nasıl? | Saatlik, serverless, storage, egress |
| Docker/CUDA kontrolü ne kadar? | VoxCPM2 dependency riski |

---

## 8. Önerilen PoC ve Deneyler

Bu bölüm karar değil; araştırmayı ölçüme dönüştürmek için deney listesi.

### Deney A: RunPod Dedicated Benchmark

Amaç: Colab dışındaki gerçek GPU performansını ölçmek.

Ölçülecek kombinasyonlar:

```text
cfg_value: 1.3, 1.5, 1.8
inference_timesteps: 12, 16, 20
optimize: false, true
text length: kısa / orta / uzun
voice: neeko-proto-api
```

Çıktı:

- `benchmark_results.csv`
- örnek WAV dosyaları
- VRAM ve elapsed time notları
- kalite dinleme notu

### Deney B: Kalıcı Playground

Amaç: Colab dışı test deneyimi oluşturmak.

İlk sürüm için iki seçenek:

- FastAPI içinde `/playground` Jinja/HTMX sayfası
- ayrı Vite/Next frontend

Araştırma notu karara zorlamaz. Fakat en hızlı repo-içi test yüzeyi FastAPI/Jinja olabilir.

### Deney C: Async Job Endpoint PoC

Amaç: Direct `/v1/tts` yanında async modelin doğru çalıştığını görmek.

Önerilen endpoint taslağı:

```text
POST /v1/tts/jobs
GET  /v1/tts/jobs/{job_id}
GET  /v1/tts/jobs/{job_id}/audio
```

State örnekleri:

```text
queued
running
completed
failed
cancelled
```

### Deney D: Object Storage Output

Amaç: WAV dosyasını response body yerine R2/S3'e yazmak.

Ölçülecekler:

- upload latency
- signed URL generation
- expiration behavior
- retry behavior
- local disk fallback

### Deney E: LoRA Adapter Cache

Amaç: per-voice LoRA adapter kullanımını ölçmek.

Ölçülecekler:

- ilk load süresi
- sonraki request cache süresi
- adapter switch latency
- checksum doğrulama
- invalid adapter fallback

---

## 9. API Contract Referans Taslağı

Bu taslak karar değildir; ürün API'sini tartışmak için başlangıçtır.

### Async Job Create

```http
POST /v1/tts/jobs
Authorization: Bearer <api_key>
Idempotency-Key: <client_request_id>
Content-Type: application/json
```

```json
{
  "voice_id": "neeko-proto-api",
  "text": "Merhaba. Ben Neeko. Seni duyuyorum.",
  "generation_params": {
    "cfg_value": 1.5,
    "inference_timesteps": 20,
    "format": "wav"
  },
  "callback_url": null,
  "metadata": {
    "project_id": "demo"
  }
}
```

### Async Job Response

```json
{
  "job_id": "job_tts_...",
  "status": "queued",
  "created_at": "2026-05-24T10:00:00Z"
}
```

### Job Status

```json
{
  "job_id": "job_tts_...",
  "status": "completed",
  "metrics": {
    "queue_wait_seconds": 0.4,
    "inference_seconds": 12.8,
    "generated_audio_seconds": 5.0,
    "rtf": 2.56
  },
  "output": {
    "audio_url": "https://...",
    "expires_at": "2026-05-24T11:00:00Z",
    "content_type": "audio/wav"
  }
}
```

---

## 10. Güvenlik ve Hukuki Referans Notları

Araştırmanın güvenlik tarafındaki sinyalleri önemli:

- Text length cap zorunlu.
- Per-key ve per-tenant rate limit zorunlu.
- Queue depth limiti olmalı.
- Voice ownership metadata tutulmalı.
- Reference audio ve generated audio retention politikası olmalı.
- Voice clone için açık izin ve audit trail gerekir.
- Synthetic audio disclosure / labeling konusu pazar ve ülkeye göre takip edilmeli.

Mevcut repo açısından not:

- Admin/auth/tenant katmanı başlamış durumda.
- Voice manifest'te `source`, `license`, `created_by` alanları var.
- Daha güçlü governance için ileride `consent`, `release`, `watermark`, `fingerprint`, `retention` alanları gerekir.

---

## 11. Araştırma Soruları

Bu sorular kapanmadan "prod mimari kararı" verilmemeli.

1. İlk ürün yüzeyi ne olacak: playground, API-only, B2B dashboard, çocuk karakter stüdyosu?
2. İlk SLA hedefi ne: 30 sn içinde sonuç mu, realtime mı?
3. İlk GPU provider hangisi: RunPod dedicated, RunPod serverless, Lambda, Modal?
4. Async queue için Redis Streams mi, Dramatiq mi?
5. Output retention kaç saat/gün?
6. Kullanıcı ses klonlama ne zaman açılacak?
7. Voice consent ve lisans akışı ilk sürümde nasıl tutulacak?
8. LoRA adapter per-voice mı, global character model mi?
9. Streaming gerçek ürün ihtiyacı mı, yoksa ikinci faz mı?
10. Hedef fiyatlandırma dakika başı mı, karakter başı mı, paket mi?

---

## 12. Araştırma Sonucu Olarak Şimdilik En Güçlü Hipotez

Bu karar değildir, ama bugünkü en güçlü mühendislik hipotezi:

```text
Faz 0:
  Mevcut sync API + Colab/RunPod benchmark + geçici test UI

Faz 1:
  Kalıcı playground
  RunPod/Lambda benchmark
  LoRA runtime stabilizasyonu
  metrics/logging

Faz 2:
  Async TTS jobs
  Redis queue
  ayrı GPU worker
  R2/S3 output storage
  idempotency

Faz 3:
  per-voice LoRA adapter cache
  stronger governance
  billing/quota
  product dashboard

Faz 4:
  WebSocket streaming
  Ray Serve / Triton / Modal değerlendirmesi
  advanced concurrency
```

Bu hipotez Gemini araştırmasıyla uyumlu, fakat repo gerçekliğini de hesaba katıyor. Asıl karar; benchmark, PoC ve ürün önceliği netleştikten sonra `docs/decisions/` altında ayrı yazılmalı.
