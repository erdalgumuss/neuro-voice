# Araştırma Brief'i — Türkçe Çocuk-Yönelimli TTS Yığını (v0.1)

**Hazırlayan:** Atlas + Erdal · **Tarih:** 2026-05-19 · **Hedef LLM:** ChatGPT (GPT-5 / DeepResearch tarzı uzun-bağlam araştırma modu) veya eşdeğeri

> **Bu dosyayı doğrudan kopyalayıp araştırma LLM'ine yapıştırın.** Sonuç `docs/research/01-chatgpt-findings.md` olarak repo'ya iner, kürate damıtma `docs/research/02-distilled-findings.md` altında oluşur. Damıtmadan ilk karar (`docs/decisions/`) ve ilk deney (`experiments/`) çıkar.

---

## Bağlam ve hedef

**Şirket:** NEEKO — 3-7 yaş Türk çocukları için karakter-merkezli, peluş kabuk içinde edge AI çalıştıran etkileşimli oyuncak. Ticari model Tonies-benzeri: cihazdan değil içerik/figür/DLC'den marjin; %85 organik kelime-ağzı; koleksiyon + ritüel = retention. Karakterin omurgası **sesi**.

**Şu anki durum:** Üçüncü-parti TTS (ElevenLabs) kullanılıyor. Üç problem: (a) maliyet ölçek aldıkça sürdürülemez, (b) karakter sesi IP sahipliği bizde değil, (c) tek satıcıya bağımlılık stratejik risk.

**Hedef (12 ay):** **Türkçe + 3-7 yaş çocuk konuşması + sürdürülebilir karakter sesi (Neeko)** alt-domain'inde ElevenLabs-grade veya üstü kalite. Açık-ağırlıklı modellere fine-tune + speaker LoRA + Türkçe text frontend katmanı üzerinden. **Genel TTS yarışına girmiyoruz**; dar bir niche'te derinleşiyoruz.

**Donanım kısıtı:** Lokal makine düşük VRAM. Eğitim ve çoğu inference bulut GPU üzerinde (Kaggle T4 ücretsiz, Google Cloud $300 öğrenci kredisi, RunPod/Lambda Labs $1/saat civarı, TRUBA HPC akademik başvuru yolda).

**Bu brief ne istiyor:** Sektör tarama. "Hangi modelle/araçla/yaklaşımla başlayalım" sorusunun kararını üreten araştırma. Akademik özet değil; karar-üreten somut bulgular.

---

## Araştırma kapsamı — 8 alan

Her alan için beklenen çıktı: (1) **200-300 kelimelik özet**, (2) **1-2 karşılaştırma tablosu** (mümkün yerlerde), (3) **kaynaklı bulgular** (link + tarih), (4) **bizim durumumuza özel 2-3 maddelik öneri**. Tahmin yerine kaynak; "iyi" yerine "MOS X / Elo Y / kaynak Z".

### Alan 1 — Açık-ağırlıklı TTS state-of-the-art (Şubat 2025 – Mayıs 2026)

Son 15 ayda yayınlanan açık-ağırlıklı (open-weights) TTS modellerini kataloglayın. Özellikle:

- Hangi modeller TTS Arena (artificial-analysis, huggingface.co/spaces/TTS-AGI) gibi public leaderboard'larda en yüksek Elo/MOS skorlarına sahip?
- Adaylar arasında en az şunları değerlendirin: **XTTS-v2 (Coqui), F5-TTS, Fish Audio S2 / S1 (OpenAudio), Step Audio EditX / 2.5, NVIDIA Magpie-TTS Multilingual, Mistral Voxtral TTS, Kokoro, OpenVoice v2, MeloTTS, Bark, VALL-E X benzerleri, ChatTTS**.
- Lisans durumu (Apache 2.0 / MIT / non-commercial / CC-BY-NC / custom) — ticari kullanıma engel var mı?
- Türkçe desteği — resmi destek mi yoksa çok-dilli yan etkisi mi? Türkçe örneklerin gerçek kalitesi nasıl?
- Mimari özet — autoregressive vs flow-matching vs diffusion vs NAR? Vocoder entegre mi ayrı mı?
- Inference VRAM, latency (TTFB ve RTF), model boyutu (GB).
- Topluluk/HuggingFace yıldız sayısı, son commit tarihi (terk edilmiş projeleri eler).

### Alan 2 — Türkçe G2P (Grapheme-to-Phoneme) ve text frontend

Bu büyük olasılıkla bizim **en yüksek leverage'lı** katmanımız çünkü açık kaynak modeller Türkçe fonetikte zayıf.

