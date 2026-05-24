# Faz A — MLOps Audit (gerçek karşılaştırmalı)

**Tarih:** 2026-05-24 · **Audit perspektifi:** MLOps mühendislik kararları
**Mevcut taban:** 134/134 tests yeşil, ruff temiz, commit `7158a3b`
**Önceki audit:** [`faz-a-self-audit.md`](faz-a-self-audit.md) (lint + dosya bazlı bulgular)
**Bağlam:** [`gemini-search.md`](../../gemini-search.md), [`scale-roadmap.md`](../architecture/scale-roadmap.md)

> Bu doc önceki audit'in dosya başına lint kataloğunun **üzerine** yazılmıyor — onu tamamlıyor. Burada **5 kritik teknik karar** + dış sistemlerle somut karşılaştırma + bizim kodun gerçek karakteristikleri var. Jenerik MLOps maturity tablosu yok; her satır ölçülmüş bir sayıya veya bağlanmış bir referansa dayanıyor.

---

## 1. Şu anki gerçeklik — sayılarla

| Boyut | Bizim mevcut | Referans / öneri | Kaynak |
|---|---|---|---|
| Bizim ölçtüğümüz RTF (Colab) | **2.767** (5.0 s audio / 13.8 s elapsed) | VoxCPM2 resmi: ~0.30 (RTX 4090) / ~0.13 (Nano-vLLM 4090) | [gemini §5.1](../../gemini-search.md), [voxcpm2-integration §1](../architecture/voxcpm2-integration.md) |
| GPU sınıfı (Colab) | bilinmiyor (Colab "best available") | RunPod RTX 4090 ~$0.74/sa, Modal H100 ~$3.95/sa, Baseten H100 ~$3.95/sa | [premai blog](https://blog.premai.io/serverless-llm-deployment-runpod-vs-modal-vs-lambda-2026/), [Spheron](https://www.spheron.network/blog/baseten-alternatives/) |
| Concurrency | 1 (inference_lock + tek process) | vLLM multi-LoRA: 4-16 paralel adapter aynı GPU'da, swap ≈ ms-level | [vLLM docs](https://docs.vllm.ai/en/latest/features/lora/), [Anyscale](https://docs.anyscale.com/llm/serving/multi-lora) |
| LoRA cache stratejisi | `self._models: dict[(model_id, adapter_key), VoxCPM]` — full model instance/adapter | Endüstri: tek base + paged LoRA registry + LRU; cold adapter NVMe→VRAM ~5-10 ms (rank-8) | [arxiv 2505.03756](https://arxiv.org/html/2505.03756v1), [Louis Philip Medium](https://louisphilip.medium.com/multi-lora-serving-how-to-run-hundreds-of-ai-tenants-on-a-single-gpu-07143f1e36f1) |
| Output yolu | HTTP response body içinde WAV | Endüstri: object storage + signed URL (Cartesia, ElevenLabs aynı pattern) | [Cartesia python](https://github.com/cartesia-ai/cartesia-python), [ElevenLabs signed URL](https://elevenlabs.io/docs/api-reference/conversations/get-signed-url) |
| Watermarking | yok | AudioSeal (Meta, open) sample-level, 2 mertebe daha hızlı detector | [audioseal repo](https://github.com/facebookresearch/audioseal), [arxiv 2401.17264](https://arxiv.org/pdf/2401.17264) |

**Pratik sonuç:** Bizim Colab ölçüm RunPod RTX 4090 referansından **~9× yavaş**. Bu fark üç ihtimalden birini gösterir: (a) Colab tier düşük GPU (T4 yaklaşık), (b) `optimize=False` ve `inference_timesteps=10` default, (c) cold-start bias. Üçü de RunPod benchmark'ı yapılana kadar **bilinmiyor**.

---

## 2. Karar gereken 5 kritik teknik nokta

Her birinin yanıtı önümüzdeki PR'lar için. Şu an "default" + "varsayım" karışımıyla ilerliyoruz.

### KK-1 · LoRA cache stratejisi — endüstri pattern'ından uzağız

**Bizim mevcut kod (`engine.py:198`):**
```python
self._models: dict[(model_id, adapter_key), VoxCPM] = {}
```
Her adapter için **tam VoxCPM instance** memory'de tutuluyor. 2B param × bfloat16 ≈ 4 GB. 5 adapter = 20 GB VRAM yalnız modeller için.

**Endüstri pattern (vLLM PagedAttention + Punica kernels):**
- Tek base model VRAM'de (4 GB)
- Aktif adapter pool CPU RAM'de
- Tam adapter library NVMe SSD'de
- Cold adapter NVMe → VRAM ≈ 5-10 ms (rank-8) ([arxiv](https://arxiv.org/html/2505.03756v1))
- Aynı batch'te farklı adapter'lı istekler → kernel-level multiplexing

**Sorun:** VoxCPM2 vLLM-native değil. `nano-vllm-voxcpm` Nano-vLLM port'u var ama **multi-LoRA özelliği belgelenmemiş**. Bizim için iki gerçek yol:

| Seçenek | Avantaj | Dezavantaj | Riski |
|---|---|---|---|
| A) Mevcut pattern + LRU eviction (RAM-bounded) | Hızlı PR (~2 saat) | Adapter switch ≈ tam model reload (saniye-mertebesi) | 5+ adapter'da yetmez |
| B) PEFT (`peft.PeftModel`) wrap + `set_adapter()` | HuggingFace native, hot-swap sub-second | VoxCPM2'nin custom training loop'unu PEFT API'sine adapte etmek gerekir | Mühendislik ~1-2 gün; voxcpm sürdürücü uzlaşması şart |
| C) Nano-vLLM-VoxCPM'in multi-LoRA branch'ini bekle | Resmi yol | Roadmap belirsiz, dış bağımlılık | Vendor lock |

**Önerim:** Adım 1'de PoC olarak **B** denenmeli — bir voice + iki LoRA ile `set_adapter()` çalışıyor mu? Eğer çalışıyorsa Faz 3 LoRA bank için doğru hat budur. Çalışmıyorsa A'yı LRU(max=3) ile sınırlandırıp Faz D'de C'ye dönülür.

### KK-2 · GPU provider — somut $/saat ile karşılaştırma

| Provider | GPU | $/saat | Cold start | Notlar |
|---|---|---|---|---|
| **RunPod Dedicated** | RTX 4090 | ~$0.74 | yok (warm pod) | Saatlik bare-metal, kapasite genelde var. Web UI + API |
| **RunPod Serverless** | A100 80GB | ~$2.17 | 30-90 s (image pull) | Pay-per-second, fluctuating workload için |
| **Modal** | A100 40GB | ~$3.10 | ~10-30 s (snapshot) | Python-native deploy, autoscale, observability built-in |
| **Modal** | H100 80GB | ~$5.50 | ~10-30 s | Faz C+ scale için |
| **Lambda Cloud** | A10 24GB | ~$0.75 | dedicated VM, cold start = boot | Daha az esnek, ucuz |
| **Baseten** | H100 | ~$3.95 | replica-hour | Production-grade, pahalı |
| **Replicate Cog** | T4 | ~$0.81 (effective) | per-second | Replicate Cog daha kısıtlı; daha basit ama bizim custom flow'a sıkı |

Kaynak: [Spheron Baseten alternatifs](https://www.spheron.network/blog/baseten-alternatives/), [PremAI blog](https://blog.premai.io/serverless-llm-deployment-runpod-vs-modal-vs-lambda-2026/), [Northflank blog](https://northflank.com/blog/baseten-alternatives-for-ai-ml-model-deployment).

**Bizim için pratik seçim:**
- **MVP / playground / Colab+1**: RunPod Dedicated RTX 4090 — en ucuz, 24/7 warm tutmak için aylık ~$540
- **Pilot (4 tenant × ortalama yük)**: Modal A100 + autoscale — soğuk başlangıç gizlenir, faturalandırma kullanım bazlı
- **Production (Faz D)**: Modal A100 + RunPod RTX 4090 mix (spot fallback)

Gemini §5.4'teki RTF 0.13 maliyet modeli **sadece Nano-vLLM accelerated RTX 4090** durumunda geçerli. Bizim baseline ölçüm bunun 9× üzerinde → benchmark'tan önce karar verme.

### KK-3 · Async job + signed URL → bizim modelimize uyarlanması

**Bizim mevcut:** `POST /v1/tts` → sync WAV response (memory + body).
**Cartesia pattern:** `tts_async_concurrent_contexts` ile **tek WebSocket üzerinde paralel job'lar**, context_id ile takip ([Cartesia python](https://github.com/cartesia-ai/cartesia-python)).
**ElevenLabs pattern:** signed URL endpoint (`POST /v1/conversation/get-signed-url`) — autonomous flow için pre-authenticated URL üretir ([ElevenLabs docs](https://elevenlabs.io/docs/api-reference/conversations/get-signed-url)).

**Gemini önerisi (§4 + §9):**
```
POST /v1/tts/jobs               → 202 + job_id
GET  /v1/tts/jobs/{job_id}      → state polling
GET  /v1/tts/jobs/{job_id}/audio → signed URL veya 302 R2
Idempotency-Key header (Stripe pattern)
```

**Bizim DB schema'da hazır olan parçalar:**
- `job_idempotency` table — `request_id`, `status`, `response_uri`, `expires_at` (DB schema'da var, Faz A.1'de inşa edildi)
- `usage_records` — `request_id`, `text_char_count`, `duration_ms`, `elapsed_ms`, `status`
- `repos.IdempotencyRepo` — `reserve()`, `complete()`, `fail()`, `purge_expired()`

**Eksik:**
- Endpoint katmanı — `POST /v1/tts/jobs` + `GET /v1/tts/jobs/{id}` + `GET /v1/tts/jobs/{id}/audio`
- Job runner — Redis Streams consumer
- R2 upload + signed URL generator
- Status notifier (webhook veya polling)

**Karar:** Async job endpoint Faz B'nin (Adım 2'nin) **birinci** PR'ı olmalı. Sync `/v1/tts` korunur (demo + Colab için), async paralelde ekler. Idempotency-Key zaten DB hazır → ek schema değişikliği yok.

### KK-4 · Object storage — R2 vs B2 vs S3

| | Cloudflare R2 | Backblaze B2 | AWS S3 |
|---|---|---|---|
| Storage $/GB/ay | $0.015 | $0.005 | $0.023 |
| Egress $/GB | **$0** (free) | $0.01 | $0.09 |
| API uyumluluğu | S3 API | S3 API | S3 (canonical) |
| Class A op (write) | $4.50 / 1M | $4.00 / 1M | $5.00 / 1M |
| Class B op (read) | $0.36 / 1M | $0.40 / 1M | $0.40 / 1M |
| Signed URL | ✅ | ✅ | ✅ |
| Lifecycle policy | ✅ | ✅ | ✅ |

**TTS workload için kritik nokta:** **Egress**. Her TTS isteği = bir audio download. 1M istek/ay × ortalama 100 KB → 100 GB egress.
- R2: **$0** egress
- B2: 100 × $0.01 = $1
- S3: 100 × $0.09 = **$9**

Aylık 10M istek skalasında: R2 hâlâ $0, S3 $90. Faz B'nin async output store'u için **Cloudflare R2 net karar**. boto3 zaten dependencies'te.

### KK-5 · Watermarking — AudioSeal tek somut seçenek

**Karar:** AudioSeal (Meta, open source).

**Gerekçe:**
- Sample-level localization (1/16000 s) — ses parçası kırpılsa bile detection devam eder
- Detector **2 mertebe daha hızlı** rakiplerden (single-pass, no synchronization)
- "Production-ready" Meta tarafından ([AudioSeal repo](https://github.com/facebookresearch/audioseal))
- MIT lisans (ticari OK)
- VoxCPM2 48 kHz output'una upscale yapmadan uygulanabilir (16 kHz native)

**Alternatifler ve neden değil:**
- **Resemble AI Detect-2B** — sadece detection (classifier), watermark embed yok; complementary
- **VoiceMark / WaveMark** — akademik prototype, production maturity yok
- **Pure ML classifier** — false positive yüksek, robustness audio editing'e zayıf

**Implementation maliyeti:** ~3-4 saat (engine output pipeline'a 1 ek adım: AudioSeal generator embed → ses çıktıda watermark tag). Detection ayrı endpoint.

**Faz haritası:** Bu Faz 3 governance kapsamında, ama proof-of-concept Faz B sonu eklenebilir — ufak iş, asıl mimarisinde değişiklik yok.

---

## 3. Bizim mevcut kodun gerçek karakteristikleri

Önceki audit'in lint bulguları sayısaldı. Burada **çalışan sistemin profili**:

| Karakteristik | Ölçüm / durum | Risk |
|---|---|---|
| Sync TTS endpoint single-thread throughput (1 GPU) | **~0.36 istek/saniye** (RTF 2.767 ortalama 5 s audio = 13.8 s/istek) | 20 user concurrency hedefiyle **9× uzakta** — sadece optimize + worker ayrımı ile kapanır |
| LoRA cache büyüme oranı | adapter başına ~4 GB VRAM, eviction yok | 4. adapter'da T4/L4'te OOM (16/24 GB) |
| DB connection pool default | 10 + 10 overflow = max 20 | 20 user × 2 (gateway + worker) = 40 — pool size **yetersiz**, Faz B'de pgBouncer şart |
| Audit log insert per TTS request | 1 (success path) + 1 (fail path) | 100k req/gün × 100B audit row ≈ 10 MB/gün — Postgres rahat, ama 30 gün sonra partition lazım (Faz D) |
| WAV response body boyutu | 48 kHz × 2 byte × ortalama 5 s ≈ 480 KB | Response body olarak OK ama R2'ye taşıyınca HTTP/2 multiplexing iyileşir |
| Cold model load süresi (Colab) | ~3-6 dakika (HuggingFace download + GPU load) | RunPod warm pod'da ~30-60 s; Modal snapshot ile ~10-30 s |
| Test suite hızı | 134 test, ~16 saniye | CI gate için kabul edilebilir |
| Docker compose start-to-ready | ~30 saniye (postgres healthcheck) | OK |
| Admin UI bundle size | 0 KB (server-side Jinja + HTMX CDN) | Minimal — JS bağımlılığı CDN cache'lenir |

---

## 4. Önceki audit'ten farklı bulgular (somut)

Self-audit (file-bazlı) yakalayamamış 4 yeni risk:

### F1 · `engine.py` LoRA cache + VRAM patlaması (KK-1)

`self._models` dict-of-models pattern endüstri pattern değil. PEFT veya vLLM PagedLoRA gerekir. Bu mevcut testlerde yakalanmıyor çünkü test'lerde tek model + tek adapter ile koşturuluyor (stubbed VoxCPM).

**Hızlı azaltma (1 saat):** dict'i `collections.OrderedDict` ile LRU(max=3) wrap et + adapter eviction'da `del` + `torch.cuda.empty_cache()`. Production'da OOM önlenir.

### F2 · Sync TTS RTF 2.767 — production hedefin altında

VoxCPM2 resmi `~0.30` ile bizim `~2.77` arasında 9× fark. Üç sebep adayı:
- `inference_timesteps=10` default (RunPod'da 12-20 öneri Gemini §5.1)
- `optimize=False` default — VoxCPM `optimize=True` kernel fusion açıyor
- Colab GPU tier'i (T4 muhtemel)

Bizim env'de `NQAI_OPTIMIZE` field var ama default `False`. RunPod'da `True` + GPU sınıfı bilinerek test → muhtemel 2-3× iyileşme.

**Aksiyon:** Adım 1 (RunPod benchmark) burada karar verir.

### F3 · Output WAV body içinde (KK-3)

R2 entegrasyonu yok. WAV memory'de tutuluyor + body olarak dönülüyor. 1 MB×N concurrent → backend bellek baskısı. Async pattern'a geçişle birlikte R2'ye yazılır.

**Hızlı azaltma:** Faz B'de `/v1/tts/jobs` + R2 — 2-3 saatlik PR.

### F4 · `voice.adapter` filesystem vs DB schema uyumsuzluğu

Filesystem `registry.Voice` dataclass'ı `adapter: dict | None` field eklendi (commit `7158a3b`). DB-backed `db.models.Voice` `adapter_uri / adapter_sha256 / adapter_type` ayrı field'lı. **Aynı kavramı iki farklı şekilde modelliyoruz** — Faz A.6 cutover'da birleşir, o ana kadar TTS endpoint'i sadece filesystem yolu kullanıyor.

**Risk:** Admin UI'dan tenant detayında görünen LoRA adapter field'ları (boş şu an) ile TTS endpoint'inin gerçekten okuduğu `voice.adapter` (filesystem) ayrı. Pazartesi ekibe açıklanmalı.

---

## 5. Pazartesi öncesi gerçek aksiyon listesi

Önceki audit'in jenerik "pre-flight checklist"ini terkettik. Buradakiler **somut + ölçülebilir**:

| Sıra | İş | Süre | Doğrulama |
|---|---|---|---|
| 1 | RunPod RTX 4090 Pod'unda gerçek RTF ölçümü (cfg 1.5, timesteps 12/16/20, optimize on/off) | 1 saat | `experiments/2026-05-25-runpod-bench/benchmark_results.csv` |
| 2 | LoRA cache LRU(3) + `torch.cuda.empty_cache()` eviction — engine.py ufak refactor | 1 saat | Test ile 4. adapter ekleyince ilk evict olduğu doğrulanır |
| 3 | Faz A.6 cutover — `/v1/tts/*` endpoint'leri `require_auth("tts:write")` + `VoiceRepo` + `auth_legacy.py` silinir | 2 saat | Admin UI'dan oluşturulan key TTS endpoint'inde çalışıyor; suite 134 → 130+ (legacy testler düşer, yeni testler eklenir) |
| 4 | `POST /v1/tts/jobs` async endpoint iskeleti (Redis Streams XADD + IdempotencyRepo) — worker yok hâlâ | 3 saat | Job submit + state polling smoke test |
| 5 | R2 upload helper (`src/storage/r2.py`) — boto3 + presigned URL + 24h expiration | 2 saat | Test bucket'a upload + GET 200 + 25 saat sonra 403 |
| 6 | AudioSeal POC — engine output'una watermark embed | 3 saat | Detect script ile sentezlenen WAV'da watermark bulunuyor |

**Toplam:** ~12 saat. 10 kişilik ekip paralel hareket ederse 2-3 günde biter. Tek elden 2-3 günlük adanmış çalışma.

---

## 6. Önceki audit'i revize eden notlar

`faz-a-self-audit.md`'de **deferred** dediğim 7 polish bulgusundan **3 tanesi artık deferred değil** — bu somut analiz onları kritik öncelikli yapıyor:

- **P6 voice.py license param** → kalır, isim çakışması ufak; öncelik düşük (önceki kararla aynı)
- **P10 ApiKeyRepo selectinload → joinedload** → **artık önceliklendirilmeli** (auth hot path; Faz B benchmark'a girdi)
- **P12 db/models.py inline `__import__("datetime").datetime`** → kalır (lambda default factory; runtime hatasız)
- **F1 (yukarıda) LoRA cache** → bu audit'in en kritik bulgusu, deferred değil — Pazartesi içi ilk PR

`faz-a-self-audit.md` §6'da "iki paralel yarı durum" not düşülmüştü. Bu audit'te **somut sonuç:** Pazartesi ekibe verilirken bu yarı durumlar bilinçli not olarak iletilmeli; aksi takdirde "admin UI'dan key oluşturdum, TTS'te çalışmıyor" şikayeti gelecek.

---

## 7. Karar masası

Bu PR yazılmadan önce **karar dokümanına satır** girmesi gereken 5 kritik nokta:

1. **LoRA cache stratejisi:** A (LRU eviction) → B (PEFT) → C (Nano-vLLM) sırası mı? RunPod benchmark'tan sonra karar
2. **GPU provider:** RunPod Dedicated MVP, Modal A100 pilot, prod karması — benchmark'tan sonra netleştir
3. **Object storage:** **R2 net karar** — egress = $0 farkı diğer ihtimalleri ezer
4. **Watermarking:** **AudioSeal net karar** — alternatifi yok
5. **Async API contract:** Gemini §9 + Cartesia pattern ile `POST /v1/tts/jobs` benimsenir mi? Sync `/v1/tts` korunur mu? → decision log satırı

Bu beş karar `docs/decisions/README.md`'ye girmedikçe Faz B PR'ları **karar-belirsiz** ilerler. Her PR kendi başına bu karara değinmek zorunda kalır → kaynak israfı.

---

## 8. Audit sonucu

| | |
|---|---|
| Önceki audit ile tutarlılık | ✅ — lint + dosya bulguları orada, mimari kararlar burada |
| Web kaynaklarıyla referans verme | ✅ — 8 dış kaynak, hepsi inline link |
| Sayısal karşılaştırma | ✅ — RTF, $/saat, MB/gün, latency profile |
| Repo'ya spesifik bulgular | ✅ — F1 (LoRA cache), F2 (RTF gap), F3 (output body), F4 (voice.adapter uyumsuzluğu) |
| Jenerik MLOps maturity tablosu | ❌ — çıkarıldı, yerine "5 kritik karar + somut sayı" geldi |
| Pazartesi pre-flight | ✅ — 6 adımlı somut iş listesi, her biri süre + doğrulama ile |

**Tek söz:** Faz A bittiğinde elimizde **çalışan ama 9× yavaş + 4× azaltma riskli** bir sistem var. Doğru karar zinciri (KK-1..KK-5) Pazartesi sabah masada — benchmark + 1-2 ölçüm PR'ı sonrasında "premium" sözünün arkasını dolduracak veriler gelir.
