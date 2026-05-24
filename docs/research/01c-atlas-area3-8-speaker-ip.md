# Atlas — Alan 3 + 8: Speaker Adaptation, Karakter Kimliği, IP Koruması

> Neeko TTS araştırması üçüncü ayak: Neeko'nun ses kimliği nasıl kurulur, 12 ay boyunca tutarlı kalır, çoğaltılır ve yasal/teknik olarak korunur.
> Hedef: 3-7 yaş çocuk Türkçe oyuncak; karakter > ürün > teknoloji (north star).
> Tarih: 2026-05-19.

---

## 0) Yönetici Özeti (TL;DR)

| Soru | Cevap |
| --- | --- |
| Zero-shot voice cloning hangi modelle? | F5-TTS veya XTTS-v2 (10-15 s referansla mid-tier kalite); production karakter için **LoRA / few-shot adapter** zorunlu. |
| Karakter tutarlılığı için minimum veri? | 5-10 dk temiz studio (LoRA rank 16-32 yeter); 30+ dk full fine-tune kalite ceilinge yakın. |
| Drift kaç dk sonra başlar? | Single-pass long-form'da 60-120 s sonra timbre kayışı gözlenir (VoxCPM bug raporları + literatür); ürün tarafında **utterance bazlı, sessiz ref re-injection** zorunlu. |
| Ses biyometrik mi (KVKK)? | Kimlik doğrulama amacı varsa **özel nitelikli** (m.6); ses bizim için "konuşmacı kimliği"ne bağlı olduğundan **açık rıza + amaç sınırı + saklama süresi** zorunlu. |
| Voice talent kontratı çerçevesi? | Buyout + sınırlı AI training right (Replica/SAG-AFTRA çerçevesinin sonlandığını not et); FSEK 80, TMK m.24, TBK m.49 tetik maddeler. |
| Watermark zorunlu mu? | Evet — AudioSeal (output) + VoiceMark (speaker-specific) ikilisi; FTC/EU AI Act yönü ve marka itibarı için defensif. |