- Türkçe için açık kaynak G2P kütüphaneleri: **espeak-ng** (Türkçe destek seviyesi), **Phonemizer**, **MaryTTS Turkish**, **g2p_en benzerleri Türkçe versiyonu**, **CharsiuG2P**, **Turkish-NLP-Suite**, **TDD/Boun NLP**. Hangisi en iyi?
- Türkçe ünlü uyumu (büyük/küçük), ünsüz yumuşaması, vurgu kuralları (genelde son hece + istisnalar), uzun ünlü, IPA gösterimi.
- Türkçe **sayı okuma** (1.234.567 → "bir milyon iki yüz otuz dört bin beş yüz altmış yedi"), **tarih okuma**, **kısaltma açma** (Dr., Av., TBMM, AB), **sembol okuma** (%, €, ₺) için açık kaynak çözümler.
- Türkçe text normalization için academic/industry approach (BoundaryNet, BERT-based normalization, vb.).
- "Türkçe LID" (language ID) ve kod-karışımı (Türkçe-İngilizce karışık cümle: "iPhone'umu açtım") nasıl ele alınır?

### Alan 3 — Speaker adaptation, voice cloning, karakter sesi sahipliği

Neeko'nun tutarlı sesinin teknik temeli.

- **Zero-shot voice cloning** teknikleri: XTTS reference audio, F5-TTS in-context cloning, OpenVoice tone color, VALL-E benzeri prompt-based. 5-30 saniye referansla ne kadar iyi sonuç?
- **Few-shot / adapter / LoRA** yaklaşımları: kaç dakika kayıt + ne kadar GPU saati + hangi kalite kazancı? Speaker LoRA tipik konfigürasyon (rank, alpha, hedef katmanlar)?
- **Full speaker fine-tune** ne zaman değer? Saat-ölçekli veri + multi-day training karşılığında ne kadar kalite/tutarlılık kazancı?
- Karakter tutarlılığı (uzun konuşmada drift olmama, oturumlar arası sabit kalma) nasıl ölçülür? **Speaker similarity metrikleri** (ECAPA-TDNN, WavLM-based, Resemblyzer)?
- "Sesi bir kez kayıtla, sonra tek sözleşmeyle sahipliğini al ve modele bas" akışı için **endüstri standardı kontrat şartları** (Resemble AI, ElevenLabs, Murf gibi şirketlerin voice talent kontrat şablonları)?

### Alan 4 — Çocuk-yönelimli konuşma (child-directed speech / "motherese")

Bu alanda akademik literatür önemli.

- Çocuğa konuşmanın akustik/prozodik farkları: F0 aralığı, hız, vurgu sıklığı, energy, pauses. Yetişkin-yetişkin konuşmasından farkları rakamla?
- Türkçe child-directed speech araştırması var mı? Boğaziçi / TDD / METU / TÜBİTAK projeleri?
- **Açık veri setleri**: Türkçe çocuk diyalogu, masal anlatımı, animasyon dublajı. CommonVoice Türkçe bölümünün çocuk-içerik oranı?
- TTS modellerini "çocuğa konuşma" tonuna fine-tune etmek için **veri seti tasarım önerileri**: kaç saat, kaç speaker, hangi cümle tipleri (soru, anlatı, oyun, ders, uyku öncesi), hangi prozodik varyasyonlar?
- Voice talent yönlendirmesi (acting direction) için profesyonel best practices — Tonies, Loquendo, audiobook stüdyoları nasıl çalışıyor?

### Alan 5 — Düşük-latency streaming TTS (Neeko cihazı için real-time)

Cihaz interaktif konuşacak; TTFB hedefi <300ms.

- **Streaming-capable** açık-ağırlıklı modeller hangileri? XTTS streaming, F5 chunked, Piper streaming, RealtimeTTS framework'leri.
- **Autoregressive vs flow-matching** modellerin streaming davranışı — flow-matching gerçek streaming yapabiliyor mu yoksa tek-shot mu?
- Cloud + edge **hibrit pipeline** patternleri: ilk chunk hızlıca, geri kalan paralel. WebRTC/LiveKit + TTS streaming entegrasyon örnekleri.
- **Edge inference** opsiyonları: ONNX Runtime, TensorRT, CoreML, GGML quantization (int8, int4). Bir 4GB-8GB VRAM edge device'ta hangi modeller çalıştırılabilir?
- **TTFB optimizasyon teknikleri**: model warmup, KV cache, batch=1 optimizasyon, vocoder fusion.

### Alan 6 — Veri toplama ve voice talent best practices

Profesyonel TTS veri kayıt standartları.

