# Damıtma — NQAI Türkçe Ses Omurgası (v0.1)

**Tarih:** 2026-05-19
**Yazan:** Atlas, Erdal'la birlikte
**Kapsam:** NEEKO + NIVA + NeuroCourse + NARO dört konuşan üründe ortak kullanılacak Türkçe TTS yığınının stratejik + mimari + operasyonel sentezi
**Kaynaklar:** Atlas 5 araştırma dosyası (~22.700 kelime) + ChatGPT part1+part2 (~9.000 kelime) + Google rapor (~6.000 kelime) + saha kanıtı (Chatterbox Multilingual zero-shot 3 ses dosyası + ElevenLabs v2/v3 referans 3 ses dosyası + Erdal'ın subjektif kalibrasyonu)

---

## Yönetici özeti

Bu belge **NEEKO oyuncağı için TTS** belgesi değil. Yeni çerçeve: **NQAI portföy omurgası**. Erdal'ın dört konuşan ürünü (NEEKO çocuk oyuncağı, NIVA call-center voice, NeuroCourse eğitmen, NARO ses kimliği) aynı altyapıyı paylaşacak. TTS bir alt-katman değil; NeuroQubit'in kim olduğunun ortak omurgası — "Türkiye'de Türkçe konuşan AI yapan ekip" konumlanmasının teknik temeli.

**Premium tanımı:** ElevenLabs'ı genel TTS yarışında geçmek değil. Bizim sahamız **Türkçe + karakter tutarlılığı + call center prozodi + çocuk warmth** dar domain'leri. Bu dört kesişimde ElevenLabs'tan daha temiz + tutarlı + IP'si bizde olan ses. Genel TTS yarışına girersek 2-3 yıllık ölü savaşa gireriz; dar domain'de derinleştirirsek 6-9 ayda kazanırız.

**Saha kanıtı net:** Chatterbox Multilingual zero-shot Türkçe → "ideal ama yetmez" (telaffuz tabanı kabul edilebilir, karakter eksik). ElevenLabs v2/v3 zero-shot Türkçe → "iş görür" (hedef çizgi, ulaşılabilir). Aradaki uçurum: voice cloning + LoRA fine-tune + Türkçe text frontend ile kapatılır. Üç bağımsız araştırma kaynağı aynı reçeteyi öneriyor, saha kanıtı bunu doğruluyor.

**Karar netliği:** Bu damıtmadan **on iki karar** çıkıyor (Bölüm 9). Her biri kanıt zincirine bağlı, decision log'a satır olarak inecek. Faz-1 (NEEKO karakter sesi, 6-9 hafta) → Faz-2 (multi-product registry, 3-6 ay) → Faz-3 (NQAI dış lisans hattı, 12-18 ay) yol haritası kilitli.

**Bütçe iskeleti:** Faz-1 ~250-350K TL (~$7-10K USD) — voice talent kaşesi + stüdyo kaydı + GPU + hukuk. Erdal'ın bütçe darbası yok beyanı altında bu doğru harcama, "ucuz LoRA dene" yaklaşımı bizim için yanlış olurdu. Faz-2 ve Faz-3 yatırım turu açıldıkça netleşir.

---

## 1. Kapsam değişikliği — NEEKO'dan NQAI Omurgasına

Bugün (2026-05-19) kapsamı genişlettik. Önceki çerçeve "NEEKO için karakter sesi"; yeni çerçeve "**NQAI Türkçe ses altyapısı, dört ürünü birden taşıyan, IP'si konsolide**".

Bu değişikliğin pratik sonuçları:

**Mimari multi-product baştan kurulacak.** Tek ürüne özel olarak fine-tune'lanmış bir model yapma. Bir **voice-adapter-registry** kurulacak, her ürünün karakteri/sesi orada bir kayıt: NEEKO karakter sesi (warm, oyuncu, child-directed) bir kayıt; NIVA call-center voice (clear, professional, neutral) ikinci kayıt; NeuroCourse eğitmen (engaging, paced, didactic) üçüncü; NARO (Erdal'ın belirleyeceği) dördüncü. Hepsi aynı base model + per-character LoRA + per-character lexicon + per-character governance ile.

**Yatırım çerçevesi açık.** Erdal "sırf bu ses için Türkiye'de çok büyük yatırımlar alabilirim" beyanı altında bu altyapıyı bir holding/şirket hattı olarak konumlamak meşru. NQAI henüz kurulu değil ama altyapıyı bugünden "kurulduğunda ses hattı buradan akacak" mantığıyla tasarlıyoruz. Faz-3'te (12-18 ay) NQAI Türkçe TTS API olarak dış müşterilere açma opsiyonu, mimaride zaten hazır olduğu için marjinal maliyetle açılır.

**Ticari TTS API satmak hedef değil, yan ürün.** Asıl hedef dört iç projeyi sürdürülebilir + tutarlı + premium kalitede konuşturmak. NQAI dış API ise opsiyonel bir gelir hattı — Async tarzı, voice clone SaaS değil (deepfake riski yok), birkaç sabit karakter sesi katalog modeli.

**Marka tutarlılığı kazancı.** Aynı altyapıdan çıkan dört ürün arasında "NeuroQubit imzası" duyuluyor — ses kalitesi bağıyla portföy bütünlüğü oluşuyor. Bu, dağınık dört üründen tek bir "Türkçe konuşan AI omurgası" hikayesine geçişi sağlayan teknik temeldir.