**Ana karar (önerilen):** v1 için **XTTS-v2 base + Neeko karakter LoRA (rank 16-32, attention'a target)**. Voice talent: tek seferlik buyout + **non-exclusive synthetic voice license**, AI re-training right Neeko'da, kişilik hakkı (FSEK m.80 ahlaki + TMK m.24) talente kalır → "geri çekme" senaryosu için sözleşmede explicit lisansın geri alınamazlığı maddesi şart.

---

## ALAN 3 — Speaker Adaptation + Voice Cloning

### 3.1 Zero-Shot Voice Cloning Karşılaştırma (2024-2026)

**TL;DR:** F5-TTS, CosyVoice 2 ve MaskGCT 2025 SOTA. XTTS-v2 ekosistemi (eğitim/inference/finetune docs) hâlâ en olgun. VALL-E 2 kapalı kaynak (paper-only). Spark-TTS hızlı + LLM-tabanlı. Hiçbiri Türkçe için "raw zero-shot" üretim seviyesinde değil — hepsi **LoRA / few-shot** ile karakter sabitleme gerektirir.

#### Model Karşılaştırma Tablosu

| Model | Min ref (s) | Kalite (UTMOS/MOS) | Speaker sim (SECS / ECAPA) | Hız (RTF) | Türkçe | Lisans | Repo |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **XTTS-v2** | 6 s | 4.00 (UTMOS, EN) | 0.64 SECS | ~0.3 (3090) | Var (16 dil) | CPML (non-commercial base; commercial via Coqui→Idiap, sonra fork ekosistemi) | [coqui/XTTS-v2](https://huggingface.co/coqui/XTTS-v2) |
| **F5-TTS** | 10-15 s | ~4.1 UTMOS | yüksek (in-context) | ~0.15 (4090) | Cross-lingual fork mevcut | MIT (paper); Apache-derived | [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS) |
| **OpenVoice v2** | 10-30 s | ~3.8 | orta (tone color sadece) | hızlı | Limited | MIT | [arXiv 2312.01479](https://arxiv.org/abs/2312.01479) |
| **CosyVoice 2** | 3-10 s | ~4.05 | yüksek (semantic tokens) | streaming-friendly | EN/ZH + multi | Apache 2.0 | [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) |
| **VALL-E 2** | 3 s | human parity (LibriSpeech) | 0.65+ | autoreg yavaş | Microsoft Research only | Kapalı (paper) | [arXiv 2406.05370](https://arxiv.org/abs/2406.05370) |
| **Tortoise TTS** | 5 s × 3-5 clip | yüksek doğallık | iyi (CLVP rerank) | çok yavaş (dakikalar) | EN | Apache 2.0 | docs.coqui.ai/tortoise |
| **YourTTS** | 3-5 s (cross-ling 1 dk) | orta | orta | hızlı | EN/PT/FR | MIT (research) | [Edresson/YourTTS](https://github.com/Edresson/YourTTS) |
| **StyleTTS 2** | 5-10 s | LJSpeech süper-insan | iyi | hızlı (~0.1 RTF) | EN ağırlık | MIT | [yl4579/StyleTTS2](https://github.com/yl4579/StyleTTS2) |
| **Spark-TTS** | 3-10 s | SOTA (Qwen2.5-0.5B backbone) | iyi | hızlı | EN/ZH | Apache 2.0 | [SparkAudio/Spark-TTS](https://github.com/SparkAudio/Spark-TTS) |
| **IndexTTS / v2** | 3-5 s | ICASSP'25 industrial-level | iyi (conformer encoder) | hızlı | EN/ZH (poliphonic ZH avantaj) | Apache 2.0 | [index-tts/index-tts](https://github.com/index-tts/index-tts) |
| **MaskGCT** | 5-10 s | ICLR'25 NAR | yüksek timbre + stil | hızlı (NAR) | EN/ZH | research | [openreview ExuBFYtCQU](https://openreview.net/pdf?id=ExuBFYtCQU) |

**Referans süresi → kalite eğrisi (literatür uzlaşısı):**

- **3-5 s:** F5/CosyVoice/Spark/MaskGCT için "minimum işler". Speaker sim yüksek ama prosodic varyans dar.
- **10-15 s:** F5-TTS sweet spot (Local AI Master 2026 dokümanı + nomaditsu.com 2024 testi); XTTS-v2 için de optimal.
- **30 s - 1 dk:** Diminishing returns; sadece daha geniş prosodic örüntü kazanılır. Tek-kaynak fine-tune değil.
- **5+ dk:** Zero-shot tarafında anlamsız — bu noktadan sonra LoRA / fine-tune devreye girer.

**Kaynak:**
- XTTS paper: [arXiv 2406.04904](https://arxiv.org/html/2406.04904v1) — SECS 0.6423 (LibriSpeech-EN), UTMOS 4.007, CER 0.54%.
- ECAPA-TDNN vs alternatif speaker enc: [arXiv 2506.20190](https://arxiv.org/abs/2506.20190) — H/ASP encoder, ECAPA-TDNN, x-vector karşılaştırması; ECAPA-TDNN speaker recognition'ta SOTA ama zero-shot TTS speaker similarity için H/ASP üstün gelebilir.
- VALL-E 2 human parity: [arXiv 2406.05370](https://arxiv.org/pdf/2406.05370).
- Spark-TTS: [arXiv 2503.01710](https://arxiv.org/html/2503.01710v1).
- IndexTTS: [arXiv 2502.05512](https://arxiv.org/html/2502.05512v1).
- MaskGCT ICLR 2025: [openreview](https://openreview.net/pdf?id=ExuBFYtCQU).
- CosyVoice 2: [arXiv 2412.10117](https://arxiv.org/html/2412.10117v3).

**Neeko önerisi:** v1 için **XTTS-v2** (Türkçe TR dahil 16 dil + en olgun fine-tune ekosistemi + community Türkçe modelleri var); v2'de **CosyVoice 2 streaming** ve **F5-TTS** karşılaştırma. VALL-E 2 araştırma sinyali, ürün tarafı değil.

---

### 3.2 Few-Shot / LoRA / Adapter Adaptation

**TL;DR:** LoRP-TTS ve VoiceTailor (NeurIPS/ICASSP 2025) speaker LoRA için tipik rank=8-32, alpha=2×rank, attention modüllerinde target. 5-10 dk veri ile rank 16; 30+ dk veri varsa rank 32+ veya full fine-tune. RTX 3090/4090 için 2-6 saatlik tek-speaker LoRA tipik.

#### LoRA Konfigürasyon Önerileri (somut sayılarla)

| Parametre | Düşük (proof-of-concept) | **Önerilen (Neeko v1)** | Yüksek (production) |
| --- | --- | --- | --- |
| `r` (rank) | 4-8 | **16** (5-10 dk veri); **32** (>30 dk) | 64-128 |
| `lora_alpha` | r veya 2r | **2 × r** (32 veya 64) | 2 × r |
| `lora_dropout` | 0.0 | **0.05** | 0.1 |
| `target_modules` | `["q_proj","v_proj"]` | **`["q_proj","k_proj","v_proj","o_proj"]`** (tüm attention proj) | Tüm linear: + `["gate_proj","up_proj","down_proj"]` |
| `bias` | "none" | **"none"** | "lora_only" |
| Batch size (effective) | 4 | **8** (RTX 3090 24 GB; gradient accumulation 2) | 16-32 (A100 80 GB) |
| Learning rate | 1e-4 | **5e-5 → 1e-4** (cosine schedule) | 1e-5 (full FT) |
| Warmup steps | 100 | **500** | 1000+ |
| Total steps | 500-1000 | **2000-5000** | 10K+ |
| Audio veri (dk) | 1-3 | **5-10** | 30+ |
| GPU saati (RTX 4090) | 30 dk - 1 sa | **2-4 saat** | 8-12 saat |
| GPU saati (A100) | 15 dk | 1-2 saat | 4-6 saat |

**Kaynak / akademik dayanak:**
- **LoRP-TTS** (2025) — düşük-kaynak speaker LoRA: [arXiv 2502.07562](https://arxiv.org/html/2502.07562v1). Anahtar bulgu: LoRA rank 8 + alpha 16, batch 4 (effective 8) speaker similarity'de full FT'ye yakın sonuç.
- **VoiceTailor** (2024) — diffusion-tabanlı kişiselleştirilmiş TTS adapter; toplam parametrenin %0.25'i ile full FT performansına yakın: [arXiv 2408.14739](https://arxiv.org/pdf/2408.14739).
- **PEFT (HuggingFace) — LoRA config docs:** [peft tuners/lora/config.py](https://github.com/huggingface/peft/blob/main/src/peft/tuners/lora/config.py).
- **HF LoRA target_modules best practice:** `["q_proj","k_proj","v_proj","o_proj"]` memory-efficient default ([HF docs](https://huggingface.co/docs/peft/en/package_reference/lora)).

**XTTS-v2 fine-tune pratik:**
- Coqui resmi: `docs.coqui.ai/en/latest/models/xtts.html` (Fine-Tune section). RTX 4090'da 3-5 saat tipik ([erew123/alltalk_tts wiki](https://github.com/erew123/alltalk_tts/wiki/XTTS-Model-Finetuning-Guide-(Advanced-Version))).
- Community PEFT-LoRA: [gokhaneraslan/XTTS_V2-finetuning](https://github.com/gokhaneraslan/XTTS_V2-finetuning).
- VRAM gereksinimi: full FT için 16 GB+; LoRA için 10-12 GB yeter.

**F5-TTS fine-tune:**
- Resmi gradio UI: [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS) (`infer/SHARED.md`).
- Community LoRA fork yok (resmi öncelik full FT); kişiselleştirme için "OpenF5-TTS-Base" + custom training script tartışmaları HF discussion'da.

**YourTTS:** "1 dk altı verisi olan ses ile fine-tune edilebilir, SOTA voice similarity" (resmi paper iddia + Coqui docs).

**CosyVoice 2:** Resmi repo'da SFT script; Apache 2.0 → ticari uygun.

---

### 3.3 Full Speaker Fine-Tune Ne Zaman Değer?

**TL;DR:** LoRA, 5-10 dk veriyle speaker similarity tarafında full FT'nin %85-95'ine ulaşır. Full FT'nin gerçek üstünlüğü **uzun-vadeli karakter tutarlılığı, çoklu duygu/stil kapsama ve unseen-prosody'de naturalness**.

| Boyut | LoRA (rank 16-32) | Full FT |
| --- | --- | --- |
| Speaker similarity (ECAPA SECS) | 0.55-0.65 | 0.65-0.75 |
| Naturalness (UTMOS) | 3.8-4.0 | 4.0-4.2 |
| Stil/duygu çeşitliliği | Sınırlı (LoRA bias) | Geniş |
| Uzun konuşmada drift | Daha çok kayma | Daha stabil |
| Disk (per character) | 50-200 MB | 2-5 GB |
| GPU saati | 2-6 sa | 24-72 sa |
| Maliyet (cloud) | $5-30 | $80-300 |
| Multi-character switching | **Çok kolay (adapter swap)** | Her karakter için ayrı checkpoint |

**Aktif full FT script'i olan modeller:** XTTS-v2 (Coqui), CosyVoice 1/2, F5-TTS, StyleTTS 2, Tortoise (zorlu).

**Neeko stratejisi:**
- v1 launch: **LoRA**. 1 karakter (Neeko) için 5-10 dk veri yeter.
- v2 (multi-character market): **shared base + per-character LoRA**. Disk efficiency + hot-swap.
- Specific character çok satıyorsa: **full FT yan-checkpoint** (premium tier).

---

### 3.4 Karakter Tutarlılığı (Consistency) Ölçümü

**TL;DR:** ECAPA-TDNN SpeechBrain encoder + cosine similarity ölçütü endüstri standardı. SECS > 0.55 same-speaker, > 0.7 high-confidence. Drift detection için her N saniye/utterance "ref reference"a karşı ölçüm.

#### Speaker Verification Encoder Seçenekleri

| Encoder | EER (VoxCeleb1-O) | Eşik (cos sim) | Repo |
| --- | --- | --- | --- |
| **ECAPA-TDNN (SpeechBrain)** | 0.69-0.90% | ~0.25-0.30 same-speaker | [speechbrain/spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) |
| **WavLM-large-sv** | 0.95% | 0.388 (Orange/wavLM-id), 0.86 (microsoft/wavlm-base-sv) | [Orange/Speaker-wavLM-id](https://huggingface.co/Orange/Speaker-wavLM-id) |
| **Resemblyzer (d-vector)** | 5-6% (eski) | 0.65-0.75 (uygulama-bağımlı) | [resemble-ai/Resemblyzer](https://github.com/resemble-ai/Resemblyzer) |
| **ReDimNet** | 0.5-0.7% | dataset-bağımlı | community |
| **H/ASP (YourTTS)** | n/a (zero-shot TTS için iyi) | n/a | YourTTS repo |

**Pratik eşik önerisi (Neeko ses-doğrulama testi):**
- ECAPA-TDNN cosine similarity ≥ **0.5** → "muhtemelen aynı" (clip-level), ≥ **0.7** → "kesin aynı" (high-confidence). Studio-temiz ref ile mikser bazlı production speech karşılaştırmasında 0.6 eşiği makul.

**Drift detection (uzun konuşmada ses kayması):**
- VoxCPM2 issue tracker'ında belgelenmiş: tek-pass long-form'da 60-120 s sonra timbre kayışı tipik ([OpenBMB/VoxCPM #302](https://github.com/OpenBMB/VoxCPM/issues/302)).
- Akademik framing: "voice drift effect" — prompt conditioning aşamalı olarak self-generated latent conditioning'e doğru kayar.
- **Mitigation:** (a) utterance bazlı segmentasyon (her cümle 20-40 s); (b) her N saniyede prompt re-injection; (c) post-hoc speaker sim score eşiği altında segment regen.
- Long-form için tasarlanmış: MOSS-TTSD (60 dk tek-pass), Qwen3-TTS (10 dk) — ama bunlar henüz Türkçe değil.

**Cross-session / cross-emotion consistency ölçümü:**
- Aynı karaktere ait 100 utterance (farklı duygu, farklı bağlam) → centroid çıkar, her utterance'ın centroid'e uzaklığı → "consistency score". Production'da Neeko karakter sürümleri arası **regression test**.

---

## ALAN 8 — Multi-Character + Karakter Kimliği + IP Koruması

### 8.1 Aynı Modelde Birden Fazla Karakter Sesi

**TL;DR:** Üç ana pattern: (1) speaker embedding lookup table — basit, ucuz, ifade gücü düşük; (2) multi-LoRA adapter swap — production-friendly, vLLM-style sunum; (3) per-character full FT — premium kalite, disk pahalı.

#### Karşılaştırma

| Pattern | Disk/karakter | Switch latency | Speaker sim | Kullanım |
| --- | --- | --- | --- | --- |
| **Speaker embedding bank (lookup)** | 1-10 KB (d-vector/x-vector) | sıfır | orta | Hızlı çoklu-karakter, sınırlı stilistik kontrol |
| **Multi-LoRA adapter** | 50-200 MB | ms (vLLM in-mem) - s (disk load) | yüksek | **Neeko market scaling pattern** |
| **Per-character full FT** | 2-5 GB | tam model yükle | en yüksek | Premium / signature karakterler |

**Referanslar:**
- DeepVoice 2/3 / FastSpeech 2: learnable speaker embedding table ([arXiv 1812.05253](https://ar5iv.labs.arxiv.org/html/1812.05253)).
- vLLM Multi-LoRA: [docs.vllm.ai multilora](https://docs.vllm.ai/en/v0.9.2/examples/offline_inference/multilora_inference.html) — N adapter aynı VRAM'de, runtime activation.
- Cross-Model KV-Cache Reuse with Activated LoRA: [arXiv 2512.17910](https://arxiv.org/pdf/2512.17910) — 58× latency reduction multi-adapter pipeline.
- CaraServe (CPU-assisted LoRA): [arXiv 2401.11240](https://arxiv.org/pdf/2401.11240).

**Neeko market mimarisi önerisi:**
- v1: Tek karakter (Neeko) — LoRA tek (tek adapter).
- v2: Çocuk-içerik market açıldığında — **shared XTTS/CosyVoice base + multi-LoRA registry**. Her karakter:
  - `character_id`, `lora_path`, `speaker_embedding`, `reference_clip`, `metadata`.
- Inference: request → character_id → adapter swap (vLLM-style sunucu). Ortalama swap latency hedefi: < 200 ms.

---

### 8.2 Karakter Sesi Versiyonlama

**TL;DR:** Karakter sesi = model + reference + LoRA + config tripleti. Her release manifest + hash. Voice fingerprint regression: her CI'da N standart cümle üretip ECAPA centroid karşılaştırması.

#### Manifest Şablonu (Neeko character voice)

```yaml
character_id: neeko
voice_version: 1.2.0
base_model:
  name: xtts-v2
  hash: sha256:abc...
  license: CPML+commercial
adapter:
  type: lora
  path: voices/neeko/lora_v1.2.0.safetensors
  rank: 32
  hash: sha256:def...
training:
  data_hash: sha256:ghi...
  hours: 0.7  # 42 minutes
  date: 2026-05-15
  hardware: rtx-4090
reference_audio:
  - voices/neeko/ref_1.wav  # 12s, studio
  - voices/neeko/ref_2.wav  # 15s, studio
speaker_centroid:
  encoder: ecapa-tdnn-speechbrain
  embedding: voices/neeko/centroid_v1.2.0.pt
  consistency_threshold: 0.55  # cos sim
qa:
  regression_clips: voices/neeko/qa_v1.2.0/
  utmos: 4.05
  secs_self: 0.68
  drift_test_pass: true  # 5 min long-form
```

**Voice fingerprint (kendi sesimizle aynı mı doğrulama):**
- Production'da her N gün rastgele utterance topla → ECAPA embedding çıkar → karakter centroid'e cosine → eşik altıysa alarm. Drift erken yakalama.
- Aynı sistem: voice misuse detection (3. taraf bizim karakterimizi klonladı mı).

**Model registry seçenekleri:**
- **HuggingFace Hub private** — kolay, ücretsiz private repo, hash + version control built-in. **Neeko v1 için en pragmatik.**
- **Weights & Biases Artifacts** — dataset + model birlikte; training run lineage.
- **MLflow Model Registry** — kurumsal disiplin, self-hosted.
- **DVC + S3** — git-style + cheap storage.

**Öneri:** HF Hub private + W&B Artifacts (training lineage için). MLflow over-engineering bu aşamada.

---

### 8.3 IP Koruması — Watermarking + Anti-Cloning

**TL;DR:** Çift katmanlı: **(a) Output watermarking** (her synthesized clip'e Neeko-imzası göm — AudioSeal) + **(b) Speaker-specific watermarking** (klon edilmiş Neeko sesini bile tespit et — VoiceMark). Anti-cloning adversarial perturbation (AntiFake) defansif ama De-AntiFake çalışması zayıflığını gösterdi — yeterli değil tek başına.

#### Watermarking Yöntemleri Karşılaştırma

| Method | Algı (imperceptibility) | Robustness (cloning sonrası) | Lokalize | Repo / Paper |
| --- | --- | --- | --- | --- |
| **AudioSeal (Meta)** | Çok iyi (auditory masking loss) | İyi (post-processing'e dayanıklı) | **Sample-level (1/16000 s)** | [arXiv 2401.17264](https://arxiv.org/abs/2401.17264), [facebookresearch/audioseal](https://github.com/facebookresearch/audioseal) |
| **VoiceMark** | İyi | **%95+ doğruluk zero-shot VC sonrası** | speaker-specific latents | [arXiv 2505.21568](https://arxiv.org/abs/2505.21568) |
| **WavMark** | Orta (artefakt audible) | Orta | Audio chunks | [arXiv 2308.12770](https://arxiv.org/abs/2308.12770) |
| **SilentCipher** | **Çok iyi (artefakt imperceptible)** | Orta | chunk-level | [arXiv 2406.03822](https://arxiv.org/html/2406.03822v2) |
| **Timbre Watermarking** | Orta | İyi (clone tespiti odaklı) | speaker timbre | [arXiv 2312.03410](https://arxiv.org/pdf/2312.03410) |
| **WaveVerify** | Yeni (2025) | Media authentication odaklı | clip-level | [arXiv 2507.21150](https://arxiv.org/html/2507.21150) |
| **AudioMarkNet** | Orta | Deepfake speech detection odaklı | clip-level | dl.acm.org/10.5555/3766078.3766318 |

**Anti-cloning adversarial defenses:**
- **AntiFake** — referans audio'ya imperceptible perturbation ekleyerek voice cloning'i sabote etme. Ama De-AntiFake çalışması ([arXiv 2507.02606](https://arxiv.org/abs/2507.02606), ICML 2025) bu perturbation'ların purification ile %45→%76 attack success rate ile aşılabildiğini gösterdi.
- **An Imperceptible Adversarial Watermarking** (Interspeech 2025 SPSC): [isca archive](https://www.isca-archive.org/spsc_2025/park25_spsc.pdf).
- **Mevcut durum (2026):** Adversarial perturbation tek başına yetersiz; **watermarking + legal + abuse-monitoring** üç katman.

**Deepfake detection (Neeko sesini başkası klonladı mı?):**
- Output watermarking decoder (AudioSeal detector → 1-2 ms inference; speaker watermarking decoder → VoiceMark).
- Speaker centroid drift detection (kendi karakter centroid'ine karşı 3. taraf clip cosine sim).
- Endüstri pratik: brand monitoring SaaS (Pindrop, Resemble Detect) — kapalı kaynak ama API.

**Neeko önerisi (defansif IP stack):**
1. Her output WAV'da **AudioSeal** (kimliksiz, sadece "AI-generated by Neeko") gömülü.
2. Her karakter LoRA training'de **VoiceMark-style speaker-specific** watermark referans verisine ekli → klon edilmiş Neeko sesini bile %95+ tespit.
3. Production'da web monitoring (3. taraf çocuk-içerik platformlarında klon takibi).

---

### 8.4 Türkiye Hukuk Çerçevesi (Ses-Spesifik)

**TL;DR:** Üç katman: **KVKK** (ses biyometrik veri midir? — kullanım amacına göre, kimlik doğrulama varsa özel nitelikli m.6; Neeko'da konuşmacı kimliğine bağlı eğitim verisi → açık rıza + amaç sınırı zorunlu). **FSEK 5846** (icracı sanatçı bağlantılı hakları — m.80, ahlaki haklar devredilemez, mali haklar lisans-edilebilir). **TMK m.24 + TBK m.49** (kişilik hakkı ihlali — ses kişilik hakkı kapsamında; devredilemez/feragat edilemez ama kullanım rızası verilebilir).

#### KVKK — Ses Biyometrik Veri Boyutu

- **Tanım:** KVKK m.3 — kişisel veri "kimliği belirli veya belirlenebilir gerçek kişiye ilişkin her türlü bilgi". Ses dahil.
- **Özel nitelikli — m.6:** Biyometrik veri (kimlik doğrulama amaçlı ses dahil) **özel nitelikli**. Açık rıza olmaksızın işlenemez.
- **Neeko bağlamı:** Voice talent'ın sesini eğitim verisi olarak kullanmak — kişisel veri işleme. Sentetik karakter ses üretimi için modelin "speaker identity" embedding'i çıkarması KVKK m.6 kapsamında biyometrik veri işleme sayılabilir (otorite görüşü gelişmekte). **Konservatif yaklaşım: açık rıza alın, amaç sınırla, saklama süresi belirle.**
- **2025 Üretken AI rehberi:** KVKK 24 Kasım 2025 tarihli "Üretken Yapay Zekâ ve Kişisel Verilerin Korunması Rehberi" — eğitim verisi işleme için açık rıza + veri minimizasyonu + amaç sınırı + saklama süresi açıkça düzenlenmiş.
- **Kaynak:** [Biyometrik Verilerin İşlenmesinde Dikkat Edilmesi Gereken Hususlara İlişkin Rehber](https://www.kvkk.gov.tr/Icerik/7047/Biyometrik-Verilerin-Islenmesinde-Dikkat-Edilmesi-Gereken-Hususlara-Iliskin-Rehber), [KVKK m.6 değişiklikleri (Mart 2024)](https://kvkk.gov.tr/SharedFolderServer/CMSFiles/4eba766b-7425-4cd4-97f5-cb09c4cf4ef9.pdf).

#### FSEK 5846 — Bağlantılı Hak / İcracı Sanatçı

- **m.80:** İcracı sanatçı eserin yorumcusu. Voice talent (Neeko karakteri seslendiren) icracı sanatçı sıfatını alır.
- **Bağlantılı haklar:** Mali haklar (çoğaltma, yayma, temsil, umuma iletim) — **lisans veya devir mümkün**.
- **Manevi haklar (m.80/A):** İcracı olarak tanıtılma, performansın haysiyetine zarar verecek tahrifata karşı koruma — **devredilemez, vefattan sonra da korunur**.
- **Neeko bağlamı:** Voice talent kontratında "synthetic voice / digital replica" özelinde mali hak devri/lisansı + manevi hakka saygı maddesi (sentetik üretimin onurunu zedelememesi).
- **Kaynak:** [FSEK 5846 tam metin](https://mevzuat.gov.tr/mevzuatmetin/1.3.5846.pdf), [İcracı Sanatçılar ve Hakları (Özdoğru Hukuk)](https://www.ozdogruhukuk.com/yayinlar/icraci-sanatcilar-ve-icraci-sanatcilarin-haklari.html).

#### Türk Medeni Kanunu m.24 + Türk Borçlar Kanunu m.49

- **TMK m.24:** Kişilik hakkı saldırıya uğrayan kişi hâkimden korunma isteme hakkına sahip. Ses kişilik hakkı kapsamındadır (kişinin "ses, görüntü, isim, şeref" gibi değerleri).
- **TBK m.49:** Hukuka aykırı eylem sonucu zarar görenin tazminat talep hakkı. Kişilik hakkı ihlalinde manevi tazminat tipik.
- **"Geri çekme hakkı" senaryosu:** Türk hukukunda kişilik hakkı **devredilemez**; ama **kullanım rızası** geri alınabilir. Voice talent diyebilir: "Neeko karakterimi artık kullanmasın." Bu durumda Neeko'nun pozisyonu:
  - (a) Sözleşmede explicit **gayri-kabili rücu lisans** maddesi (yorum açısından zayıf — kişilik hakkı geri alınabilir olduğu için tam etki tartışmalı, Yargıtay kararları bu yönde sınırlayıcı yorum yapabilir).
  - (b) Buyout + hak devri olarak değil, **eser yorumlama / icra** olarak konumlandır → FSEK m.80 mali haklarının devri (geçerli).
  - (c) Pratikte: voice talent ile **uzun süreli royalty + iyi ilişki**, "geri çekme" durumunda **karakter sesini ikinci-versiyon-talent ile geçiş süreci** plan.
- **Kaynak:** [TMK m.24 şerh](https://www.ilhanhelvacidersleri.com/turk-medeni-kanunu/turk-medeni-kanunu-madde-24), [Kişilik Haklarının İhlalinden Doğan Sorumluluk](https://www.hukukihaber.net/kisilik-haklarinin-ihlalinden-dogan-sorumluluk), [TBK 6098 mevzuat](https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=6098&MevzuatTur=1&MevzuatTertip=5).

#### Yargıtay / Doktrin Eğilimi

- Yargıtay Hukuk Genel Kurulu (HGK) ve 4. Hukuk Dairesi içtihatları kişilik hakkı ihlalinde tazminat (TBK m.58, m.49) yönünde istikrarlı. Ses-spesifik karar literatürü dar; ama "isim, görüntü, ses" üçlüsü kişilik hakkı kapsamında doktrinel kabul (Ünsal, Oğuzman, Sungurbey gibi medeni hukuk klasiklerinde).
- Doktrin atıfı: [TBB Kitapları — Borçlar Kanunu](https://medya.barobirlik.org.tr/tbbkitaplari/TBBBooks/tbkanunu.pdf).

---

### 8.5 Endüstri Standardı Voice Talent Kontratları

**TL;DR:** SAG-AFTRA + Replica Studios çerçevesi (2024 CES) endüstri yön gösterici idi ama **Replica Studios Eylül 2025'te faaliyetlerini sonlandırdı**. ElevenLabs ToS'u en olgun ticari pratik (paid plan = output commercial rights; sesin kendisi için ayrı consent). Endüstride iki ana model: **buyout (tek seferlik, geniş kapsam)** vs **royalty (kullanım başı, daha pahalı)**.

#### Karşılaştırma

| Şirket | Model | Talent commercial right | AI training right | Public ToS |
| --- | --- | --- | --- | --- |
| **ElevenLabs** | Tier-based | Paid plan → kullanıcı output sahibi | ElevenLabs **perpetual, irrevocable license to train** | [elevenlabs.io/terms-of-use](https://elevenlabs.io/terms-of-use) |
| **Replica Studios** (kapandı 2025-09) | SAG-AFTRA min rates | Per-project consent + new project re-consent | NDA + transparency + data security | [Replica FAQ archive](https://www.sagaftra.org/sites/default/files/2025-09/Replica%20Studios%20Agreement%20FAQs.pdf) |
| **Resemble AI** | Buyout veya enterprise | Enterprise license per use case | Custom; voice cloning consent verification | resemble.ai/terms |
| **Murf / WellSaid Labs** | Royalty (talent pool) | Per usage | Internal training right | murf.ai, wellsaidlabs.com |
| **Narrativ** (SAG-AFTRA dealli) | Audio ad licensing | Talent royalty | Restricted | [SAG-AFTRA Narrativ](https://www.sagaftra.org/sag-aftra-and-narrativ-announce-new-agreement) |

#### SAG-AFTRA AI Voice Agreement (2023+ çerçeve)

- **Kasım 2023 TV/Theatrical/Streaming strike çözümü:** "Digital replica" maddeleri. Stüdyolar bir performansçının dijital kopyasını **açık + bilgilendirilmiş onay olmadan** yapamaz / kullanamaz.
- **Ocak 2024 Replica Studios deal (CES):** Min rates + per-project consent + use case transparency + NDA limits + data security. (Eylül 2025'te sonlandı; çerçeve hâlâ referans.)
- **Mart 2024 Animation Agreement:** İlk SAG-AFTRA voiceover sözleşmesi AI koruma maddesi ile.
- **Ağustos 2024 Narrativ deal:** Audio reklam için lisans.
- **Ethovox deal (2025):** Performer-empowering AI guardrails.
- Kaynak: [SAG-AFTRA AI Bargaining Timeline](https://www.sagaftra.org/contracts-industry-resources/member-resources/artificial-intelligence/sag-aftra-ai-bargaining-and).

#### "Karakter Sesi" Özelinde Tipik Kontrat Maddeleri

| Madde | Tipik formülasyon | Neeko önerisi |
| --- | --- | --- |
| **Use case scope** | Belirli ürün / oyun / animasyon | "Neeko karakteri (oyuncak + companion app + market + tanıtım), child-safe context" |
| **Geographic scope** | Worldwide vs region | Worldwide (Türkiye birincil, EU/US ikinci faz) |
| **Duration** | Süresiz vs N yıl | **5 yıl + 5 yıl opsiyonel renewal** (talent ile ilişki açık) |
| **AI training right** | Açık + amaç sınırlı | "Neeko karakter LoRA eğitim + iyileştirme + versiyon güncellemesi"; **3. taraf model ticareti yasak** |
| **Derivative right** | Karakter sesinden türetilmiş seslerin sahipliği | Neeko'da; ama "talent'a benzer ses" türetme yasak |
| **Synthetic voice license** | Münhasır vs gayri-münhasır | **Münhasır (exclusive) Neeko karakteri için**; talent başka projelerde kendi gerçek sesini kullanmaya devam edebilir |
| **Talent attribution** | İsim kullanımı / gizlilik | NDA + isim gizli (karakter > talent), istisna talent talep ederse |
| **Buyout vs royalty** | Tek ödeme vs kullanım başı | **Karma: signing buyout + yıllık residual** (Neeko karakter sesinin uzun ömrü için fair) |
| **Revocation / withdraw** | Talent geri çekme şartı | KVKK + TMK m.24 zorunlu (rıza geri alınabilir); geçiş süreci 12 ay |
| **Misuse takedown** | Sesin kötüye kullanımı (deepfake) | Neeko izlemekle yükümlü, talent'a bildirim |
| **Likeness / personality rights** | Talent'ın ses kimliği koruması | FSEK m.80/A manevi hak saklı |

---

## 9) Neeko İçin Pratik Karar Önerileri

### v1 (önümüzdeki 6 ay, 150 ürün, 50K USD bütçe)

1. **Base model:** XTTS-v2 (ticari lisans ekosistemi en olgun + Türkçe destek + community fork zenginliği).
2. **Karakter ses:** Voice talent ile 1-2 saat studio kayıt → **5-10 dk seçilmiş data → LoRA rank 16, alpha 32, attention target, 2000-3000 step, RTX 4090'da ~3 sa**.
3. **Reference clips:** 3 farklı tonda (sakin, meraklı, oyuncu) 10-15 s ref clip kütüphanesi.
4. **Drift mitigation:** Utterance bazlı segmentasyon (her cümle max 25 s); her cümle inference'ında ref re-injection.
5. **Watermarking:** AudioSeal her output WAV'a (zorunlu); v1.5'te VoiceMark çalışması başlat.
6. **Consistency QA:** Her release'de 50 cümle regression suite + ECAPA-TDNN centroid + cosine ≥ 0.6 eşiği CI pass.
7. **Hukuki çerçeve:** Voice talent kontratı buyout (~bütçe içi) + 5 yıl münhasır lisans + KVKK açık rıza + FSEK m.80 mali hak devri + TMK m.24 saygı maddesi.

### v2 (multi-character / market / 12-18 ay sonra)

1. **Base model:** CosyVoice 2 streaming (Apache 2.0, daha modern) veya XTTS devam.
2. **Multi-LoRA registry:** vLLM-style multi-adapter sunucu. Her karakter ayrı LoRA, hot-swap.
3. **Voice fingerprint izleme:** 3. taraf platform monitoring (3. taraf Neeko klonu var mı?).
4. **VoiceMark zorunlu:** Speaker-specific watermark her karakter ref verisinde.
5. **Voice talent pool:** Çoklu karakter çoklu talent — her birinin kontratı standardize.

---

## 10) Açık Kalan Sorular ve İleri Çalışma

1. **Türkçe ECAPA-TDNN benchmark:** Türkçe için EER ölçümü yapılmış mı? Public test set var mı?
2. **Türkçe XTTS-v2 kalite:** Resmi 16-dil destek tablosunda Türkçe nerede? Community Türkçe fine-tune varlığı.
3. **KVKK m.6 sentetik ses yorumu:** KVKK 2025 üretken AI rehberinde sentetik ses üretimi için spesifik içtihat / pratik yorum var mı?
4. **FSEK m.80 vs sentetik ses Yargıtay yorumu:** Doktrin "icracı sanatçı + sentetik klon" senaryosu için henüz uzlaşmadı — hukuki danışman görüşü alınmalı.
5. **Drift mitigation Türkçe-specific:** Türkçe ünlü uyumu + uzun cümle yapısı drift'i agrave eder mi? Ampirik test gerek.
6. **Çocuk sesi vs yetişkin sesi karakter:** Neeko 3-7 yaş için yetişkin "warm" tone vs çocuk-doğal tone karşılaştırması — pedagoji + KVKK + ürün UX açısından.

---

## Kaynak Listesi (Toplu)

**Voice cloning models:**
- XTTS: [arXiv 2406.04904](https://arxiv.org/html/2406.04904v1), [HF coqui/XTTS-v2](https://huggingface.co/coqui/XTTS-v2)
- F5-TTS: [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS), [Cross-Lingual F5-TTS arXiv 2509.14579](https://arxiv.org/html/2509.14579v1)
- OpenVoice: [arXiv 2312.01479](https://arxiv.org/abs/2312.01479)
- VALL-E 2: [arXiv 2406.05370](https://arxiv.org/pdf/2406.05370)
- VALL-E X: [arXiv 2303.03926](https://arxiv.org/pdf/2303.03926)
- CosyVoice 2: [arXiv 2412.10117](https://arxiv.org/html/2412.10117v3)
- StyleTTS 2: [yl4579/StyleTTS2](https://github.com/yl4579/StyleTTS2), [PMC11759097](https://pmc.ncbi.nlm.nih.gov/articles/PMC11759097/)
- Spark-TTS: [arXiv 2503.01710](https://arxiv.org/html/2503.01710v1)
- IndexTTS: [arXiv 2502.05512](https://arxiv.org/html/2502.05512v1)
- MaskGCT: [openreview ICLR 2025](https://openreview.net/pdf?id=ExuBFYtCQU)
- YourTTS: [arXiv 2112.02418](https://arxiv.org/pdf/2112.02418), [Edresson/YourTTS](https://github.com/Edresson/YourTTS)
- Tortoise: [docs.coqui.ai/tortoise](https://docs.coqui.ai/en/latest/models/tortoise.html)

**LoRA / Adaptation:**
- LoRP-TTS: [arXiv 2502.07562](https://arxiv.org/html/2502.07562v1)
- VoiceTailor: [arXiv 2408.14739](https://arxiv.org/pdf/2408.14739)
- HF PEFT: [peft repo](https://github.com/huggingface/peft), [LoRA docs](https://huggingface.co/docs/peft/en/package_reference/lora)
- XTTS finetune: [erew123 guide](https://github.com/erew123/alltalk_tts/wiki/XTTS-Model-Finetuning-Guide-(Advanced-Version)), [gokhaneraslan/XTTS_V2-finetuning](https://github.com/gokhaneraslan/XTTS_V2-finetuning)

**Speaker verification:**
- ECAPA-TDNN: [speechbrain/spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb), [arXiv 2506.20190](https://arxiv.org/abs/2506.20190)
- WavLM-SV: [Orange/Speaker-wavLM-id](https://huggingface.co/Orange/Speaker-wavLM-id), [microsoft/wavlm-base-sv](https://huggingface.co/microsoft/wavlm-base-sv)
- Resemblyzer: [resemble-ai/Resemblyzer](https://github.com/resemble-ai/Resemblyzer)

**Multi-LoRA:**
- vLLM multi-LoRA: [docs.vllm.ai](https://docs.vllm.ai/en/v0.9.2/examples/offline_inference/multilora_inference.html)
- Activated LoRA: [arXiv 2512.17910](https://arxiv.org/pdf/2512.17910)
- CaraServe: [arXiv 2401.11240](https://arxiv.org/pdf/2401.11240)

**Watermarking + Anti-cloning:**
- AudioSeal: [arXiv 2401.17264](https://arxiv.org/abs/2401.17264), [facebookresearch/audioseal](https://github.com/facebookresearch/audioseal)
- VoiceMark: [arXiv 2505.21568](https://arxiv.org/abs/2505.21568)
- SilentCipher: [arXiv 2406.03822](https://arxiv.org/html/2406.03822v2)
- WavMark: arXiv 2308.12770
- Timbre Watermarking: [arXiv 2312.03410](https://arxiv.org/pdf/2312.03410)
- WaveVerify: [arXiv 2507.21150](https://arxiv.org/html/2507.21150)
- De-AntiFake: [arXiv 2507.02606](https://arxiv.org/abs/2507.02606)
- Audio Watermarking Deepfake Survey USENIX 25: [usenix.org/zong](https://www.usenix.org/system/files/usenixsecurity25-zong.pdf)

**Türkiye hukuk:**
- FSEK 5846: [mevzuat.gov.tr](https://mevzuat.gov.tr/mevzuatmetin/1.3.5846.pdf), [LEXPERA](https://www.lexpera.com.tr/mevzuat/kanunlar/fikir-ve-sanat-eserleri-kanunu-5846)
- TMK m.24: [ilhanhelvacidersleri.com](https://www.ilhanhelvacidersleri.com/turk-medeni-kanunu/turk-medeni-kanunu-madde-24)
- TBK m.49: [mevzuat.gov.tr 6098](https://www.mevzuat.gov.tr/mevzuat?MevzuatNo=6098&MevzuatTur=1&MevzuatTertip=5)
- KVKK biyometrik veri rehberi: [kvkk.gov.tr/7047](https://www.kvkk.gov.tr/Icerik/7047/Biyometrik-Verilerin-Islenmesinde-Dikkat-Edilmesi-Gereken-Hususlara-Iliskin-Rehber)
- KVKK 2024 değişiklikler: [kvkk.gov.tr CMSFiles](https://kvkk.gov.tr/SharedFolderServer/CMSFiles/4eba766b-7425-4cd4-97f5-cb09c4cf4ef9.pdf)
- Cottgroup biyometrik veri analizi: [cottgroup.com KVKK GDPR](https://www.cottgroup.com/tr/blog/kvkk-gdpr/item/biyometrik-verilerin-kvkk-ve-gdpr-bakimindan-islenmesi)
- İcracı sanatçı hakları: [ozdogruhukuk.com](https://www.ozdogruhukuk.com/yayinlar/icraci-sanatcilar-ve-icraci-sanatcilarin-haklari.html)

**Endüstri kontratları:**
- ElevenLabs ToS: [elevenlabs.io/terms-of-use](https://elevenlabs.io/terms-of-use), [terms.law ElevenLabs analizi](https://terms.law/ai-output-rights/elevenlabs/)
- SAG-AFTRA AI: [sagaftra.org AI bargaining](https://www.sagaftra.org/contracts-industry-resources/member-resources/artificial-intelligence/sag-aftra-ai-bargaining-and), [Replica Studios deal](https://www.sagaftra.org/sag-aftra-and-replica-studios-introduce-groundbreaking-ai-voice-agreement-ces), [Narrativ deal](https://www.sagaftra.org/sag-aftra-and-narrativ-announce-new-agreement)

---

*Atlas — Alan 3 + 8 raporu sonu. Sonraki adımlar: 01a (TTS mimari) + 01b (ses veri pipeline) ile çapraz okuma; karar oturumu için 6 ay roadmap.*