- **Akustik standartlar**: örnekleme hızı (44.1/48 kHz?), bit-depth (16/24-bit?), kayıt formatı (WAV, FLAC), mikrofon seçimi (kondenser, large-diaphragm), room treatment (anechoic ideal mi yoksa lightly-treated yeterli mi?).
- **Kayıt protokolü**: cümle başına kaç take, takeler arası mola, prompt sırası (rastgele mi kategorize mi), session uzunluğu.
- **Veri seti boyutu**: çocuk-yönelimli karakter sesi için tipik saat (Tonies, audiobook prodüksiyonları referans). Speaker LoRA için minimum-makul-ideal eşikleri?
- **Türkçe voice talent**: nereden bulunur (seslendirme ajansları İstanbul/Ankara, oyunculuk okulları), tipik saatlik/cümle başı ücret, IP sahipliği şartları, kullanım sınırlandırması.
- **Veri augmentation**: pitch shift, speed perturbation, noise injection, reverberation. Çocuğa-konuşma tonunu bozmadan augmentation nasıl yapılır?
- **Veri etiketleme**: prozodi etiketi, duygu etiketi, hedef-yaş etiketi. Açık kaynak araçlar (Praat, ELAN, Label Studio audio modülü)?

### Alan 7 — Değerlendirme metrikleri ve A/B test protokolleri

Eval olmadan iterasyon kör.

- **Objektif metrikler**: NISQA, UTMOS, DNSMOS, Whisper-WER (Türkçe Whisper kalitesi?), pesq, mcd. Hangi metrik neyi ölçer? Türkçe'ye ne kadar transfer eder?
- **Speaker similarity**: ECAPA-TDNN, WavLM-large speaker verification, Resemblyzer. Türkçe'de bias var mı?
- **Subjektif değerlendirme**: MOS (mean opinion score) toplama yöntemi, MUSHRA, CMOS (comparative). Kaç jüri / kaç örnek / hangi platform (Prolific, MTurk, Türkçe alternatifi)?
- **TTS Arena tarzı pairwise A/B** — bizim ölçeğimizde nasıl kurulur? Açık kaynak araçlar (gradio, HuggingFace Spaces)?
- **Çocuk hedef kitlede değerlendirme**: ailelerle test protokolü, çocuk dikkat süresi, retention metrikleri, çocuk-anne reaction. Akademik literatür?

### Alan 8 — Multi-character, karakter kimliği ve IP koruması

İleride Neeko + yan karakterler olacak.

- Aynı modelde **birden fazla karakter sesi** tutma: speaker embedding bank, multi-LoRA, per-character adapter switching. Hangi mimari nasıl ölçeklenir?
- **Karakter sesi versiyonlama**: v1-Neeko, v2-Neeko, deprecate eski versiyonu. Voice fingerprint hashing.
- **Karakter sesi IP koruması**: voice watermarking (AudioSeal, VoiceMark benzeri), anti-cloning defense, deepfake detection. Yasal+teknik katmanlar nasıl birleştirilir?
- Türkiye'de **ses kişilik hakkı** hukuki çerçevesi (KVKK + 5846 sayılı FSEK + medeni kanun ses kullanım hakkı). Voice talent'ın "kişilik hakkı geri çekme" senaryosu için hazırlık.

---

## Çıktı format spesifikasyonu

- Her alan ayrı bir bölüm (Markdown H2).
- Her alanın başında **TL;DR (3-5 cümle)**, sonunda **Bize özel öneri (2-3 madde)**.
- Tablolar Markdown formatında, sıralı (en üst = en güncel/önerilen).
- Kaynaklar **link + tarih** (örn. `arxiv.org/abs/2410.06885 (Ekim 2024)`).
- Yorum/tahmin yerine kaynak; emin değilse açıkça yaz ("bu konuda kamuya açık ölçüm bulamadım").
- Toplam beklenen uzunluk: **8.000-12.000 kelime**.

## Kapsam dışı (bunlara girme)

- Tacotron 2, Glow-TTS gibi 2022 öncesi mimarilerin teorik derinliği.
- Jenerik İngilizce TTS karşılaştırması (sadece çok-dilli destek bağlamında değerlendir).
- ElevenLabs, OpenAI TTS, Google Gemini TTS, Microsoft Azure ürün incelemeleri (sadece referans/benchmark olarak).
- ASR/STT (Speech-to-text) — ayrı bir araştırma konusu, bu brief'in dışı.
- LLM tarafı (Claude bağımsızlığı, açık-ağırlık LLM seçimi) — ayrı araştırma.

## Pratik not: araştırmayı nasıl bölmek

ChatGPT'nin tek oturumda 8 alanı derinlemesine işleme kapasitesi sınırlı olabilir. Pratikte iki seçenek:

- **Tek oturum:** brief'in tamamını yapıştır, "her alanı sırayla derinlikli işle" iste. DeepResearch / o3-research modunda mümkün.
- **Bölünmüş:** 8 alanı 3 oturuma böl — (Alan 1+5), (Alan 2+3+8), (Alan 4+6+7). Her oturum sonunda Markdown çıktıyı `01-chatgpt-findings-partN.md` olarak repo'ya kaydet.

İkincisi daha kaliteli sonuç verir.