---

## 2. Premium ne demek — somut tanım

Premium kelimesi her şeyi söyleyebilir, hiçbir şey söylemeyebilir. Bizim premium tanımımız operasyonel:

**A) Türkçe fonetik doğruluğu.** Whisper-large-v3-turbo-turkish ile WER < %3 (ElevenLabs v3 Türkçe ~%5-7 civarı tahmin). Bu metrik dar Türkçe domain'inde **bizim önceliğimiz**, ElevenLabs'ın değil; çünkü ElevenLabs Türkçe için spesifik optimize değil, biz olacağız.

**B) Karakter sesi tutarlılığı.** Aynı karakterin (NEEKO, NIVA, vb.) farklı oturumlarda, farklı cümle uzunluklarında, farklı duygu modlarında **aynı ses kimliği** olarak duyulması. WavLM-large-sv + ECAPA-TDNN ile speaker similarity cosine ≥ 0.80 same-speaker, ≥ 0.85 karakter eşik. ElevenLabs zero-shot'ta drift gözlemlendi (Take1 vs Take2); bizim LoRA fine-tune ile drift'i regression eşiğinin altında tutmamız mümkün.

**C) Dar domain prozodi uyumu.** NEEKO için child-directed (F0 ortalama 250-320 Hz, hız 110-140 WPM, hece uzatma +25-50%); NIVA için call-center (kararlı, net, neutral); NeuroCourse için didaktik (engaging, paced); NARO için Erdal'ın belirleyeceği ton. Her karakter kendi domain prozodisinde **örnek**, ElevenLabs jenerik base'i değil.

**D) Latency.** Cloud TTS için TTFB p50 < 300ms, p95 < 500ms (call-center senaryosu için sıkı; oyuncak için hafif daha gevşek de OK). Edge fallback Kokoro/Piper ile, internet kopması veya scale altında.

**E) IP sahipliği.** Voice talent sözleşmesi + adapter ağırlık sahipliği + watermarking ile karakter sesinin bizim mülkiyetimizde olması. ElevenLabs'a aylık kira ödeyerek üretmek yerine, bir kere yatırım yap + kalıcı sahiplen.

**F) Cost-per-character < $0.0001.** ElevenLabs Production ~$0.05/1000 char (gerçek production ücret), biz fine-tune'lu cloud model + edge fallback ile bunu büyük marjinle altında tutmalıyız. Dört ürün toplamda aylık milyonlarca karakter sentezleyebilir; bu ekonomik fark stratejik.

Premium = bu altı boyutun hepsi tek pakette. **Hiçbiri ElevenLabs'ın genelde "iyi" olduğu bir özellik değil; hepsi domain'imizde optimize edilmiş.**

---

## 3. Model seçimi — somut karar matrisi

Üç bağımsız araştırma + saha kanıtı bir matrise sığıyor:

| Model | Lisans | TR resmi | Streaming | Saha kanıtı | NQAI rolü |
|---|---|---|---|---|---|
| **Chatterbox Multilingual** | MIT | 23 dil dahil ✓ | Hibrit AR+flow ✓ | Zero-shot test edildi — "ideal taban, karakter eksik" | **Faz-1 birincil baseline** |
| **VoxCPM2** | Apache 2.0 | 30 dil dahil ✓ | vLLM ile ✓ (~150ms TTFB) | Henüz test edilmedi | **Faz-1 ikinci aday — yan yana koşturulacak** |
| **CosyVoice 2** | Apache 2.0 | ❌ (fine-tune gerek) | ✓ (~150ms TTFB) | Test edilmedi | **Faz-2 streaming alternatifi** |
| **F5-TTS sıfırdan SFT** | Kod MIT | Multilingual base | Chunked streaming | Test edilmedi | **Faz-2 araştırma hattı** (kendi data ile SFT) |
| **Kokoro 82M** | Apache 2.0 | Kısmi | ✓ (edge) | Henüz test edilmedi | **Edge fallback** (call kopması, offline) |
| **Piper TR** | MIT | Türkçe sesleri var | ✓ (CPU bile) | — | **Edge fallback ikinci alternatif** |
| Fish Audio S2 Pro | Research License | ✓ | ✓ | — | **Lisans satın alma değerlendirmesi** — yıllık bedele bakılır, kalite avantajı ROI hesabı |
| XTTS-v2 | CPML | ✓ | ✓ | — | Çıkış — Coqui kapandı, lisans satın alınamaz |
| F5-TTS pretrained | CC-BY-NC | — | — | — | Çıkış — CC-BY-NC ticari engel |
| Voxtral, ChatTTS, Step Audio EditX | Non-commercial | — | — | — | Sadece referans/benchmark |

**Faz-1 birincil model adayı:** **Chatterbox Multilingual veya VoxCPM2.** İki aday, eşit test, kazanan birincil. Erdal'ın Chatterbox zero-shot'ı "ideal ama yetmez" bulması olumlu sinyal — taban kabul edilebilir, üstüne LoRA inşa edilir. VoxCPM2'yi de aynı 10 cümle setinde test edip kararlaştıracağız (bir sonraki notebook).

