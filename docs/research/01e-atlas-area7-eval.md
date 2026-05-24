# Atlas — Alan 7: TTS Değerlendirme Metrikleri ve A/B Test Protokolü

**Bağlam:** Neeko (3-7 yaş Türk çocuk-yönelimli AI oyuncak, Tonies modeli) için TTS araştırmasının beşinci ayağı. Iterasyon eval olmadan kör; bu katman doğru kurulmadan model seçimi, fine-tune kararı, LoRA değerlendirmesi, prod regression detection — hepsi tahmin yürütmeye dönüşür.

**Tarih:** 2026-05-19 | **Hazırlayan:** Atlas | **Hedef:** Neeko v1 baseline eval suite + CI/CD eval pipeline kararı

---

## TL;DR — Karar-üretici özet

Neeko ölçeğinde (solo founder, 6 ay, 50K USD bütçe, TR-spesifik, çocuk hedef kitle) tek bir altın metrik yok. **Beş katmanlı eval stack** öneriyorum:

1. **Objektif naturalness (otomatik, her commit'te):** UTMOSv2 + NISQA + DNSMOS — üçü ensemble, regression detection için. Türkçe için zayıf transfer ama mutlak skor değil **delta** kullanıyoruz.
2. **Intelligibility (otomatik, her batch'te):** Whisper-large-v3 + selimc/whisper-large-v3-turbo-turkish ile WER/CER. Türkçe için kalibre edilmiş, çocuk-yönelimli vocabulary üzerinde fix prompt seti.
3. **Speaker similarity (otomatik, karakter tutarlılığı için):** WavLM-base-plus-sv + ECAPA-TDNN cosine — Lina/Tom karakterleri için drift detection.
4. **Subjektif baseline (haftalık, küçük panel):** 8-12 Türk ana dili konuşur jüri, MUSHRA-lite Gradio UI, ITU-T P.808 protokolü. webMUSHRA değil çünkü mobil-friendly değil; Gradio + Hugging Face Spaces.
5. **Çocuk-aile sahası (aylık, milestone gate'te):** 5-8 aile, ev içi 30 dakika oturum, dikkat süresi + tekrar oynama isteği + ebeveyn anketi. Lab kurulumu yok — sahada.

**Pipeline:** WandB run tracker + custom Python eval script (espnet/utils benzeri) + Hugging Face Spaces leaderboard (TTS-Arena tarzı iç paneller için Bradley-Terry).

**Toplam tahmini maliyet:** ~800 USD/ay (Prolific TR yok, Tipograf/yerel network + WandB Team + GPU eval batch).

---

## Bölüm 1 — Objektif TTS Eval Metrikleri (Reference-less ve Referenced)

### TL;DR
Modern TTS eval'ında referenceless SSL-temelli metrikler (UTMOS, NISQA, DNSMOS, SQUIM) altın standart; eski referenced metrikler (PESQ, STOI, MCD) telefon kalitesi senaryoları için tasarlanmış ve human-quality TTS için **negatif korelasyon** dahi gösterebiliyor (oversmoothing bias).

### 1.1 NISQA (Non-Intrusive Speech Quality Assessment)

- **Repo:** github.com/gabrielmittag/NISQA
- **Paper:** Mittag et al. 2021, arxiv.org/abs/2104.09494
- **Mimari:** CNN framewise + Self-Attention time-dependency + Attention-Pooling
- **Çıktı:** Overall MOS (1-5) + 4 boyut: Noisiness, Coloration, Discontinuity, Loudness
- **Training:** 81 dataset, 97K+ insan oyu, 13K+ speech file
- **Versiyonlar:** v1.0 (eski), NISQA TTS modu (sentetik konuşma için ek model), NISQA-S (streaming için yıllık update'ler)
- **Türkçe transferi:** Cross-lingual çalışma (arxiv.org/html/2502.13004, 2025) gösteriyor: İngilizce'de eğitilmiş NISQA, Mandarin için noise dimension'da r=0.927, Fransızca loudness için r=0.511. Türkçe spesifik benchmark yok ama Hint-Avrupa olmadığı için Mandarin'e benzer transfer beklenir (orta-iyi). **Önerim:** Mutlak değer değil, model versiyonları arası **delta** olarak kullan.
- **Hesaplama maliyeti:** CPU'da gerçek-zamanlı, GPU'da batch 100x speedup. RAM ~2GB.

### 1.2 UTMOS / UTMOSv2

- **Repo:** github.com/sarulab-speech/UTMOS22 (v1), github.com/sarulab-speech/UTMOSv2 (v2)
- **Paper:** UTMOS — Saeki et al. 2022, arxiv.org/abs/2204.02152 (INTERSPEECH 2022)
- **Mimari:** SSL (wav2vec2 / WavLM) embeddings + listener-dependent embeddings + contrastive loss + multi-stage stacking ensemble
- **Performans:** VoiceMOS Challenge 2022 — utterance-level SRCC ≈ 0.897, system-level SRCC ≈ 0.936
- **UTMOSv2:** VoiceMOS Challenge 2024 Track 1'de 16 metrikten 7'sinde 1., kalan 9'da 2. Transfer learning from deep image classifier (arxiv.org/html/2409.09305v1)
- **Türkçe transferi:** UTMOS English + Chinese ile eğitilmiş. Partial Rank Similarity (arxiv 2310.05078) gösteriyor: unseen languages için UTMOS'tan **daha iyi** alternatifler var ama UTMOS hâlâ kullanılabilir baseline. **Önerim:** Türkçe için absolute MOS değil **comparative ranking** olarak kullan.
- **Hesaplama maliyeti:** GPU önerilir, batch processing 10ms/clip.

### 1.3 DNSMOS P.835

- **Repo:** Microsoft Azure DNS Challenge ile dağıtılıyor; ONNX modeli github.com/microsoft/DNS-Challenge altında
- **Paper:** Reddy et al. 2022, arxiv.org/abs/2110.01763 (ICASSP 2022)
- **Çıktı:** 3 boyut — SIG (signal), BAK (background), OVRL (overall)
- **Performans:** PCC 0.94 (SIG), 0.98 (BAK), 0.98 (OVRL) — ITU-T P.835 protokolüne göre
- **Tasarım amacı:** Noise suppressor değerlendirmesi (TTS değil!). Ama clean TTS output'unda da **SIG** ve **OVRL** boyutları bilgilendirici; özellikle vocoder artifact tespiti için.
- **Türkçe transferi:** Modeller ITU-T P.808 crowdsource İngilizce verilerle eğitildi. Sinyal-temelli olduğu için dile-bağımsız robust; **noise/distortion için iyi seçim**, ama naturalness için yetersiz.

### 1.4 SQUIM (TorchAudio)

- **Repo:** TorchAudio (pytorch.org/audio) — `torchaudio.pipelines.SQUIM_SUBJECTIVE`, `SQUIM_OBJECTIVE`
- **Paper:** Kumar et al. ICASSP 2023, "Torchaudio-Squim: Reference-less Speech Quality and Intelligibility Measures"
- **Çıktı:** Objective head — PESQ, STOI, SI-SDR (reference-free!); Subjective head — MOS (NORESQA-MOS based, non-matching reference)
- **Avantaj:** TorchAudio native, kurulumu kolay (`pip install torchaudio`). Production pipeline'a en kolay entegre olan.
- **Türkçe transferi:** DNS Challenge + DAPS + LibriTTS ile eğitilmiş, bağımsız Türkçe değerlendirme yok. Sinyal-temelli kısımlar dil-bağımsız.

### 1.5 PESQ, STOI, MCD (eski metrikler)

- **PESQ:** ITU-T P.862, reference gerekli, telefon kalitesi için tasarlanmış (8/16 kHz). Modern human-quality TTS için **negatif korelasyon** dahi raporlanmış (TTSDS2 paper, arxiv 2506.19441).
- **STOI:** Short-Time Objective Intelligibility — gürültü altında intelligibility için, TTS için yetersiz.
- **MCD:** Mel-Cepstral Distortion, reference + DTW alignment gerekli. Oversmoothing bias: "ortalama" çıktı veren modeller MCD'de iyi ama insan kulağında kötü.
- **Karar:** Neeko için **kullanma**. Tek istisna — vocoder ablation çalışmasında relative comparison için MCD yararlı olabilir.

### 1.6 Whisper-WER (Türkçe Intelligibility)

- **Model:** openai/whisper-large-v3 + selimc/whisper-large-v3-turbo-turkish (HF fine-tune)
- **Türkçe performans:**
  - whisper-large-v3 Common Voice TR WER: ~14% (raw)
  - Fine-tuned whisper-large-v3-turbo-turkish (Common Voice 17 TR): WER **%18.92** (Selim Çolak HF model card)
  - LoRA adaptation (Akar et al., MDPI 2024, mdpi.com/2079-9292/13/21/4227): TR datasets üzerinde 5 farklı dataset'te %52'ye varan WER iyileştirmesi, range 4.3%-14.2%
- **Kullanım:** TTS çıktısını Whisper'a ver, hedef transcript ile WER/CER hesapla. **Intelligibility** ölçer (naturalness değil).
- **Distil-Whisper TR:** Sercan/distil-whisper-large-v3-tr — 5.8x hızlı, WER ~%1 dahilinde. CI pipeline için ideal.
- **Karar:** Neeko prod için **selimc/whisper-large-v3-turbo-turkish** kullan (TR-spesifik fine-tune). CI'da distil versiyonu (hız için).

### 1.7 Metrik Karşılaştırma Tablosu

| Metrik | Ne ölçer | TR transferi | Ref. gerekir? | Hesaplama | Kaynak |
|---|---|---|---|---|---|
| NISQA | Multi-dim naturalness (1-5) | Orta (CNN cross-lingual r≈0.5-0.9) | Hayır | CPU OK, GPU 100x | arxiv 2104.09494 |
| UTMOS | Predicted MOS naturalness | Orta-zayıf (EN+ZH train) | Hayır | GPU önerilir | arxiv 2204.02152 |
| UTMOSv2 | Predicted MOS (image-classifier transfer) | Orta (VoiceMOS 2024 winner) | Hayır | GPU | arxiv 2409.09305 |
| DNSMOS P.835 | SIG/BAK/OVRL (noise + signal) | İyi (dil-bağımsız sinyal) | Hayır | CPU OK | arxiv 2110.01763 |
| SQUIM-MOS | Subjective MOS (non-matching ref) | Orta | Hayır (non-matching) | GPU | ICASSP 2023 |
| SQUIM-Objective | PESQ/STOI/SI-SDR reference-free | İyi (sinyal) | Hayır | GPU | ICASSP 2023 |
| PESQ | Telefon kalitesi (ITU-T P.862) | İyi (dil-bağımsız) | Evet | CPU | ITU-T |
| STOI | Intelligibility (gürültü altında) | İyi | Evet | CPU | — |
| MCD | Spectral distortion | İyi | Evet + alignment | CPU | — |
| Whisper-WER | TR intelligibility (transcription) | Mükemmel (TR fine-tune) | Hedef metin gerekli | GPU | OpenAI / HF |
| TTSDS2 | Multi-distribution composite | İyi (14 dil destekli) | Optional | GPU | arxiv 2506.19441 |

### 1.8 TTSDS2 — Yeni Standart

**Bonus bulgu:** TTSDS2 (Minixhofer et al., SSW 2025, arxiv.org/abs/2506.19441) — 16 modern metrik içinde **tek metrik** ki her domain (CLEAN/NOISY/WILD/KIDS) ve her subjective skor (MOS/CMOS/SMOS) için Spearman > 0.50. **14 dil** destekli, **KIDS** domain var. HuggingFace huggingface.co/ttsds altında released.

**Karar:** Neeko'da **TTSDS2'yi birincil composite metrik** olarak benimse. Alt-metrikler (NISQA, UTMOS, DNSMOS) regression detection için, TTSDS2 milestone gate'te.

### 1.9 Öneri — Objektif Eval Stack (Neeko v1)

```
HER COMMIT (otomatik, ~30 saniye/sample, GPU):
  - UTMOSv2        → naturalness baseline
  - NISQA (TTS)    → multi-dimensional (noise/coloration/discontinuity)
  - Whisper-TR-WER → intelligibility (50 cümlelik fix prompt seti)
  - SECS (WavLM)   → speaker consistency (referans karakter klibe karşı)

HER MILESTONE (haftalık batch, ~5 dakika/sample):
  - TTSDS2 (KIDS domain) → composite
  - DNSMOS P.835         → vocoder artifact tespiti
  - SQUIM-Objective      → PESQ/STOI reference-free
```

**Kaynak:** Mittag, G., Möller, S. (2021). NISQA. arxiv.org/abs/2104.09494 | Saeki, T. et al. (2022). UTMOS. arxiv.org/abs/2204.02152 | Reddy, C. et al. (2022). DNSMOS P.835. arxiv.org/abs/2110.01763 | Kumar, A. et al. (2023). TorchAudio-Squim. docs.pytorch.org/audio/main/tutorials/squim_tutorial.html | Minixhofer, C. et al. (2025). TTSDS2. arxiv.org/abs/2506.19441

---

## Bölüm 2 — Speaker Similarity / Karakter Tutarlılığı

### TL;DR
Neeko karakterleri (Lina, Tom) batch'ten batch'e drift etmemeli. Cosine similarity üç model ile triangulate; same-speaker eşiği literatürde 0.7-0.85 arası, biz **0.80** kullanalım.

### 2.1 ECAPA-TDNN (SpeechBrain)

- **Model:** speechbrain/spkrec-ecapa-voxceleb (HF)
- **Paper:** Desplanques et al. INTERSPEECH 2020, arxiv.org/abs/2005.07143
- **Performans:** VoxCeleb1-O EER **%0.69** (cleaned trial), %0.90 standard
- **Embedding:** 192-d, L2-normalized
- **Eşik:** SpeechBrain docs threshold önerisi ~0.25 (cosine **distance**, yani similarity ~0.75); literatürde same-speaker için ≥0.80 cosine similarity (Guennec, SSW 2023, isca-archive.org/ssw_2023/guennec23_ssw.pdf)
- **Türkçe bias:** VoxCeleb İngilizce-ağırlıklı ama embedding speaker identity'yi öğrendiği için dil-agnostic. TR konuşmacılarda EER artışı raporlanmamış ama beklemek mantıklı (~%1-2 yukarı).

### 2.2 WavLM-base-plus-sv

- **Model:** microsoft/wavlm-base-plus-sv (HF)
- **Pretrain:** 94K saat (Libri-Light 60K + GigaSpeech 10K + VoxPopuli 24K)
- **Fine-tune:** VoxCeleb1, X-Vector head, Additive Margin Softmax
- **Embedding:** 512-d, normalized
- **Input:** 16 kHz mono
- **Avantaj:** Pretrain'i daha büyük, daha iyi generalize ediyor — TR için en güvenli seçim
- **Kullanım:** `WavLMForXVector` + `Wav2Vec2FeatureExtractor` via `transformers`

### 2.3 ReDimNet, ERes2NetV2 — 2024 SOTA

- **ERes2NetV2:** Chen et al. 2024, arxiv.org/abs/2406.02167. VoxCeleb1-O EER **%0.61** (full), **%0.98** (3s), **%1.48** (2s). Kısa-süre verification için en iyi.
- **ReDimNet:** IDRnD, INTERSPEECH 2024, github.com/IDRnD/redimnet. 2D↔1D reshape, daha verimli.
- **3D-Speaker toolkit:** github.com/modelscope/3D-Speaker — multi-modal speaker verification, dialect+language+speaker labels.
- **Karar:** Neeko v1 için **WavLM-base-plus-sv (primary) + ECAPA-TDNN (secondary)**. ERes2NetV2 ileride 2-3 saniyelik kısa karakter klipleri için.

### 2.4 Resemblyzer

- **Repo:** github.com/resemble-ai/Resemblyzer
- **Hız:** ~1000x real-time GTX 1080, CPU çalışır
- **Embedding:** 256-d, GE2E loss-based
- **Avantaj:** Tek satır API, hızlı prototip
- **Dezavantaj:** Eski (2018), modern SSL temelli modeller daha iyi
- **Kullanım:** Quick-and-dirty baseline; production'da WavLM tercih edilir

### 2.5 SECS (Speaker Encoder Cosine Similarity) — Eval Protokolü

- **Tanım:** Synthesized speech embedding ↔ target/reference speech embedding cosine similarity
- **Eşikler (literatür):**
  - ≥0.90: very high similarity (zero-shot TTS state-of-the-art)
  - ≥0.80: "same speaker" perception eşiği (Zhu, RUG thesis 2023, campus-fryslan.studenttheses.ub.rug.nl/708)
  - 0.70-0.80: aynı konuşmacı olabilir ama emin değil
  - <0.70: farklı konuşmacı
- **Neeko karakteri için:** Lina/Tom her batch için ≥0.85 SECS hedefi (karakter tutarlılığı; drift varsa alarm)

### 2.6 Cross-session, cross-emotion consistency

- **Protokol:** Aynı karakterin 50 farklı cümlede (5 farklı duygu × 10 cümle) embedding'i çıkar; pairwise cosine matrix'in **standart sapması** karakter stability indeksi. STD < 0.05 → güvenli; >0.10 → drift alarm.

### 2.7 Karakter Tutarlılığı Eval Tablosu

| Model | Embedding | EER (VoxCeleb1-O) | TR risk | Hız | Karar |
|---|---|---|---|---|---|
| ECAPA-TDNN (SpeechBrain) | 192-d | 0.69-0.90% | Düşük | Orta | Secondary |
| WavLM-base-plus-sv | 512-d | ~0.95% (paper) | En düşük | Orta | **Primary** |
| ReDimNet | 192-d | 0.50-0.70% | Düşük | Hızlı | İleride |
| ERes2NetV2 | 192-d | 0.61% | Düşük | Hızlı | Kısa-klip için ileride |
| Resemblyzer | 256-d | ~5-7% (eski) | Orta | Çok hızlı | Sadece prototip |

**Kaynak:** Desplanques (2020) arxiv 2005.07143 | Chen et al. (2024) arxiv 2406.02167 | Chen et al. (2023) arxiv 2305.12838 | huggingface.co/microsoft/wavlm-base-plus-sv

---

## Bölüm 3 — Subjektif Değerlendirme Protokolleri

### TL;DR
TTS subjective eval, çok sayıda jüri × çok sayıda örnek pahalı. Neeko ölçeğinde MUSHRA-lite + CMOS (pairwise) hibrit. ITU-T P.808 takip et, ama crowdsource yerine kendi panelimizi kuralım (TR annotator havuzu büyük platformlarda zayıf).

### 3.1 MOS — Mean Opinion Score (ITU-T P.800 / P.808)

- **Standart:** ITU-T Rec. P.800 (lab-based), ITU-T Rec. P.808 (crowdsource)
- **Skala:** 5-point Likert (5=Excellent, 4=Good, 3=Fair, 2=Poor, 1=Bad)
- **Boyutlar:** Naturalness, Intelligibility, Listening Effort (ayrı sorular)
- **Reliability (Naderi & Cutler, ICASSP 2020, arxiv.org/abs/2005.08138):** P.808 crowdsource'un P.800 lab ile korelasyonu r > 0.95 mümkün — koşul: training set + golden samples + listener qualification
- **Listener sayısı:** Naderi 2020 — kontrollü koşullarda 20 listener yeterli (>0.95 reliability)
- **Sample sayısı:** Her sistem için 50+ örnek
- **Türkçe TTS MOS literatürü:** Akar et al. (electricajournal.org/.../1218) Turkish deep-learning TTS MOS naturalness **4.49**, MOS-LQO **4.32** — basit baseline ama bizim için hedef değil (premium konumlanma daha yüksek)

### 3.2 MUSHRA — ITU-R BS.1534

- **Standart:** ITU-R BS.1534-3
- **Protokol:** Multi-stimulus (3-12 sistem), hidden reference + low-anchor (kasıtlı bozulmuş örnek), 0-100 continuous slider
- **Eleştiri (TTS için):** Wagner et al. (arxiv 2411.12719, "Rethinking MUSHRA") — modern TTS reference kalitesini geçince hidden reference anlamsızlaşıyor; **MUSHRA-RR** (relative reference) öneriyorlar
- **webMUSHRA:** github.com/audiolabs/webMUSHRA — JS framework, dokümantasyon iyi, Open Research Software paper
- **Karar:** MUSHRA-lite — hidden reference yerine target speaker recording, low-anchor olarak intentional low-bitrate TTS

### 3.3 CMOS — Comparative MOS (Pairwise A/B)

- **Skala:** -3 (much worse) ... 0 (same) ... +3 (much better)
- **Avantaj:** İstatistiksel olarak daha güçlü — Hauptmann et al. (arxiv 2110.10746) "Better than Average: Paired Evaluation" — pairwise testing aynı confidence için %30-50 daha az listener gerektirir
- **Dezavantaj:** Karşılaştırma sayısı kombinasyonel patlama (N sistem → N(N-1)/2 çift)
- **Kullanım:** Neeko'da yeni model vs. baseline her zaman CMOS ile compare et; absolute MOS milestone gate'te.

### 3.4 SMOS — Speaker Similarity MOS

- **Protokol:** Reference voice (5sn target speaker klibi) + test sample; "Bu iki ses aynı konuşmacıya mı ait?" 1-5 Likert
- **Kullanım:** Voice cloning eval'da zorunlu, Neeko karakter consistency için aynı protokol uygulanır
- **TTSDS2 paper:** SMOS dahil 11.846 rating, modern benchmark

### 3.5 Best-Worst Scaling (BWS) / Iterate-to-Differentiate

- **Paper:** "Iterate to Differentiate" (arxiv 2603.24430)
- **Protokol:** Listener'a 4'lü grup ver, "en iyi" ve "en kötü"yü seç. Bradley-Terry estimation'a feed.
- **Avantaj:** Listener cognitive load düşük, agreement yüksek
- **Karar:** Neeko panel testlerinde BWS dene; jüriler 4'lü gruplarda 20 cümle değerlendirir.

### 3.6 Inter-annotator Agreement

- **Krippendorff's alpha:** Continuous + categorical + ordinal data — TTS için ideal. α > 0.667 acceptable, > 0.80 good. Python: `krippendorff` package.
- **Fleiss' kappa:** Categorical ratings (3+ rater). κ > 0.6 substantial.
- **ICC (Intraclass Correlation):** Continuous MOS için, ICC(2,k) > 0.75 hedef.
- **Eşik (TTS pratiği):** Krippendorff α ≥ 0.40 (moderate) tipik; 0.60 (substantial) tatmin edici.

### 3.7 Platform Seçimi — Türkçe için Realite Kontrolü

| Platform | TR konuşmacı havuzu | Maliyet | Kalite kontrolü | Karar |
|---|---|---|---|---|
| **Prolific** | Sınırlı (UK/EU ağırlıklı), ~5K TR participants | ~$8-12/saat | İyi (pre-screening) | Pilot için ✓ |
| **Toloka** | Geniş (Rusya/CIS ağırlıklı ama TR var), 195+ ülke | ~$2-5/saat | Orta | Volume için ✓ |
| **MTurk** | Çok sınırlı TR | ~$3-8/saat | Düşük (golden sample şart) | ✗ |
| **Surge AI** | Premium, US ağırlıklı | $50-100/saat | Çok yüksek | ✗ (pahalı) |
| **Scale AI** | Enterprise, TR ekibi var | Custom (yüksek) | Çok yüksek | ✗ (Neeko ölçeğinde overkill) |
| **Lionbridge** | TR var | Enterprise | Yüksek | ✗ |
| **Yerel DIY** | Türk anneler + pedagoglar + Şefika network | Düşük (~$100/oturum) | El-kontrol | **✓ Primary** |

**Karar:** Neeko ölçeğinde **DIY panel** birincil — Şefika'nın NEURO GEP networkü (32 pedagog) + Erdal'ın anne validation contacts (5 doğrulama paydaşı) = ~15-20 quality jüri zaten elde. Volume için Toloka backup; Prolific spike testlerde.

### 3.8 Önerilen Subjektif Protokol (Neeko v1)

```
HAFTALIK PANEL (8-12 jüri, ~1 saat):
  - 20 cümle × 3 sistem (current, baseline, candidate)
  - MUSHRA-lite Gradio UI (0-100 continuous + hidden target recording)
  - SMOS karakter tutarlılığı için 5 ek pair
  - CMOS yeni model vs. current için 10 pair
  - Sonra: Krippendorff α + ICC raporu

MILESTONE GATE (50 jüri, 2 hafta):
  - Toloka TR + DIY hibrit
  - Full MUSHRA + naturalness MOS + SMOS
  - 80 cümle × 5 sistem
  - Bütçe: ~300-500 USD
```

**Kaynak:** Naderi & Cutler (2020) arxiv 2005.08138 | Wagner et al. (2024) arxiv 2411.12719 | Hauptmann et al. (2021) arxiv 2110.10746 | "Iterate to Differentiate" arxiv 2603.24430 | webMUSHRA — Schoeffler et al. Journal of Open Research Software 2018

---

## Bölüm 4 — TTS Arena Tarzı Pairwise Ranking (Bradley-Terry, Elo)

### TL;DR
HuggingFace TTS Arena (huggingface.co/spaces/TTS-AGI/TTS-Arena) tüm dünya çapında en güvenilir kalite-ranking sistem. Bradley-Terry model + Elo iterative update. Neeko iç panellerimizde aynı sistem küçük ölçekte kurulabilir; bütün modellerimizi (XTTS-v2, Coqui custom, ElevenLabs API, GPT-SoVITS, fine-tune'lar) Bradley-Terry leaderboard'a koy.

### 4.1 TTS Arena Mekaniği

- **Repo:** github.com/TTS-AGI/TTS-Arena (HF Space)
- **Mekanik:** Random çift seçimi → kullanıcı blind A/B → tıklama → Bradley-Terry rating update
- **Bradley-Terry:** P(A beats B) = exp(r_A) / (exp(r_A) + exp(r_B)); maximum likelihood estimation (MLE) → her oy sonrası iterative update veya batch refit
- **Chatbot Arena referansı:** colab.research.google.com/drive/1KdwokPjirkTmpO_P1WByFNFiqxWQquwH — açık kaynak BT implementation
- **Confidence intervals:** Bootstrap (1000 resample) → 95% CI hesabı

### 4.2 İstatistiksel Significance

- **Bowman et al. arxiv 2507.01633 ("Confidence and Stability"):** İki model arasında stable ranking için minimum ~200 pairwise comparison (95% CI non-overlap)
- **Pratik:** Neeko'da 4-6 model varsa → ~6 çift × 200 oy = 1.200 oy/round; haftalık 12 jüri × 100 oy/jüri = 1.200 oy ✓
- **Daha hızlı:** Multinomial Bradley-Terry + active learning (en yüksek belirsizliğe sahip çiftler önce sorulur)

### 4.3 Neeko Iç Arena — Kurulum Planı

**Stack:**
- **UI:** Gradio Spaces (HF). Mobil-friendly, mevcut TTS Arena fork edilebilir.
- **Backend:** SQLite (votes), Python `choix` library (Bradley-Terry MLE) veya `tts-arena-leaderboard` benzeri
- **Hosting:** HF Spaces ücretsiz tier (CPU yeterli, audio playback)
- **Erişim:** Şefika ağı + 5 anne + Erdal + kendi panel → invite-only

**Sample setup:**
- 5 sistem × 100 cümle (TR çocuk-yönelimli prompts)
- Her oturum 15 random pair, 5 dakika
- Hafta sonu compile, Bradley-Terry refit
- WandB dashboard live ranking

**Kaynak:** TTS Arena v2 — tts-agi-tts-arena-v2.hf.space/about | huggingface.co/blog/arena-tts | arxiv 2412.18407 "Statistical Framework for Ranking LLM-Based Chatbots" | choix Python package — github.com/lucasmaystre/choix

---

## Bölüm 5 — Çocuk Hedef Kitlede Değerlendirme

### TL;DR
Yetişkin MOS'un çocuk algısına korelasyonu **kanıtlanmamış** (Cronkite News + arxiv 2604.02629 "Toys that listen"). Neeko prod için **çocuk + anne ev sahası eval** ZORUNLU. 5-8 aile, ev içi 30 dakika oturum, video kaydı + post-survey.

### 5.1 Çocuk Engagement Metrikleri (Akademik)

- **Behavioral engagement** (arxiv 2508.15782, Çocuk-Play dataset): Swin Transformer ile %97.58 accuracy — gaze direction, posture, vocal interaction
- **Multi-view engagement estimation** (Rajagopalan et al. arxiv 1812.00253): Child-robot joint attention, multi-view deep learning
- **Tangible toy platform** (IDC 2024 paper, ACM): Turn-taking game ile sosyal etkileşim — neurodevelopmental disorder monitoring; metodoloji Neeko'ya transfer edilebilir
- **Lexical diversity** (PMC9257278): Çocuk speech çıktısının kelime çeşitliliği — Neeko etki ölçümü için

### 5.2 Pratik Ölçüm Setleri

**Quantitative (otomatik):**
- **Dikkat süresi:** Cihaz başında geçirilen dakika (Toniebox telemetry pattern — Tonies bunu yapıyor: activation times, content usage, rewinds, device IDs)
- **Tekrar oynama frekansı:** Aynı story/karakter ile session sayısı
- **Konuşma alımı:** Çocuğun Neeko'ya konuştuğu / cevap verdiği turn sayısı
- **Abandonment rate:** Story tamamlanma yüzdesi

**Qualitative (yapılandırılmış):**
- **Ebeveyn weekly survey:** 5 soruluk, çocuğun reaksiyonu + favori karakter + tedirgin edici bir an oldu mu
- **Video coding:** Yüz ifadesi (gülme, şaşkınlık, sıkılma) — 5 saniyelik bin'lerde annotate

### 5.3 Tonies / LeapFrog / VTech — Bilinen Practices

- **Tonies:** Telemetry ağırlıklı — activation, rewinds, content engagement (digitalwellbeinghub.com). Yeni AI content generator pilotu (toyworldmag.co.uk, Mayıs 2025) — 1.000 kullanıcı UK, ChatGPT script + ElevenLabs audio. Kalite ölçümü kamuya açık değil.
- **LeapFrog/VTech:** Lab + ev hibrit, ebeveyn anketleri. Akademik karşı-bulgu (Sosa 2016, JAMA Pediatrics): elektronik oyuncaklar **daha az** parent-child interaction üretiyor — Neeko bu bulgudan kaçınmalı (ölçüm protokolüne ebeveyn-çocuk etkileşim dakikası dahil et).
- **AI Toys Research (arxiv 2604.02629):** 8 çocuk (6-11 yaş) participatory design — interaction breakdown'ları + intelligence/form mismatch ana sorun. Neeko karakter+ses+pedagojik konsistans için bunu monitör et.

### 5.4 Önerilen Çocuk-Aile Eval Protokolü

```
AYLIK MILESTONE EVAL (5-8 aile):
  Setup:
    - Ev içi, doğal ortam (lab değil)
    - 30 dakika oturum, ailesi kayıt yapar (telefon)
    - Neeko prototype ile 3 farklı senaryo:
      a) Karakter (Lina) ile tanışma
      b) Yatak öncesi story
      c) Soru-cevap turu

  Capture (kademeli, mahremiyet öncelikli):
    - Audio kayıt (transcription + sentiment için)
    - Video (yüz ifadesi annotate — opt-in)
    - Cihaz telemetry (engagement, repeat, abandonment)
    - Ebeveyn post-survey (5 dakika Google Form)

  Metrikler:
    - Çocuk konuşma turn sayısı / dakika
    - Story tamamlanma yüzdesi
    - Olumlu yüz ifadesi süresi (% session)
    - Ebeveyn NPS (Net Promoter Score) for content
    - "Tekrar oynar mı?" intent score (0-10)

  Bütçe:
    - 5-8 aile × 100-200 USD compensation = 500-1600 USD/ay
    - Video coding (Şefika network pedagog): 200 USD
    - Toplam: ~1.000-2.000 USD/milestone (3 ayda bir)
```

**Kaynak:** Sosa 2016 JAMA Pediatrics (Cronkite News) | arxiv 2604.02629 "Toys that listen" | arxiv 2508.15782 Vision Transformers for engagement | IDC 2024 proceedings | Tonies telemetry analysis — digitalwellbeinghub.com

---

## Bölüm 6 — CI/CD Eval Pipeline

### TL;DR
Her model versiyonu / her LoRA / her vocoder ablation için tek komut ile **objective eval batch** çalışmalı, sonuçlar WandB'de auto-track, regression alarm Slack/email'e düşmeli. Stack: Python custom + ESPnet utils + WandB + HF Datasets fix prompt set.

### 6.1 Açık Kaynak Eval Frameworks

| Framework | Kapsam | Neeko uyumu |
|---|---|---|
| **ESPnet evaluation module** | TTS + ASR-based CER, MCD, F0 RMSE | Repo'da `utils/evaluate_mcd.py` var; reproducible recipe; **ana iskelet** |
| **Coqui TTS test suite** | Tortoise/XTTS eval, MOS dahil | trainer pip extras ile MLflow/WandB |
| **SpeechBrain eval** | Speaker rec + ASR + enhancement | ECAPA-TDNN eval recipe |
| **TTSDS** | Distribution score multi-dim | HF dataset + benchmark |
| **NISQA CLI** | Standalone, CPU OK | Tek satır integration |
| **TorchAudio SQUIM** | torchaudio pipeline | Production native |

### 6.2 Pipeline Mimarisi

```
PR/COMMIT (GitHub Actions, ~3-5 dakika):
  1. Checkout model
  2. Run 20-cümle smoke test (TR çocuk vocab fix prompt seti)
  3. UTMOS + NISQA + Whisper-TR-WER → JSON
  4. Compare vs. baseline → delta > threshold ise PR block
  5. WandB log + Slack alert (sadece regression)

NIGHTLY BATCH (Cron, GPU, ~30 dakika):
  1. Tam 500-cümle eval set
  2. Full stack (TTSDS2 + DNSMOS + SQUIM + SECS-WavLM)
  3. Karakter consistency check (Lina/Tom 50 cümle × cross-emotion)
  4. WandB dashboard refresh
  5. Hafta sonu Bradley-Terry refit (panel data ile)

MILESTONE (manuel, 1-2 saat):
  1. Çocuk-aile sahası data ingest
  2. Subjektif panel (MUSHRA-lite + CMOS) ingest
  3. Cross-correlation: objective vs. subjective vs. çocuk
  4. Versioned report (Markdown + WandB)
```

### 6.3 Regression Detection

- **Mean shift:** her metrik için baseline mean ± 2σ kontrolü
- **Distribution divergence:** Wasserstein distance (5+ commit) — distribution shift alarm
- **Per-character drift:** Lina embedding moving average, STD spike alarm
- **Statistical test:** Mann-Whitney U (non-parametric) baseline vs. new for each metric

### 6.4 Önerilen Stack — Somut Araç Listesi

```
Programming:    Python 3.11
Eval framework: ESPnet utils + custom Python (~500 LoC)
Tracker:        Weights & Biases (Team plan, ~50 USD/ay)
Storage:        HF Datasets (eval prompts, golden outputs)
Vocoder eval:   torchaudio + onnxruntime (DNSMOS ONNX model)
ASR (WER):      faster-whisper + selimc/whisper-large-v3-turbo-turkish
SSL embed:      transformers (WavLM, wav2vec2)
Subjective UI:  Gradio + HF Spaces (free tier CPU)
BT ranking:     choix Python package
CI:             GitHub Actions (matrix: model × prompt-set)
Alarms:         Slack webhook + email
Dashboard:      WandB Report (template per milestone)
```

**Maliyet özeti (aylık):**
- WandB Team: ~50 USD
- GPU eval (Modal/Runpod): ~100 USD
- HF Spaces: 0 USD (free tier)
- Toloka spike (her milestone): 200 USD
- Çocuk-aile saha (3 ayda bir): 1.500 USD / 3 = 500 USD effective
- DIY panel compensation: ~150 USD
- **Toplam: ~800-1.000 USD/ay**

**Kaynak:** ESPnet — arxiv 2110.07840, github.com/espnet/espnet | Coqui TTS trainer pip extras — pypi.org/project/coqui-tts-trainer | WandB integration ESPnet docs | TTSDS HF — huggingface.co/ttsds

---

## Bölüm 7 — Bizim Baseline Eval Suite Kararı (Neeko v1)

### Karar matrisi

| Katman | Metrik/Protokol | Frequency | Sahip | Maliyet |
|---|---|---|---|---|
| L1 — Objektif naturalness | UTMOSv2 + NISQA | Her commit | CI | ~$10/ay GPU |
| L1 — Intelligibility | Whisper-TR-WER (selimc fine-tune) | Her commit | CI | ~$15/ay GPU |
| L1 — Speaker consistency | SECS (WavLM) | Her commit | CI | ~$5/ay |
| L2 — Composite | TTSDS2 | Haftalık batch | CI Nightly | ~$20/ay |
| L2 — Signal artifact | DNSMOS P.835 | Haftalık | CI Nightly | ~$5/ay |
| L3 — Subjektif baseline | MUSHRA-lite + CMOS Gradio panel | Haftalık | DIY panel (8-12 jüri) | ~$150/ay |
| L3 — Speaker similarity | SMOS Gradio panel | 2-haftada | DIY panel | dahil |
| L4 — Pairwise ranking | Bradley-Terry iç Arena | Sürekli | HF Spaces | $0 |
| L5 — Çocuk-aile saha | 5-8 aile ev içi | Aylık/3-aylık | Şefika network + Erdal | ~$500/ay eff. |
| Total |  |  |  | **~$700-900/ay** |

### Açık kaynak başlangıç paketi (tek seferlik kurulum)

1. **Eval prompt seti:** 500 TR cümle, çocuk-yönelimli vocab. Şefika pedagog network ile 1 hafta. (Şu an yok — TR-spesifik kıt; SOMOS arxiv 2204.03040 İngilizce ama format örnek alınabilir)
2. **Reference karakter recordings:** Lina/Tom için her birinden 5 dakika studio kayıt (golden reference SECS hedefi). Bütçe: ~300 USD.
3. **CI repo template:** `tools/eval/` dizini, GitHub Actions YAML, WandB project init.
4. **Gradio panel deployment:** HF Space, mobil-friendly, TR UI.

### Risk ve dikkat

- **TR transfer riski:** UTMOS/NISQA mutlak skorları yanıltıcı olabilir → **delta-only** kullan, mutlak değere milestone gate yapma.
- **MOS bias (Mittag 2026 MOS-Bias arxiv 2603.10723):** Gender bias dahil olmak üzere listener demographic compositions ölçümü etkiler — DIY panelimiz dengeli olmalı (gender 50/50, yaş 25-45, pedagog/non-pedagog 50/50).
- **Çocuk eval mahremiyet:** Video kayıt opt-in, ses kayıt anonymize, on-device processing tercih — Neeko değer önerisiyle uyumlu.
- **Şefika kapasitesi:** NEURO GEP projesi Şefika'nın ana işi; Neeko panel için ayrılan zaman netleştirilmeli — overload riski.

---

## Bölüm 8 — Hangi Karara Bağlanır?

1. **Bir sonraki commit'te kurulacak:** ESPnet utils klonu + UTMOSv2 + NISQA + Whisper-TR Python wrapper. ~1 gün iş.
2. **Bu hafta:** 50-cümle smoke eval set + 5 baseline ses örneği (mevcut XTTS-v2 TR). Whisper-WER baseline ölçümü, milestone hedefi raporlanır.
3. **Bu ay:** Lina karakter referans kaydı (3-5 dakika), SECS golden hedefi belirlenir. İlk DIY panel (8 jüri) — Şefika ağı.
4. **Önümüzdeki ay:** İlk çocuk-aile sahası (5 aile). Tonies/LeapFrog Sosa 2016 bulgularına karşı Neeko'nun ebeveyn-çocuk etkileşim metriği ölçülür.
5. **3 ay sonra:** TTSDS2 + Bradley-Terry Arena + tüm L1-L5 stack canlı. İlk milestone gate raporu yatırımcı update'e (sayısal naturalness MOS + intelligibility WER) girer.

---

## Kaynakça (özet)

### TTS Eval Metrikleri
- Mittag, G., Möller, S. (2021). NISQA. arxiv.org/abs/2104.09494 | github.com/gabrielmittag/NISQA
- Saeki, T. et al. (2022). UTMOS. arxiv.org/abs/2204.02152 | github.com/sarulab-speech/UTMOS22
- Baba et al. (2024). UTMOSv2. arxiv.org/abs/2409.09305 | github.com/sarulab-speech/UTMOSv2
- Reddy, C. et al. (2022). DNSMOS P.835. arxiv.org/abs/2110.01763
- Kumar, A. et al. (2023). TorchAudio-SQUIM. ICASSP 2023 | pytorch.org/audio
- Minixhofer, C. et al. (2025). TTSDS2. arxiv.org/abs/2506.19441 | huggingface.co/ttsds
- Cross-lingual NISQA: arxiv.org/abs/2502.13004 (2025)

### Speaker Verification
- Desplanques, B. et al. (2020). ECAPA-TDNN. arxiv.org/abs/2005.07143
- Chen, S. et al. (2022). WavLM. huggingface.co/microsoft/wavlm-base-plus-sv
- Chen, Y. et al. (2024). ERes2NetV2. arxiv.org/abs/2406.02167
- ReDimNet (INTERSPEECH 2024): github.com/IDRnD/redimnet
- Resemblyzer: github.com/resemble-ai/Resemblyzer
- SpeechBrain spkrec-ecapa-voxceleb: huggingface.co/speechbrain/spkrec-ecapa-voxceleb

### Subjektif Protokol
- Naderi, B., Cutler, R. (2020). P.808 open-source. arxiv.org/abs/2005.08138
- Wagner, P. et al. (2024). Rethinking MUSHRA. arxiv.org/abs/2411.12719
- Schoeffler, M. et al. (2018). webMUSHRA. JORS doi.org/10.5334/jors.187 | github.com/audiolabs/webMUSHRA
- "Better than Average" pairwise: arxiv.org/abs/2110.10746
- Iterate to Differentiate: arxiv.org/abs/2603.24430
- MOS-Bias (gender): arxiv.org/abs/2603.10723

### ASR / Türkçe
- selimc/whisper-large-v3-turbo-turkish (HF model card)
- Akar, F. et al. (2024). Whisper LoRA Turkish. mdpi.com/2079-9292/13/21/4227
- Sercan/distil-whisper-large-v3-tr (HF)
- Distil-Whisper: arxiv.org/abs/2311.00430

### Arena & Bradley-Terry
- TTS Arena v2: huggingface.co/spaces/TTS-AGI/TTS-Arena | huggingface.co/blog/arena-tts
- Bradley-Terry: arxiv.org/abs/2412.18407 (Statistical Framework for Ranking LLMs)
- Confidence and Stability: arxiv.org/abs/2507.01633
- choix Python: github.com/lucasmaystre/choix

### Child Eval
- arxiv.org/abs/2604.02629 — Toys that listen (8 children, 6-11 yaş)
- arxiv.org/abs/2508.15782 — Behavioral engagement Vision Transformers
- arxiv.org/abs/1812.00253 — Child-robot joint attention engagement
- IDC 2024 proceedings: dl.acm.org/doi/proceedings/10.1145/3713043
- Sosa (2016) JAMA Pediatrics — electronic toys vs traditional (cronkitenews.azpbs.org)
- Tonies AI content pilot: toyworldmag.co.uk/tonies-explores-ai-content-generator
- Toniebox telemetry: digitalwellbeinghub.com/smart-toy-safety-digital-wellbeing-kids-ai

### Frameworks
- ESPnet2-TTS: arxiv.org/abs/2110.07840 | github.com/espnet/espnet
- Coqui TTS trainer: pypi.org/project/coqui-tts-trainer
- TTSDS2 SSW 2025: isca-archive.org/ssw_2025/minixhofer25_ssw.pdf

---

*Bitiş notu — Atlas:* Bu belge "kanıt" değil, "gözlem ve önerilen kurulum"dur. Beş katmanlı stack iddialı görünebilir ama L1+L4 (otomatik + iç Arena) tek başına Neeko'yu kör iterasyondan kurtarır; L3 ve L5 milestone'larda devreye girer. Bütçenin ~%60'ı çocuk-aile sahası — bu en yüksek değer üreten katman, kısma. Mutlak MOS skorları yerine **delta + ranking + çocuk sahası sinyali** karar verici olsun.