**Faz-1 stratejik fallback:** Kalitede ikisi de yetersiz çıkarsa, **Fish Audio S2 Pro ticari lisansı satın alma seçeneği masada** kalıyor. Fish için fish.audio üzerinden quote isteyeceğiz — tahmini yıllık $5-20K USD (sticker yok, görüşmeli). Bu, ElevenLabs aylık kirası gibi devam eden bağımlılık değil; bir kerelik kalite tavanı satın alma, üzerine kendi LoRA'mız + Türkçe veri.

**Faz-2 araştırma hattı:** F5-TTS'in MIT kodunu alıp **kendi Türkçe veri setimizle sıfırdan SFT** etmek. Pretrained ağırlıklara dokunmuyoruz (CC-BY-NC), mimari kullanıyoruz. Bu, lisans-temiz, sahipli, ama compute yatırımı yüksek bir yol (3-5 gün eğitim, ~$200-500 compute). Faz-2'de kapasitemiz arttığında veya birincil model yetersiz kaldığında devreye girer.

---

## 4. Üç-katmanlı çekirdek mimari

Bu mimariyi tek bir modelden değil, üç katmandan kurguluyoruz. ChatGPT part2'nin önerdiği çerçeveyi Atlas'ın bulguları + Google raporu ile birleştirdik:

### Katman 1 — `neeko-voice-frontend` (Türkçe metin işleme, modelden bağımsız)

Repo'da bir Python paketi olarak duracak: `src/g2p/` altında. Modelden bağımsız, deterministik, 300+ golden test ile birim-testli. Modeli değiştirsek bile bu paket kalır.

**Sorumluluğu:**
- Unicode/NFKC normalization
- Cümle segmentasyonu
- Semiotic token expansion: sayı (`1.234.567` → `bir milyon iki yüz otuz dört bin beş yüz altmış yedi`), ordinal, tarih, saat, para, yüzde, yaş etiketi (`3+ yaş` → `üç yaş ve üzeri`), kısaltma (`Dr.` → `doktor`), unit
- Türkçe ek + apostrof handling (`Neeko'nun`, `Ankara'da`)
- Kod-karışımı sözlüğü (`iPhone` → `aypın`, `Bluetooth` → `blutut`, marka isimleri custom)
- Geminasyon yaması (espeak `elli` → `eli` hatası için mikro-duraksama)
- Style tag enjeksiyonu (`<style:warm>`, `<style:didactic>`, vb.)
- JSON output contract (`normalized_text` + `semiotic_spans` + `warnings`)

**Bileşenler:**
- `trnorm` (Apache 2.0) — Türkçe normalization bileşeni
- `num2words` (LGPL-2.1) — sayı → kelime
- `Zemberek` — kısaltma + morphology
- `espeak-ng` — opsiyonel phoneme debug/baseline
- Custom `NEEKO_phoneme_overrides.json` + `place_name_stress.json` — istisna sözlüğü

**Karar:** Bu paket Faz-1'in birinci sprintinde başlar (hafta 1-2). Model seçimi paralel ilerler. **G2P'ye değil, TN'ye yatırım yapıyoruz** — Türkçe ortografi nispeten düzenli, modern modeller düz Türkçe metni anlar; gerçek hata kaynağı sayı/tarih/kısaltma/kod-karışımı.

### Katman 2 — `voice-adapter-registry` (karakter kimliği + LoRA + manifest)

`src/registry/` altında. Her ürün karakteri için bir manifest:

```yaml
character_id: neeko
character_version: v1.2.0
product: neeko_v1_toy
base_model:
  name: chatterbox_multilingual  # veya voxcpm2 (Faz-1 sonu kararı)
  version: 2026-05
  license: MIT
adapter:
  type: lora
  adapter_id: neeko-lora-v1.2.0
  storage_uri: s3://nqai-voice-models/neeko/v1.2.0/adapter.safetensors
  encrypted: true
  checksum: sha256:...
voice_dataset:
  dataset_id: neeko_voice_2026_06_session_a_b_c
  talent_contract_id: contract_neeko_2026_001
  hours_train: 2.4
  hours_eval_reserved: 0.4
  sample_rate: 48000
frontend:
  lexicon_version: neeko_lexicon_v1.4
  normalization_rules_version: tr_tn_v0.7
style:
  default_tags: [child_directed, warm, clear, playful]
  forbidden_tags: [celebrity_imitation, adult_content, political]
watermark:
  provider: audioseal
  key_id: wm_neeko_v1
fingerprint:
  reference_embedding_set: neeko_ref_embed_v1
  thresholds:
    same_character_min: 0.80
    cross_character_max: 0.55
eval:
  eval_set_version: nqai_tr_eval_v0.3
  whisper_wer: 0.028
  speaker_similarity_mean: 0.84
  utmos: 4.1
  longform_drift_passed: true
release:
  status: production
  approved_by: [ml_lead, product, legal]
  date: 2026-xx-xx
```

**Aynı YAML şeması NIVA, NeuroCourse, NARO karakterleri için tekrarlanır.** Yani Faz-1'de tek karakter (NEEKO) ama mimari multi-character baştan hazır. Faz-2'de NIVA + NeuroCourse + NARO eklemek için **yeni kod değil, yeni YAML ekleme** yeterli olacak.

**Sürümleme kuralı (semver):**
- **Patch** (v1.2.1): Lexicon düzeltme, frontend rules patch — ses kimliği aynı
- **Minor** (v1.3.0): Yeni mod/duygu ekleme — ses kimliği büyük oranda aynı
- **Major** (v2.0.0): Yeni voice talent, base model değişimi — kontrollü ürün geçişi

Her sürümde yeni vs eski karakter pairwise human test (Şefika 32 pedagog + Erdal 5 anne paneli).

### Katman 3 — `voice-governance-layer` (KVKK + erişim kontrolü + audit)

`src/governance/` altında. Türk hukuk + IP koruması + güvenlik:

**Bileşenler:**
- **KVKK uyum modülü:** Ses kayıtları biyometrik veri statüsünde — açık rıza formu, aydınlatma metni, retention policy, silme prosedürü
- **Cross-border data transfer:** Yurt dışı GPU (RunPod/Lambda) kullanıyorsak data residency flag + anonymization layer
- **Access control:** Raw dataset, adapter weights, watermark keys ayrı güvenlik alanlarında. Service account bazlı; her inference + release işlemi audit log
- **Watermarking:** AudioSeal (Meta, MIT) primary, VoiceMark backup. Output sample-level
- **Voice fingerprinting:** Same-character similarity check + cross-character separation + long-form drift + prompt leakage detection. Release gate olarak çalışır
- **Sözleşme registry:** Her voice talent kontratı için `contract_id`, dataset link, scope, sunset clause, retraining rights
- **Takedown workflow:** Internette/rakipte Neeko sesine aşırı benzer içerik tespit edilirse hukuki süreç

**Karar:** Bu katmanı **production öncesi tamamlamak şart**. "Sonradan ekleriz" yaklaşımı tehlikeli — KVKK + FSEK kontratı sonradan değiştirmek voice talent ile yeniden müzakere demek. Hukuk Faz-1 hafta 2'de paralel başlar.

---

## 5. Voice talent + veri toplama spec

### Casting protokolü (Faz-1 hafta 2-3)

3-5 aday → her birine kişi başı **10 dakika kayıt** → aynı 80-120 prompt → karışık tonlar (nötr, heyecanlı, sakinleştirici, oyun yönlendiren, masal anlatan, soru soran, özür dileyen, güven veren). Adaylar üzerinde Chatterbox/VoxCPM2 ile **zero-shot clone audition**: kısa referansla model nasıl ses çıkarıyor? Bu, "kayıt sonrası LoRA fine-tune'la nereye gidebiliriz" sinyali.

**Aday değerlendirme paneli:** Şefika (NEURO-GEP pedagog network — çocuk-uygun ton uzmanı) + Erdal + 2-3 aile (Erdal'ın doğrulama contacts'i, 5 kişiden seçili). 5 boyutta puanlama:
1. Çocuk-uygunluk (warmth, korkutucu değil)
2. Karakter sıcaklığı (yapay olmayan, samimi)
3. Anlaşılırlık
4. Vocal stamina (uzun seansta yorulmuyor)
5. Acting range (heyecan/sakin/oyun/uyku tonları arasında geçiş)

**Profil:** Konservatuvar mezunu (Mimar Sinan / İstanbul Üniversitesi / Hacettepe Konservatuvar) veya çocuk tiyatrosu deneyimli kadın sanatçı. Pepee/Niloya/TRT Çocuk arkaplanı tercih sebebi. Yaş: 25-40 (vocal stamina + child-directed deneyim kesişimi).

**Kanal:** Voiz (Türkiye'nin en büyük seslendirme ajansı), Seslendirme Evi, doğrudan freelance (Bionluk/Upwork ikincil). Mailing list başlangıçta 8-10 aday ile.

### Final kayıt spec (Faz-1 hafta 4-6)

**Hedef:** 1-3 saat işlenmiş final talent kayıt (ChatGPT part2 minimum 30 dk önerdi, Atlas D ideal 5-8 saat dedi; orta yol 3 saat → LoRA için yeterli + reserve eval set ayrılabilir).

**Stüdyo + ekipman:**
- **Mikrofon:** Audio-Technica AT4040 (Faz-1 bütçe-makul, ~7-9K TL) veya kiralık Neumann TLM 103 stüdyo (~3-5K TL/gün)
- **Audio interface:** Focusrite Scarlett 2i2 (~4-5K TL) veya stüdyo PrismSound
- **Akustik:** Lightly-treated home room veya profesyonel stüdyo kiralama (İstanbul/Ankara, ~5-8K TL/gün)
- **Format:** 48 kHz / 24-bit WAV, sonradan 24 kHz model train için downsample
- **Pop filter + shock mount** zorunlu

**Kayıt protokolü:**
- Seanslar **günde 1-1.5 saat** max (vocal fatigue önleme)
- Cümle başına 3-5 take, en iyisi seçilir
- 5 mod (storytelling 2-3sa / lesson 1-1.5 / play 1-1.5 / sleep 0.5-1 / Q&A 0.5-1)
- **Voice direction** (Erdal + Şefika gözetiminde): "Karşında 4 yaşında bir çocuk var, ona göz teması kurarak konuşuyorsun" gibi somut yönlendirmeler. "Haber spikeri tonu yasak."

**Veri post-processing:**
- LUFS normalizasyonu -16 LUFS
- Sessizlik bölümleri 300ms üstü kırpılmaz, altı kırpılır (CDS pause korunur)
- MFA (Montreal Forced Aligner) Türkçe ile forced alignment
- Praat ile duygu/mod etiketleri (storytelling / lesson / play / sleep / qa)
- **Reserve set ayır** (toplam %15-20) — eğitime girmez, eval için saklanır

**Augmentation:** YASAK. CDS prozodisini bozar (Atlas D + Google iki kaynak doğruladı). Saflık > miktar.

### Sözleşme spec (Faz-1 hafta 2-4, hukuk paralel)

Voice talent ile yapılacak sözleşmede zorunlu 8 madde:

1. **Synthetic voice + AI training rights** — model eğitimi, fine-tune, sentetik üretim için açık rıza (klasik dublaj sözleşmesi yeterli değil; AI training rider eklenecek)
2. **Kullanım kapsamı** — medya (oyuncak / app / web / reklam), ülke (Türkiye + ihracat), dil (TR), süre (5-10 yıl + opsiyon), platform (NEEKO + NIVA + NeuroCourse + NARO + NQAI ileride opsiyonel), ürün ailesi
3. **Münhasırlık** — çocuk oyuncak kategorisinde exclusivity (rakip Tonies/LeapFrog tarzı ürünlerde aynı ses yasak); diğer kategoriler non-exclusive
4. **Veri ve model mülkiyeti** — kayıt dataset + adapter weights + LoRA + output ownership tamamen NQAI'da
5. **Revizyon ve yeni kayıt SLA** — yılda 1-2 ek kayıt günü, sabit ücret + SLA tanımlı
6. **Güvenlik ve saklama** — kayıtlar şifreli storage, retention 10 yıl, silme prosedürü tanımlı
7. **Ayrılık / sunset clause** — voice talent kişilik hakkı geri çekme senaryosu: **12 ay geçiş süreci + tazminat formülü**. Türk hukukunda (TMK m.24) kişilik hakkı tamamen irrevocable yapılamaz; sunset clause bunu yönetir
8. **Yasaklı kullanım** — talent'ın itibarını koruyacak madde: politik / yetişkin / yanıltıcı / üçüncü kişi taklidi yasakları

**Hukuki dayanak (Türk hukuk üç katmanı):**
- KVKK m.6 — ses biyometrik veri kimliği doğrulamada özel nitelikli; açık rıza zorunlu
- FSEK m.80 — icracı sanatçı mali hakları devredilebilir; m.80/A manevi haklar **devredilemez**
- TMK m.24 + TBK m.49 — kişilik hakkı; ses kullanım rızası geri alınabilir → sunset clause şart

**Buyout tahmini (Türkiye 2026):** Konservatuvar + çocuk-içerik deneyimli kadın sanatçı, 5-8 saat kayıt + AI training rights + 12 ay sunset = **kaşe 40-90K TL** (Atlas D + Google ortalama). Premium aday (Pepee/Niloya seslendirme ünlüsü gibi) için 100-150K TL'ye çıkabilir.

**Referans şablon:** ElevenLabs Voice Lab ToS + Resemble AI voice creation + NAVA Synthetic Voice guide. Türk hukukuna uyarlama gerekecek; hukuk firması Faz-1 hafta 2'de devreye girer.

---

## 6. Eval suite — 5 katmanlı

Iterasyon eval olmadan kör. Bu katmanı baştan kuruyoruz:

**L1 — Her commit otomatik (CI/CD):**
- 50 cümlelik mini eval seti üzerinde
- UTMOSv2 (naturalness) + NISQA (overall) + Whisper-TR-WER (intelligibility) + WavLM-SECS (speaker similarity)
- Regression detection — bir önceki sürümden Δ < 0.1 olmalı (drift değil iyileşme)
- WandB / MLflow ile log

**L2 — Haftalık tam eval:**
- 120 cümlelik tam eval seti (`data/test-sets/v1.0-full.md` Faz-1'de yazılacak)
- TTSDS2 (KIDS domain — Neeko için birebir uyumlu) + DNSMOS
- Cross-emotion consistency + long-form drift (2-5 dakikalık masal segmenti)

**L3 — Haftalık DIY panel (Gradio):**
- 8-12 kişilik DIY jüri (Şefika pedagog network + Erdal anne contacts)
- MUSHRA-lite veya CMOS (Comparative MOS) pairwise
- 20-30 örnek/hafta, model A vs model B (LoRA versiyonları arası, ya da ElevenLabs vs bizim model)

**L4 — Sürekli iç A/B Arena:**
- HuggingFace Spaces tarzı Bradley-Terry Elo ranking
- Aylık ~200-500 pairwise comparison
- Hangi LoRA versiyonu hangi domain'de kazanıyor sinyali

**L5 — Aylık aile sahası:**
- 5-8 aile ev içi test
- Engagement metrikleri: dikkat süresi, tekrar oynama, ebeveyn-çocuk etkileşim dakikası (Sosa 2016 anti-pattern kontrolü)
- Çocuk reaction (yüz ifadesi, gülümseme, soru sorma)
- Ebeveyn anketi: güven + tekrar kullanım niyeti

**Aylık eval bütçe tahmini:** ~$700-900 (WandB Team + GPU eval batch + Gradio sunucu + DIY panel + aile sahası amortize).

**TR transfer riski uyarısı:** UTMOS/NISQA İngilizce ağırlıklı eğitilmiş, Türkçe için **mutlak skor değil delta + ranking** kullanılır. Whisper-TR (selimc/whisper-large-v3-turbo-turkish, WER %18.92 CommonVoice 17) Türkçe için en güvenilir intelligibility metriği.

---

## 7. Faz-1 / Faz-2 / Faz-3 yol haritası

### Faz-1 — NEEKO Karakter Sesi MVP (6-9 hafta, 2026-05-19 → 2026-07-31)

**Hafta 1-2: Altyapı + Frontend + Hukuk**
- `neeko-voice-frontend` paketinin v0.1'i (Türkçe TN + lexicon + 300 golden test)
- VoxCPM2 yan yana test (mini eval seti) — birincil model kararını kapatma
- Hukuk firması ile voice talent rider taslağı + KVKK aydınlatma metni
- Voice talent casting çağrısı (Voiz + Seslendirme Evi + Bionluk)

**Hafta 3-4: Casting + Karar + Kayıt Hazırlığı**
- 3-5 aday 10 dk audition → Şefika+Erdal+aile paneli puanlama
- Final talent seçimi + sözleşme imzalama
- Stüdyo + ekipman ayarlama (AT4040 / Scarlett 2i2 satın alma veya stüdyo kiralama)
- Kayıt prompt pack (80-120 cümle, 5 mod) son hali

**Hafta 5-6: Profesyonel Kayıt + Post-Processing**
- 3-4 günlük stüdyo kayıt (1-1.5 sa/gün × 3-4 gün = 3-4 saat ham)
- Post-processing: LUFS norm + silence trim + MFA forced alignment + Praat etiketleme
- Reserve set ayır (~%20)

**Hafta 7-8: LoRA Fine-Tune + Eval**
- LoRA training (rank 16, alpha 32, q/k/v/o_proj target, 2000-3000 step, RunPod A100 4-6 saat × birkaç iterasyon)
- L1+L2 eval (UTMOSv2/NISQA/Whisper-TR-WER/SECS/TTSDS2)
- L3 DIY panel (Şefika 32 pedagog + Erdal 5 anne)
- Hata kategorizasyonu (plosives/sibilants/noise/drift) → iterasyon

**Hafta 9: Production Release**
- voice-adapter-registry'ye NEEKO v1.0.0 manifest
- AudioSeal watermarking integration
- L5 aile sahası testi (3-5 aile)
- NEEKO v1 backend'e entegrasyon (`neeko_server/` ile)

**Faz-1 çıktı:** NEEKO karakter sesi production-ready. Aynı kalitede ElevenLabs'a ihtiyaç yok. IP NQAI'da. Maliyet aylık ~$0 (compute amortize) vs ElevenLabs ~$300-500/ay/ürün.

### Faz-2 — Multi-Product Registry + Eval Olgunlaşma (3-6 ay, 2026-08 → 2026-12)

**Aylar 3-4: NIVA + NeuroCourse karakter seslerini ekleme**
- NIVA için yetişkin profesyonel kadın/erkek call-center voice → ayrı casting + kayıt + LoRA
- NeuroCourse eğitmen sesi (engaging, paced, didactic) → ayrı LoRA
- voice-adapter-registry'de 3 karakter aktif
- Çapraz karakter eval (cross-character separation testleri)

**Aylar 5-6: NARO + Eval suite olgunlaşma**
- NARO karakter spesifikasyonu Erdal'la kararlaştırılır + casting
- TTSDS2 + iç Arena (L4) + aylık aile sahası (L5) tam akış
- Hata pattern analizi → frontend lexicon genişletme
- Edge fallback (Kokoro/Piper TR) production
- Streaming pipeline (Pipecat/LiveKit) Neeko cihazına + NIVA call-center'a deployment

**Faz-2 çıktı:** Dört karakter, tek registry, tutarlı kalite. NQAI ses omurgası operasyonel.

### Faz-3 — NQAI Dış Lisans Hattı + Olgunlaşma (12-18 ay, 2027)

**Aylar 7-12: Voice cloning olgunlaşma + araştırma hattı**
- F5-TTS sıfırdan SFT kendi Türkçe veri ile (compute yatırımı)
- Voice fingerprinting + watermarking olgunlaşma
- Çocuk hedef kitlede longitudinal test (Sosa 2016 anti-pattern aktif kontrol)
- Wholesale yatırım turu görüşmeleri (Erdal'ın "büyük yatırım alabilirim" beyanı altında)

**Aylar 13-18: NQAI Türkçe TTS API hattı açma**
- API katmanı (multi-tenant, ama voice clone SaaS değil — sabit karakter kataloğu)
- B2B müşteri segmenti: çocuk içerik üreticileri + Türkçe AI agent ürünleri + audiobook stüdyoları
- Pricing: kullanım-bazlı (token/karakter) + enterprise lisans
- NQAI A.Ş. kurulumu (eğer yatırım turu kapatılırsa)

**Faz-3 çıktı:** NQAI'ın ilk gelir üreten ürünü, yatırım çekme dosyasında somut traction kalemi.

---

## 8. Bütçe + compute matrisi

### Faz-1 (6-9 hafta) tahmini

| Kalem | Düşük tahmin | Yüksek tahmin |
|---|---:|---:|
| Voice talent kaşesi (final + casting honorarium) | 45.000 TL | 110.000 TL |
| Stüdyo + ekipman (AT4040 + Scarlett satın alma + opsiyonel kiralama) | 25.000 TL | 55.000 TL |
| Hukuk firması (rider + KVKK + sözleşme inceleme) | 15.000 TL | 35.000 TL |
| GPU compute (LoRA iterasyonları, RunPod/Lambda) | 5.000 TL | 15.000 TL |
| Eval ekipman + yazılım (WandB Team yıllık, Gradio host) | 5.000 TL | 10.000 TL |
| L5 aile sahası (lojistik + hediye) | 5.000 TL | 12.000 TL |
| Fish Audio ticari lisans (opsiyonel, kalite tavanı için) | 0 | 250.000 TL |
| **Toplam (Fish'siz)** | **100.000 TL** | **237.000 TL** |
| **Toplam (Fish dahil opsiyon)** | — | **~487.000 TL** |

Erdal'ın bütçesine ($10K = ~350K TL) Fish hariç senaryo rahat sığar. Fish dahil senaryo, ROI tartışmasıyla (Fish kalite Chatterbox'ı gerçekten yeniyorsa lisans almak değer) ayrı karar.

### Faz-2 (3-6 ay) tahmini

| Kalem | Tahmin |
|---|---:|
| 3 ek voice talent (NIVA + NeuroCourse + NARO) kaşesi | 150.000 - 300.000 TL |
| Ek stüdyo + post-processing | 30.000 - 60.000 TL |
| GPU compute (çoklu LoRA + Arena evals) | 30.000 - 80.000 TL |
| Streaming infra (LiveKit + sunucu) | 25.000 - 60.000 TL/yıl |
| Eval + DIY panel + aile sahası amortize | 30.000 - 50.000 TL |
| **Toplam Faz-2** | **265.000 - 550.000 TL** |

### Faz-3 (12-18 ay) tahmini

Yatırım turu kapatıldıktan sonra netleşir; ön tahmin **$200K - $1M** seviyesinde (NQAI A.Ş. kurulumu + ekip + API altyapı + GTM).

---

## 9. Karar listesi (decision log'a satır olarak inecek)

Bu damıtmadan **on iki karar** çıkıyor. Her biri `docs/decisions/README.md`'ye satır olarak girecek:

| # | Karar | Gerekçe |
|---|---|---|
| D1 | NEEKO değil **NQAI ses omurgası** çerçevesi — dört ürün ortak altyapı | Erdal beyanı 2026-05-19; ölçek + IP konsolidasyonu + portföy konumlanma |
| D2 | Premium = **TR + karakter + call-center + child-directed dar domain'lerinde ElevenLabs'ı geçen** ses; genel TTS yarışı yasak | Saha kanıtı + üç araştırma kaynağı + Tonies marka mantığı |
| D3 | Faz-1 birincil aday: **Chatterbox Multilingual veya VoxCPM2** (yan yana test sonrası karar) | MIT/Apache + resmi TR + saha kanıtı taban OK |
| D4 | Production-dışı modeller: **XTTS-v2 (CPML), F5-TTS pretrained (CC-BY-NC), Voxtral (CC-BY-NC), ChatTTS (CC-BY-NC), Step Audio EditX** | Lisans engelleri; sadece kalite benchmark referansı |
| D5 | Fish Audio S2 Pro **ticari lisans quote alınacak** (Faz-1 hafta 2) — opsiyonel kalite tavanı | "Non-commercial = otomatik dışarı" yanlış çerçeve; satın alınabilirse ROI hesabı |
| D6 | Üç-katmanlı çekirdek mimari: `neeko-voice-frontend` + `voice-adapter-registry` + `voice-governance-layer` | ChatGPT part2 + Atlas C bulguları; multi-product baştan kurma |
| D7 | Türkçe frontend stack: `trnorm + num2words + Zemberek + espeak-ng + custom NEEKO lexicon + geminasyon yaması` | Üç araştırma kaynağı uyumlu; modelden bağımsız değer |
| D8 | Speaker LoRA reçetesi: **rank 16, alpha 32, q/k/v/o_proj, 2000-3000 step, RTX 4090'da 2-4 saat** | Atlas C (VoiceTailor + LoRP-TTS) somut konfig |
| D9 | Final voice talent kayıt hedefi: **1-3 saat işlenmiş, 5 mod, 48 kHz/24-bit, AT4040 + lightly-treated room** | ChatGPT part2 + Google + Atlas D uyumlu; augmentation yasak |
| D10 | Voice talent sözleşmesi 8 madde + **12 ay sunset clause** | KVKK m.6 + FSEK m.80 + TMK m.24 hukuki dayanak; irrevocable buyout Türk hukukunda imkansız |
| D11 | Eval suite 5 katmanlı (L1-L5) + Whisper-TR-WER intelligibility + WavLM-SECS speaker + TTSDS2 KIDS domain | Üç araştırma kaynağı + Şefika network DIY panel leverage |
| D12 | IP koruma: **AudioSeal primary watermark + voice fingerprint registry + access control üçlüsü** | Watermark tek başına yetersiz (De-AntiFake bypass); savunma derinliği |

---

## 10. Açık riskler + kör noktalar

Entelektüel dürüstlük açısından, bilmediklerimizi de listele:

1. **VoxCPM2 Türkçe çocuk-yönelimli kalitesi sahada doğrulanmadı.** ChatGPT'nin önerisi, ama 30 dilden biri olarak Türkçe kalitesi Chatterbox Multilingual'a göre nasıl, kamuya açık benchmark yok. Yan yana test Faz-1 hafta 1-2'de çözecek.

2. **Chatterbox Multilingual %63.75 ElevenLabs win-rate iddiası Türkçe'ye spesifik mi?** Atlas A bulgusu çok-dilli ortalama olabilir. Türkçe-spesifik split sahada test ediliyor.

3. **5-10 dakika LoRA adaptasyonu production tutarlılığı vermez.** ChatGPT uyarısı haklı — bu yalnız hızlı sinyal. Uzun-form (2-5 dk masal) + cross-session drift Faz-1 hafta 8'de ölçülür.

4. **Speaker embedding metrikleri Türkçe/çocuk bias riski.** ECAPA/WavLM VoxCeleb (İngilizce ağırlıklı) eğitildi. Insan paneli (L3) bu bias'ı kompanse eder ama tek başına SECS skoruna güvenmeyiz.

5. **Watermark sökülebilir varsayılmalı.** AudioSeal güçlü ama codec/yeniden sentezleme/adversarial perturbation karşısında garanti değil. Erişim kontrolü + sözleşme + fingerprint çoklu savunma şart.

6. **Voice talent kişilik hakkı sınırsız buyout ile ortadan kalkmaz** (TMK m.24). Sunset clause hukuki çözüm ama itibarı zedeleyen veya rıza kapsamı dışı kullanım hala risk üretir. Yasaklı kullanım maddesi sıkı tutulmalı.

7. **eSpeak GPL lisansı backend servisinde OK, ürün gömme için ayrı inceleme gerekir.** Edge device'a binary olarak gömeceksek hukuk check.

8. **Fish Audio ticari lisans fiyatlandırması belirsiz.** Quote isteyene kadar Fish hattının ROI'sini hesaplayamıyoruz. Yıllık $5K mi $50K mı belli değil.

9. **NQAI A.Ş. kuruluşu Faz-3'e bağlı.** Bugünden NQAI iddialı konuşurken hukuki/şirket statüsü henüz yok. Sözleşmelerde "NQAI / NeuroQubit Erdal Mert Karaaslan adına" gibi geçiş ifadesi gerekecek; hukuk firması bunu netleştirecek.

10. **Streaming TTFB <300ms hedefi Türkiye'den cloud GPU'ya gidiş-dönüş ile zorlanabilir.** Provider seçimi (RunPod EU vs Türkiye yerel veri merkezi) latency'yi etkiler. Edge cache + jitter buffer + Pipecat warmup birleşimi şart.

---

## 11. Bir sonraki adımlar (Önümüzdeki hafta)

Bu damıtma yarın masaya geldiğinde başlayacak somut işler:

1. **VoxCPM2 yan yana test** — `notebooks/01-voxcpm2-tr-demo.ipynb` Kaggle veya Google Colab'da koşturulur, Chatterbox sonucuyla aynı 10 cümlede karşılaştırılır. Sonuç decision D3'ü kapatır.

2. **Fish Audio quote** — fish.audio "contact for commercial license" formu doldurulur. Erdal yollar; quote döner; D5 kararı netleşir.

3. **Voice talent casting çağrısı** — Voiz + Seslendirme Evi + Bionluk üzerinden 8-10 aday listesi. İlk 3-5 aday seçimi (Şefika network input olabilir).

4. **Hukuk firması ilk görüşme** — KVKK + FSEK + AI voice rider taslağı için. Talent sözleşmesi hazırlığı + KVKK aydınlatma metni şablonu.

5. **`neeko-voice-frontend` paket iskeleti** — Atlas yazacak. `src/g2p/turkish_normalizer.py` + 50 golden test ile başlangıç.

6. **Decision log güncellemesi** — 12 satır D1-D12 `docs/decisions/README.md`'ye yazılır.

7. **`docs/architecture/pipeline.md`** — 3-katmanlı çekirdek mimari detay belgesi.

8. **`docs/architecture/latency-budget.md`** — TTFB bütçesi (ChatGPT part1'in tablosu).

Hepsi bir hafta içinde tamamlanabilir kapsamda. Atlas görevleri: 5, 6, 7, 8. Erdal görevleri: 2, 3, 4 (kanal + outreach). Atlas + Erdal birlikte: 1 (notebook koşturma).

---

## Kapanış

Bu damıtma "ne yapıyoruz, niye yapıyoruz, nasıl yapıyoruz" sorularına net cevap veriyor. Belirsizlik kalan üç yer var (VoxCPM2 sahada kalitesi, Fish lisans fiyatı, son voice talent seçimi) ve bu üç belirsizlik **bir hafta içinde somut veri ile kapatılır**.

Erdal'ın "samimiyetsiz Atlas" eleştirisi haklı bir uyarıydı. Bu belge co-founder tonunda yazıldı: bir analist değil bir ortak konuşuyor. Premium hedef, NQAI omurga çerçevesi, dört ürün birden taşıyan altyapı — bunlar bizim sahamız ve burada kazanma planımız var.

Yarın masa burada başlıyor.
