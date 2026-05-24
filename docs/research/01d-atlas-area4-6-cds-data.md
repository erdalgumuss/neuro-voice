# 01d — Atlas / Alan 4 & 6: Çocuğa Konuşma + Veri Toplama & Voice Talent

> Araştırma tarihi: 2026-05-19
> Kapsam: Neeko (3-7 yaş Türk çocuk-yönelimli AI oyuncak) için (a) çocuğa-konuşma akustik literatürü + child-directed TTS, (b) profesyonel TTS veri toplama, mikrofon/oda/protokol best practices, Türkçe voice talent ekosistemi.
> Çıktı: bizim hangi mic + room + voice talent + kaç saat veri ile başlayacağımız net karar.

---

## TL;DR — Ana Karar Önerisi (5 madde)

1. **CDS akustik hedefi**: Nötr Türk kadın yetişkin ses ortalaması ~205 Hz, erkek ~116 Hz. Çocuğa-yönelik (3-7 yaş) modda kadın ses **F0 ortalama 250-320 Hz**, F0 aralığı (max-min) **~250 Hz**, konuşma hızı **120-140 WPM** (yetişkin nötr 150-170), cümle sonu hece uzatma **+25-40%**, ortalama pause **+50-80%**. Bu hedefler Fernald'ın cross-lingual gözlemlerine uyumlu; Türkçe için Aksu-Koç korpusunda detaylı F0 ölçümü yok — kayıtlardan çıkarmak veya kendi pilot ölçümümüzü yapmak gerek.
2. **Veri seti boyut planı (faz-1)**: Tek karakter (Neeko ana ses) için **5-8 saat işlenmiş** kayıt + zengin mod etiketleri (storytelling / lesson / play / sleep / song-like). LoRA-tarzı speaker adaptasyonu Coqui XTTS-v2 / StyleTTS2 üzerinden 30-60 dakika ile bile çalışıyor, ancak prozodi karakteri için 2-3 saat minimum, "Tonies-grade" mod varyasyonu için 5-8 saat işlenmiş tavsiye edilir.
3. **Mikrofon + oda**: Faz-1 (MVP) için **Audio-Technica AT4040 + Focusrite Scarlett 2i2 + ev içi treated room (~6m² + 30% duvar paneli + NC-25 hedef)**, total ~25-35 bin TL. Faz-2 (yatırım sonrası) **Neumann TLM 103 veya Sennheiser MKH 416 + UAD Apollo + profesyonel stüdyo kiralama** (saatlik 800-1.500 TL İstanbul). Ucuz USB mic'ler (Rode NT-USB) MVP'de bile risk; TTS modeli mic farkını öğrenir, sonradan değiştirmek mismatch yaratır.
4. **Voice talent**: Konservatuvar/tiyatro mezunu **kadın dublaj sanatçısı, 25-40 yaş, çocuk-içerik deneyimli** (Pepee/Niloya/TRT Çocuk arkaplanlı tercih); proje-bazlı kaşe **40.000-90.000 TL (5-8 saat işlenmiş içerik + IP devri)**; Voiz/Seslendirme Evi/My Prodüksiyon üzerinden ajans iş takibi veya doğrudan freelance. IP devri / "AI training rights" maddesi sözleşmede zorunlu — standart seslendirme sözleşmesi bunu kapsamaz.
5. **Pipeline**: 24 kHz / 24-bit / WAV / mono / cümle başına 3 take / 30-45 dk session bloğu + 5 dk break / dialogue coach (CDS adaptasyon kontrolü) + Praat ile F0+rate post-validation + Montreal Forced Aligner Türkçe modeli ile fonem-bazlı alignment. Toplam stüdyo süresi: **5-8 sa işlenmiş = ~30-40 sa stüdyo (1:5-1:6 oranı, dialogue + retake + warm-up dahil)**.

---

## Alan 4 — Child-Directed Speech (CDS / "motherese")

### 4.1 Akustik farklar — yetişkin vs çocuk-yönelik konuşma

#### F0 (perde / pitch)

Yetişkin-yönelik konuşmada (ADS) referans değerler:

| Cinsiyet | Mean F0 | Tipik aralık (konuşma) | Kaynak |
| --- | --- | --- | --- |
| Erkek yetişkin | 116 Hz (SD 12) | 85-180 Hz | Fitch & Holbrook 1970 / Voice Science |
| Kadın yetişkin | 205 Hz (SD 24) | 165-255 Hz | Fitch & Holbrook 1970 / Voice Science |

Çocuk-yönelik konuşmada (CDS / IDS):
- **F0 ortalama**: kadın konuşmacılarda nötrden **+50-100 Hz** yükselme; yani 250-320 Hz aralığı tipik (Fernald & Simon 1984; Narayan et al. 2016).
- **F0 aralığı (variability)**: ADS'de ~80-120 Hz; CDS'de **200-300 Hz** (yaklaşık 2-3x genişleme).
- **Yaş etkisi**: Pitch yüksekliği 0-6 ay arası tepe yapıyor, sonra **çocuğun yaşı arttıkça düşmeye başlıyor** (linear decrease) — 3-7 yaşta IDS-tipi ekstrem değerler azalır ama hâlâ ADS'den belirgin yüksek (Cox et al. 2023; Nature Sci Rep).
- **Erkek vs kadın**: "Motherese" babadan daha geniş F0 aralığı kullanır; "fatherese" daha dar aralık (Scientific Reports 2017).

#### Konuşma hızı (rate)

- **ADS yetişkin nötr**: ~150-170 WPM (İngilizce), ~140-160 WPM (Türkçe, syllable-timed).
- **IDS / erken CDS**: Tek tek vokal süresi pek değişmiyor; asıl yavaşlama **phrase-final lengthening** (cümle/öbek sonu hece uzatma) ve **kısa cümleler + uzun duraklar**ın toplam etkisinden geliyor (Martin et al. 2016, ScienceDirect).
- **3-7 yaş narrative okuma modu**: profesyonel hikaye anlatıcıları 110-130 WPM'e iner (audiobook standardı 150-160 WPM; çocuk audiobook'larında 120-140 WPM).

#### Vurgu, pitch contour, energy

- CDS'de pitch contour **daha eğimli (steep)**, soru-cevap "höyle mi?" tarzı uzatma daha sık.
- Energy/loudness farkı küçük (1-3 dB); ama dinamik aralık daha geniş — fısıltıdan "Aaaaa!" şaşkınlığa.
- **Vurgu sıklığı**: CDS'de prozodik tepe (pitch accent) yoğunluğu ADS'nin ~1.5x'i.

#### Pause uzunluk ve sıklık

- IDS'de **inter-utterance pause** ADS'den 1.3-1.8x daha uzun (Soderstrom et al., Cambridge Journal of Child Language).
- Çocuk vocabulary size büyüdükçe maternal pause kısalır — yani 3 yaş > 5 yaş > 7 yaş uzunluk hiyerarşisi.
- Bizim için: 3-4 yaş modunda **ortalama pause 600-900 ms**, 5-6 yaş **400-650 ms**, 7+ yaş **300-500 ms**.

#### Phoneme süresi (vowel hyperarticulation)

- IDS'de vokal süresi **+20-50%** uzar (özellikle stressed vowels).
- "Vowel triangle" genişler — /a/, /i/, /u/ daha uç noktalarda artikule edilir.
- Türkçe vokal armoni dili olduğundan bu hyperarticulation pedagojik olarak da değerli (çocuk vokal ayrımını öğrenirken).

### 4.2 3-4 vs 5-6 vs 7+ yaş için CDS adaptasyonu (tahmini grid)

| Özellik | 3-4 yaş | 5-6 yaş | 7+ yaş | Yetişkin ADS |
| --- | --- | --- | --- | --- |
| F0 mean (kadın) | 280-320 Hz | 240-280 Hz | 220-250 Hz | 200-220 Hz |
| F0 range | ~280 Hz | ~220 Hz | ~170 Hz | ~110 Hz |
| WPM | 110-130 | 130-150 | 140-160 | 150-170 |
| Mean pause | 700-900 ms | 450-650 ms | 300-500 ms | 200-400 ms |
| Cümle uzunluğu (kelime) | 4-7 | 6-10 | 8-14 | 12-20 |
| Vowel lengthening | +35-50% | +20-35% | +10-20% | baseline |

> Not: Bu grid Fernald & Simon (1984), Narayan et al. (2016, JASA 139), Soderstrom & Trainor literatürünün ortalamasıyla Türkçe syllable-timed özelliği için hafif düzeltme yapılmış tahmin. Türkçe için spesifik 3-7 yaş CDS akustik ölçümü yayın olarak bulamadım; Aksu-Koç korpusu transkripsiyon-odaklı, ses kayıtları kısmen erişilebilir ama akustik analiz yapılmamış.

### 4.3 Türkçe için spesifik araştırmalar

- **Aksu-Koç & Slobin 1972-73 korpusu (CHILDES)**: 33 çocuk, 28-56 ay, İstanbul kentli profesyonel aileler. 54 oturum transkripsiyon. (https://childes.talkbank.org/access/Other/Turkish/Aksu.html) Akustik analiz değil — variation sets, evidentiality, narrative yapı odaklı.
- **Aksu-Koç (1988)** *The Acquisition of Aspect and Modality: The Case of Past Reference in Turkish*, Cambridge UP — Türkçe geçmiş zaman ve evidentiality kazanımı; CDS akustiği değil ama pedagojik input yapısı için altın referans. (DOI: 10.1017/S0305000900007558 inceleme.)
- **Ekmekçi & Altıntaş korpusları (CHILDES)**: 16-28 ay tek-çocuk longitudinal; CDS akustiği yine analiz edilmemiş.
- **coltekin/childes-tr GitHub** (https://github.com/coltekin/childes-tr) — Türkçe CHILDES korpora için annotation düzeltmeleri; Çağrı Çöltekin (Tübingen) maintainer.
- **Variation Sets in Turkish CDS** — case study (Academia.edu, 2021) — yapısal tekrar / pedagojik input analizi.
- **TÜBİTAK arama**: 3001/1001 portfolyosunda "Türkçe çocuk dil edinimi" projeleri var (Boğaziçi BUSIM, ODTÜ Linguistics, Hacettepe Türk Dili) ama "akustik CDS" odaklı yayın az.

> **Eylem**: Aksu-Koç korpusunun ses dosyalarına TalkBank üzerinden başvurabiliriz; 3-7 yaş CDS F0/rate ölçümünü kendi pilot çalışmamız olarak yapmak hem TÜBİTAK 1001'e hem yatırımcı story'sine değer katar. (Şefika'nın NEURO GEP 3005 projesiyle olası entegrasyon noktası.)

### 4.4 Çocuk-yönelimli TTS literatürü

#### Storytelling / audiobook neural TTS

Akademik literatür yetişkin audiobook prozodisinde yoğun, **çocuk-yönelik storytelling TTS** spesifik:
- **"Improving Speech Prosody of Audiobook TTS with Acoustic and Textual Contexts"** (arxiv 2211.02336) — cross-sentence context encoder ile audiobook prozodisini iyileştirme.
- **"Text-aware and Context-aware Expressive Audiobook Speech Synthesis"** (arxiv 2406.05672) — adjacent-sentence context ile long-form coherent prosody.
- **"Prosody Analysis of Audiobooks"** (arxiv 2310.06930) — pitch/volume/rate prediction LSTM + MPNet embedding; Google Cloud TTS'i baseline alıp human audiobook readings'e daha yakın sonuç.
- **"Audiobook synthesis with long-form neural TTS"** — Amazon Polly grubu, ResearchGate 373421017.

Kritik gözlem: Mevcut neural TTS modelleri (XTTS-v2, StyleTTS2, F5-TTS, MaskGCT) audiobook quality'de iyi, ama **child-directed mode** için explicit fine-tuning literature'da nadir; çoğu çalışma yetişkin audiobook üzerinde.

#### Ticari karşılaştırma

| Sistem | Çocuk-yönelimli mod | Lisans | Notlar |
| --- | --- | --- | --- |
| **ElevenLabs Reader** | Expressive mode, "narrator", "calm storyteller" preset | Closed, API/$ | Audio tags ile emotion control (70+ dil) |
| **Microsoft Azure Neural TTS** | HiFiNet2 vocoder 48kHz; "story", "newscast" style | Closed, API/$ | Style switching mature; Türkçe sesler hız/pitch kontrolü zayıf |
| **Coqui XTTS-v2** | Voice cloning 6 sn ref audio, prosody iyi, mode preset yok | Mozilla Public License (kullanım kısıtı) | 20+ dil; Türkçe destek var |
| **StyleTTS 2** | Long-form prosody best-in-class (open) | MIT | Reference-driven style |
| **Tonies (ticari karş.)** | "Lab" iç stüdyosu; voice talent + traditional production | N/A | Bibi & Tina, PAW Patrol vb. lisanslı; tek-figür 60-90 dk içerik |

#### Mod-bazlı varyasyon modelleme yaklaşımları

- **Style token (GST) / reference encoder**: bir adet "storytelling" / "lesson" / "sleep" reference audio, encoder bunu style embedding'e çevirir, decoder bu embedding'i conditioning olarak alır.
- **SSML / prompt-based**: ElevenLabs audio tag (`<laugh>`, `<whisper>`), Azure SSML (`<prosody pitch>`, `<break>`).
- **Multi-corpus fine-tune**: Storytelling-tagged 2 sa + lesson-tagged 1.5 sa + play-tagged 1.5 sa + sleep-tagged 1 sa + transitional-tagged 1 sa = mod-conditioned model.

> **Karar**: Faz-1'de **StyleTTS2 veya Coqui XTTS-v2** üzerinde Türkçe fine-tune + mod-conditioning (5 mod). Lisans XTTS Mozilla Public License (ticari kullanım kısıtlı, dikkat) — alternatif F5-TTS (CC-BY-NC, hâlâ kısıtlı) veya kendi MeloTTS/Matcha-TTS bazlı pipeline.

### 4.5 Veri seti tasarımı — Neeko karakteri için

#### Boyut

| Seviye | Saat | Use case |
| --- | --- | --- |
| Speaker LoRA (XTTS) | 30 dk - 2 sa | Hızlı voice cloning; mod-conditioning sınırlı |
| Mod-conditioned LoRA | 3-5 sa (5 mod × 30-60 dk) | Karakter + 5 mod ayrımı |
| **Full character fine-tune (önerilen)** | **5-8 sa işlenmiş** | Tonies-grade karakter + prozodi karakteri |
| Multi-character + multi-mod | 15-25 sa | Faz-3, çoklu figür |

> Referans: XTTS English için 541.7 sa (LibriTTS-R) + 1812.7 sa (LibriLight); ama biz zaten pretrained base üstüne fine-tune ediyoruz. Single-speaker TTS fine-tune literatüründe "30 dk yeterli" sonucu var (arxiv 2110.05798), karakter karakteri için minimum 2-3 sa.

#### Speaker

- Faz-1: **tek karakter (Neeko ana ses)**, tek voice talent.
- Faz-2: koleksiyon mantığı için 2-4 farklı karakter (kız/erkek, yaşlı/genç voice palette).

#### Cümle dağılımı (Faz-1, 5-8 sa işlenmiş hedef)

| Mod | Süre | Cümle sayısı (~10 kelime ort.) | İçerik tipi |
| --- | --- | --- | --- |
| Storytelling (hikaye) | 2-3 sa | 1200-1800 | Masal, açık-kapanış, karakter sesleri (light) |
| Lesson / informative | 1-1.5 sa | 600-900 | Konsept açıklama, sayma, alfabe, doğa |
| Play / playful | 1-1.5 sa | 600-900 | Oyun yönlendirme, şaka, ses efektleri |
| Sleep / calm | 0.5-1 sa | 250-450 | Uyku öncesi, fısıltı, ninni-tarzı (söylenmemiş) |
| Q&A / dialog | 0.5-1 sa | 300-500 | Çocuk-AI etkileşim turları |

#### Prozodi etiketi pattern

Önerilen JSON sidecar format:
```json
{
  "id": "neeko_story_0123",
  "wav": "neeko_story_0123.wav",
  "text": "Bir varmış bir yokmuş, ormanın derinliklerinde küçük bir tilki yaşarmış.",
  "mode": "storytelling",
  "target_age": "3-5",
  "f0_mean_target": 270,
  "rate_wpm_target": 120,
  "emotion": "wonder",
  "prosody": [
    {"start": 0.0, "end": 0.8, "tag": "slow_open"},
    {"start": 0.8, "end": 2.4, "tag": "neutral"},
    {"start": 2.4, "end": 3.6, "tag": "warm_emphasis"}
  ]
}
```

### 4.6 Türkçe açık veri seti envanteri

| Veri seti | Toplam saat | Speaker | Çocuk içerik % | Lisans | Link / not |
| --- | --- | --- | --- | --- | --- |
| **CommonVoice TR v23** | ~134 sa (validated 129) | 1.790 | ~5-10% (demografik metadata var, "teens" ve altı ayrı filtre) | CC-0 | https://datacollective.mozillafoundation.org/datasets/cmflnuzw71qkz8x3kil3tgjvk |
| **LibriVox TR** | ~15-25 sa (sınırlı katalog) | <20 | <5% | Public Domain | https://librivox.org/ (Türkçe katalog küçük) |
| **CHILDES Aksu-Koç** | ~30-50 sa kayıt (sadece ~kısmı dijital) | 33 çocuk + anneler | %100 (CDS odaklı) | Talkbank academic use | https://talkbank.org/childes/access/Other/Turkish/Aksu.html |
| **CHILDES Altıntaş** | ~10 sa | 1 çocuk + ailesi | %100 | Talkbank academic use | longitudinal 16-28 ay |
| **MOZ-OpenSpeech TR** | ~50 sa | çeşitli | düşük | CC-BY-SA | Mozilla araştırma uzantısı |
| **TRT Çocuk audio** | binlerce saat (dış değer tahmin) | onlarca | %100 | **Kapalı / telif** | Lisans risk yüksek; yalnızca lisans anlaşması ile |
| **Pepee/Niloya dublaj** | yüzlerce saat | sınırlı (Yağız Alp Şimşek, Berrak Nehir Ak) | %100 | **Kapalı / telif** | Düşyeri / Kaynak Holding lisans sahibi; veri olarak alınamaz |
| **Storynory TR** | yok | - | - | - | İngilizce ağırlıklı, Türkçe içerik az |

**Sonuç**: Türkçe açık veri seti envanteri **TTS-grade child content için yetersiz**. Tek opsiyon **kendi voice talent stüdyo kaydı**.

---

## Alan 6 — Veri Toplama ve Voice Talent Best Practices

### 6.1 Akustik standartlar — Profesyonel TTS Kayıt

#### Örnekleme hızı + bit-depth

| Spec | Geçmiş standard | Modern neural TTS | Yüksek-kalite | Neeko hedef |
| --- | --- | --- | --- | --- |
| Sample rate | 16 kHz | **22.05 / 24 kHz** (LibriTTS 24, LJSpeech 22.05, XTTS 24, StyleTTS2 24) | 44.1 / 48 kHz (Azure HiFiNet2, VCTK 48) | **48 kHz capture → 24 kHz model train** |
| Bit-depth | 16-bit | 16-bit | **24-bit** | **24-bit capture** |
| Format | WAV PCM | WAV PCM | WAV / FLAC lossless | **WAV PCM mono** |
| Channels | mono | mono | mono | mono |
| MP3 / AAC | yasak | yasak | yasak | yasak |

> Neden 48→24 down-sample: capture'da 48 kHz daha sonra HiFiGAN-2 / Vocos vocoder 48 kHz çıkışına geçmeye hazır oluruz; model şu an 24 kHz, gelecekte upgrade. (Microsoft Azure 2024'te 48 kHz HiFiNet2'ye geçti, trend yukarı.)

#### Mikrofon önerisi tablosu

| Bütçe | Mikrofon | Tip | Yaklaşık fiyat 2026 TL | Self-noise | Yorum |
| --- | --- | --- | --- | --- | --- |
| **Pro / Hi-end** | Neumann U87 Ai | Large diaphragm condenser | 130-160 bin | 12 dBA | Gold standard; vocal warmth + detail; mid'lerde body |
| Pro | Neumann TLM 103 | LDC | 55-75 bin | 7 dBA | U87'nin "ucuz kardeşi"; broadcast/voice ağırlıklı; düşük self-noise |
| Pro | Sennheiser MKH 416 | Shotgun (super-cardioid) | 40-55 bin | 13 dBA | Audiobook + film dub standardı; çok yönlü rejection |
| Pro | AKG C414 XLII | LDC, 9 polar pattern | 50-70 bin | 6 dBA | Brighter top-end; modern crisp sound |
| **Mid** | Audio-Technica AT4040 | LDC | 12-18 bin | 12 dBA | U87'nin %15'ine yakın kalite; TTS dataset için popüler |
| Mid | Rode NT1 (5th gen) | LDC | 10-15 bin | 4 dBA (en sessiz) | Smooth detailed, home studio için ideal |
| Mid | Lewitt LCT 440 Pure | LDC | 8-12 bin | 7 dBA | Avusturya yapımı; nötr, transparent |
| **Entry** | Shure SM7B | Dynamic (XLR) | 18-25 bin | düşük | Podcast/voice standardı; cloudlifter gerek (+6-8 bin) |
| Entry | Rode NT-USB+ | Condenser USB | 6-9 bin | 14 dBA | Sadece pilot/test için; **production'da kullanma** |
| **Yasak** | Blue Yeti, AT2020 USB, "gaming mic" | USB | 3-6 bin | yüksek | TTS dataset'i bozar; model mic karakterini öğrenir |

> **Neeko Faz-1 önerisi**: **Audio-Technica AT4040** (mid) + Focusrite Scarlett 2i2 4th gen interface (~6 bin) + Cloudlifter CL-1 (gerekirse, +6 bin) + pop filter (Stedman PS101, ~1.5 bin) + shock mount + boom arm. Toplam ~25-35 bin TL. Faz-2 (yatırım sonrası): **Neumann TLM 103 + UAD Apollo Twin** veya stüdyo kiralama (5-8 sa içerik için 30-50 sa stüdyo = ~50-100 bin TL).

#### Oda + akustik treatment

| Seviye | Hedef noise floor | Hedef RT60 | Treatment seviyesi | Yaklaşık maliyet |
| --- | --- | --- | --- | --- |
| Broadcast booth | NC-15 (~25 dB SPL) | <0.2 s | Tam izolasyon, double-wall, HVAC sessiz | 200+ bin TL (yapım) veya stüdyo kira |
| Treated room (önerilen MVP) | NC-25 (~35 dB SPL) | 0.2-0.4 s | 6-10m² oda, %25-30 duvar paneli (5cm rockwool), bass trap köşeler, kalın perde, halı | 15-30 bin TL DIY |
| "Bedroom with panels" | NC-30 (~40 dB SPL) | 0.4-0.6 s | 4-6 panel, mikrofon arkası reflection filter | 3-8 bin TL |
| Untreated | NC-35+ | >0.6 s | yok | **TTS için kullanma** |

**Önerilen ek**: Reflection filter (sE Electronics RF-X veya Auralex MudGuard) mic'in arkasına; pop filter mic'in 5-7cm önüne; mic-ağız mesafe **15-20 cm** (sabit).

### 6.2 Kayıt protokolü

#### Session ayarları

| Parametre | Standart | Neeko |
| --- | --- | --- |
| Cümle başına take | 3-5 | **3 take** (best-of-3 seçimi) |
| Session toplam süresi | 2-3 sa (vocal fatigue eşiği) | **2.5 sa max** |
| Aktif kayıt bloğu | 30-45 dk | **35 dk + 5 dk break** |
| Günlük finished output | profesyonel narrator 2.5 sa/gün | hedef **45-60 dk işlenmiş/gün** (CDS daha yorucu, retake fazla) |
| Vocal warm-up | 15-45 dk | **10-15 dk** (lip trill, hum, articulation drill) |
| Hydration | her 20-30 dk | her 20 dk, oda sıcaklığında su |
| Mic-ağız mesafe | 15-20 cm | **17 cm sabit** (mark on stand) |

> Referans: Joe Arden Narrator + Backstage + Voice Actors News literatürü; profesyonel audiobook narrator 4-6 sa stüdyoda kalır, 60-75 sayfa/gün rekorde eder; ama CDS retake oranı yüksek → 1.5x süre eklenir.

#### Dialogue coach / direktör

**Zorunlu**: CDS modunda voice talent'ın doğal yetişkin-okuma alışkanlığına geri kayması çok hızlı. Bir kişi (yönetmen / linguist) headphone ile her cümleyi dinleyip F0/rate/warmth uyumunu real-time check etmeli. Praat live görselleştirme yardımcı (1-2 sn delay ile pitch contour ekrana basabiliriz).

**Coach kontrol listesi (her take sonrası)**:
- F0 ortalama 250-320 Hz aralığında mı? (Praat real-time)
- Cümle sonu uzatma var mı?
- "Yetişkin haber spikeri" tonuna düştü mü? (red flag)
- "Bebek-bebek" abartı moduna düştü mü? (red flag — 3-7 yaş için inceltilmeli)
- Pause yeterli mi (3-5 yaş için 700-900 ms)?
- Articulation crisp mi (vokal hyperarticulation gözleniyor mu)?

#### Prompt sıralama

Mod-conditioning için en iyi pratik: **aynı mod'u blok halinde kaydet** (mode switch fatigue yaratır). Önerilen sıra:
1. Warm-up + storytelling neutral (35 dk)
2. Storytelling emotional (35 dk)
3. Break + hydrate
4. Lesson / informative (35 dk)
5. Play (35 dk)
6. Break
7. Sleep / calm (25 dk — vokal yorgunluğunda calm mode kaliteyi artırır)
8. Q&A dialog (25 dk)

### 6.3 Veri seti boyutu — referans tablo

| Set / model | Saat | Speaker | Sample rate | Tip | Lisans |
| --- | --- | --- | --- | --- | --- |
| LJSpeech | 24 sa | 1 (kadın) | 22.05 kHz | Audiobook | Public Domain |
| LibriTTS | 585 sa | 2.456 | 24 kHz | Audiobook | CC-BY-4.0 |
| LibriTTS-R | 585 sa | 2.456 | 24 kHz | Audiobook (Restored) | CC-BY-4.0 |
| VCTK | 44 sa | 109 | 48 kHz | Stüdyo cümle | ODC-By |
| Hi-Fi TTS | 292 sa | 11 | 44.1 kHz | Audiobook (high-quality) | CC-BY-4.0 |
| LibriLight | 60.000 sa | 7.000+ | 16 kHz | Audiobook (unlabeled) | CC-BY-4.0 |
| Common Voice TR | 134 sa | 1.790 | 48 kHz orig → 16/24 | Read sentences | CC-0 |

**Tonies tek-figür tipik**: 60-90 dk finished audio; iç prodüksiyon 8-12 sa stüdyo. (Bizim 5-8 sa hedefi Tonies'in 5-8 figürlüğü.)

**Faz planı**:
- **Faz-1 MVP (6 ay)**: 5-8 sa işlenmiş, 1 karakter, 5 mod. Coqui XTTS-v2 veya StyleTTS2 fine-tune. Kalite hedef MOS 4.0+.
- **Faz-2 (12-18 ay)**: 15-25 sa, 3-4 karakter, 6 mod (+ song-like). Kalite hedef MOS 4.3+.
- **Faz-3 (24+ ay)**: 50+ sa, koleksiyon ekosistemi, on-device opsiyon.

### 6.4 Türkçe voice talent ekosistemi (İstanbul + Ankara)

#### Ajanslar

| Ajans | Konum | Uzmanlık | İletişim notu |
| --- | --- | --- | --- |
| **Seslendirme Evi** | İstanbul | 550m² stüdyo, dublaj + voice over, 80+ dil, çocuk-içerik tecrübeli | seslendirmeevi.com.tr |
| **My Prodüksiyon** | İstanbul | Reklam + voice over + çocuk-içerik; demo geniş | myproduksiyon.com |
| **Voiz** | İstanbul | Online voice talent platform | voiz.com.tr |
| **Stüdyo Limon** | İstanbul | Animasyon + çocuk dublaj | endüstri içi |
| **Stüdyo İmaj** | İstanbul | Reklam + dublaj | endüstri içi |
| **Sesarşivi** | Ankara/İstanbul | Online katalog + freelance | sesarsivi.com |
| **Tek-Ses** | İstanbul | Animasyon ağırlıklı | endüstri içi |
| **Turkish Voice Talents** | online | Yurt dışı ağırlıklı, ulusal/uluslararası | turkishvoicetalents.com |
| **Bodalgo TR** | platform | Freelance Türkçe voice over | bodalgo.com/en/voice-over-talents/turkish |

#### Konservatuvar / tiyatro mezunu havuz

- **Mimar Sinan Güzel Sanatlar Üniversitesi Devlet Konservatuvarı** (oyunculuk, ses eğitimi)
- **İstanbul Üniversitesi Devlet Konservatuvarı**
- **Hacettepe Ankara Devlet Konservatuvarı**
- **Galatasaray Üniversitesi İletişim Fakültesi**
- **Yeditepe Üniversitesi Tiyatro Bölümü**

Çocuk dublajı için arayacağımız profil: konservatuvar mezunu + animasyon dublajında 3+ yıl + çocuk-içerik (Pepee/Niloya/TRT/Cartoon Network Türkiye) referansı.

#### Ücret tarifesi 2024-2026 (Oyuncular Sendikası Marjinal İşler V3.0 + sektör raporları)

| Proje tipi | Düşük | Orta | Yüksek | Birim |
| --- | --- | --- | --- | --- |
| Reklam seslendirme (radyo/TV) | 5.000 | 15.000 | 50.000+ | TL / spot |
| Radyo reklam yerel 30 sn | 200 | - | - | TL / spot |
| Radyo reklam ulusal 30 sn | 1.000 | - | - | TL / spot |
| Dizi/film dublaj | 500 | 2.000 | 5.000+ | TL / bölüm |
| Animasyon dublaj | 2.000 | 8.000 | 20.000 | TL / proje |
| Belgesel anlatımı | 1.500 | 5.000 | 10.000 | TL / proje |
| Eğitim/kurumsal | 1.000 | 5.000 | 15.000 | TL / proje |
| **AI training / dataset (yeni kategori)** | 25.000 | 60.000 | 150.000+ | TL / 5-8 sa + IP devri |

> **AI training kategorisi 2024-2026'da hızla şekilleniyor**. Standart kaşe + **AI training rights premium** (genelde +%40-100). IP devri "exclusive perpetual" ise +%50-100; "non-exclusive limited" ise +%20-40.

Aylık maaş referansı (Eleman.net 2025): seslendirme sanatçısı 26.800-40.200 TL; deneyimli serbest 50-70 bin TL. Bu rakamlar **stüdyo işçilik**; tek seferlik dataset projesi proje-bazı kaşeyle ölçülür.

#### IP sahipliği ve sözleşme şartları

Standart Türk seslendirme sözleşmesi şunları kapsamaz, **eklenmesi zorunlu**:
- "AI/ML model training rights" — sesin TTS model eğitiminde kullanım hakkı
- "Synthetic voice generation" — sentezlenmiş ses üretim hakkı
- "Voice clone licensing" — alt-lisanslama
- "Sunset clause" — gelecekte model geri çekme / silme zorunluluğu (GDPR / KVKK uyumu)
- "Royalty / revenue share" — opsiyonel; standartlaşmamış, %2-10 görüşülebilir

**Önerilen yapı**: Tek seferlik kapsamlı kaşe + (opsiyonel) düşük royalty (%2-5) + sunset clause + per-product revenue cap. Sözleşme **kayıt öncesi** imzalı olmalı.

#### Çocuk-içerik seslendirme uzmanları

- **Berrak Nehir Ak** (Niloya) — Sentries Telif lisans çatısı altında çalışıyor.
- **Yağız Alp Şimşek** (Pepee) — Düşyeri prodüksiyon.
- TRT Çocuk dublaj kadrosu (genellikle 8-12 kişilik core grup).
- Voiz / Seslendirme Evi çocuk-uzman demosunda 15-25 kadın sanatçı havuzu var.

**Erişim stratejisi**: Doğrudan kişiye ulaşmak (LinkedIn / agent) > ajans üzerinden cast > Voiz açık ihale. Doğrudan ulaşım daha hızlı ama IP pazarlığı zor; ajans üzerinden cast IP pazarlığını kolaylaştırır.

### 6.5 Veri augmentation

#### Kullanışlı teknikler (TTS dataset için)

| Teknik | Parametre | Etki | Risk |
| --- | --- | --- | --- |
| Pitch shift | ±50 cent (±0.5 semitone) | Speaker variety, küçük | Karakter F0 kayar, IDS değerini bozar |
| Speed perturbation | 0.9 / 1.0 / 1.1× | Rate variety | Vowel duration patterns kayar |
| Noise injection (room noise) | SNR 20-40 dB | Robustness | TTS için **kullanma** (clean target istiyoruz) |
| Reverberation / RIR | small room IR | Robustness | TTS için **kullanma** |
| SpecAugment (mel mask) | freq/time mask | Model-side generalisation | Train-time only, kayıt etkilemez |
| Volume / gain | ±3 dB | Loudness consistency | Düşük risk |

> **CDS'ye özel uyarı**: Pitch shift + speed perturbation CDS prozodisinin temel akustik karakterini bozar. Augmentation **inference robustness için ASR/STT tarafında** mantıklı; TTS target audio için **augmentation YAPMAYIN**. Bunun yerine: daha fazla doğal varyasyon (mod, emotion, target-age etiketi) kaydet.

#### Augmentation oranı vs kalite

| Augmentation ratio | Augmented:Original | TTS WER/MOS etki | Karar |
| --- | --- | --- | --- |
| 0% (yok) | 0 | baseline | TTS target audio için **bu** |
| 50% | 1:2 | hafif WER düşüş, MOS aynı | ASR/recognition tarafında |
| 100% | 1:1 | MOS düşebilir | TTS'de **yapma** |
| 200%+ | 2:1+ | distribution shift, MOS belirgin düşer | yapma |

### 6.6 Veri etiketleme

#### Etiket katmanları

| Katman | Format | Araç |
| --- | --- | --- |
| Transcript (text) | UTF-8 plaintext + punctuation | manuel + Whisper-large-v3 ön taslak |
| Forced alignment (phoneme) | TextGrid (Praat / MFA) | **Montreal Forced Aligner v3 + Türkçe acoustic model** (mevcut) |
| Prosody tag | JSON sidecar (per-utterance + per-span) | manuel + Praat script |
| Emotion / mode tag | enum (storytelling, lesson, play, sleep, qa) | kayıt sırasında metadata |
| Target age | enum (3-4, 5-6, 7+) | kayıt sırasında metadata |
| Quality flag | enum (clean, noisy, breath, mouth_click) | post-process otomatik + manuel review |
| Speaker ID | string | sabit (faz-1 tek karakter) |

#### Açık kaynak araç

- **Praat** (https://www.fon.hum.uva.nl/praat/) — F0/intensity/formant analiz, manuel annotation, script otomasyonu. Altın standard.
- **ELAN** (https://archive.mpi.nl/tla/elan) — multi-tier annotation, video destekli.
- **Label Studio audio** — modern web UI, multi-user, JSON export.
- **Audacity + custom labels** — basit, hızlı.
- **Montreal Forced Aligner (MFA) v3** (https://montreal-forced-aligner.readthedocs.io/) — Kaldi-based, GMM-HMM forced alignment, Türkçe acoustic model + G2P model mevcut. Recipe: monophone → triphone → speaker adaptation.

#### MFA Türkçe pipeline (özet)

```bash
# 1. Türkçe model indir
mfa model download acoustic turkish_mfa
mfa model download dictionary turkish_mfa

# 2. Validate corpus
mfa validate /data/neeko_corpus turkish_mfa turkish_mfa

# 3. Align
mfa align /data/neeko_corpus turkish_mfa turkish_mfa /data/neeko_aligned

# 4. Optional: train custom acoustic model (Neeko karakteri için)
mfa train /data/neeko_corpus turkish_mfa /data/neeko_acoustic_model.zip
```

---

## Genel — Bizim Kararlarımız (özet matris)

| Karar | Faz-1 (MVP, 6 ay) | Faz-2 (12-18 ay) | Faz-3 (24+ ay) |
| --- | --- | --- | --- |
| Veri saat | **5-8 sa işlenmiş** | 15-25 sa | 50+ sa |
| Karakter sayısı | 1 (Neeko) | 3-4 | koleksiyon |
| Mod sayısı | 5 (story/lesson/play/sleep/qa) | 6 (+song) | 8+ |
| Sample rate / bit-depth | 48 kHz / 24-bit capture → 24 kHz train | 48/24 | 48/24 hedef vocoder |
| Format | WAV PCM mono | aynı | aynı |
| Mikrofon | **AT4040 + Scarlett 2i2** | TLM 103 + UAD Apollo / stüdyo | studio booking |
| Oda | NC-25 treated home room | profesyonel stüdyo kiralama | dedicated booth |
| Voice talent | konservatuvar mezunu, çocuk-content uzman, kadın 25-40 | + erkek karakterler, + yaşlı/genç çeşit | koleksiyon roster |
| Voice talent ücret tahmin | 40-90 bin TL (5-8 sa + IP) | 150-300 bin TL | sürekli sözleşme |
| Model platformu | **StyleTTS2 veya XTTS-v2 fine-tune** | + custom mode-conditioning | on-device + cloud hybrid |
| Etiketleme | Praat manuel + MFA TR forced alignment | + emotion classifier auto-tag | + auto-prosody prediction |
| Lisans/IP | exclusive perpetual + AI training rights + sunset | aynı | + revenue share standartı |
| Akustik hedef F0 | 250-320 Hz (3-7 yaş ortalama) | + yaş bandı mod | + dinamik adaptasyon |
| Akustik hedef WPM | 110-140 (mod'a göre) | aynı | aynı |
| Toplam Faz-1 bütçe tahmini | **150-250 bin TL** (ekipman + voice talent + stüdyo + dialogue coach + annotation iş) | 400-600 bin TL | 1M+ TL |

---

## Kaynaklar

### Akademik — CDS akustik
- Fernald, A. (1985). Four-month-old infants prefer to listen to motherese. *Infant Behavior and Development*. (Klasik IDS pitch çalışması.)
- Narayan, C. R., & McDermott, L. C. (2016). Speech rate and pitch characteristics of infant-directed speech: Longitudinal and cross-linguistic observations. *JASA* 139(3), 1272-1281. (https://pubs.aip.org/asa/jasa/article/139/3/1272/910689) + CHILDES PDF: https://talkbank.org/childes/access/EastAsian/0docs/Narayan2016.pdf
- Soderstrom, M. (2007). Pause and utterance duration in CDS in relation to child vocabulary size. *Journal of Child Language*. Cambridge.
- Martin, A., et al. (2016). Utterances in infant-directed speech are shorter, not slower. *Cognition*. https://www.sciencedirect.com/science/article/abs/pii/S0010027716301901
- Cox, C., et al. (2023). Mothers adapt their voice during children's adolescent development. PMC 8770681. https://pmc.ncbi.nlm.nih.gov/articles/PMC8770681/
- Cox, C., et al. (2025). Maternal and paternal IDS modulated by child age. *Sci Rep* 15. https://www.nature.com/articles/s41598-025-98047-3
- Pitch characteristics of IDS affect infants' vowel discrimination. *Psychon Bull Rev*. https://link.springer.com/article/10.3758/BF03196290
- Soderstrom, M., Ko, E.-S., Nevzorova, U. Acoustics of CDS. UMD thesis: https://drum.lib.umd.edu/bitstreams/19a4c131-3c7c-484a-8289-f486ff6a53db/download
- Fitch, J. L., & Holbrook, A. (1970). Modal vocal fundamental frequency of young adults. (Adult F0 baseline.) Voice Science özet: https://www.voicescience.org/lexicon/average-speaking-frequencies/
- Prosodic Features from Large Corpora of CDS as Predictors of Age of Acquisition of Words. arXiv 1709.09443. https://arxiv.org/pdf/1709.09443

### Türkçe CDS / child language
- Aksu-Koç, A. (1988). *The Acquisition of Aspect and Modality: The Case of Past Reference in Turkish*. Cambridge UP. https://www.cambridge.org/core/books/acquisition-of-aspect-and-modality/5CA8840999DC76774E4A76E36680DDEC
- CHILDES Turkish Aksu-Koç Corpus. https://childes.talkbank.org/access/Other/Turkish/Aksu.html (Slobin & Aksu-Koç 1972-73, 33 children, 2;0-4;8.)
- coltekin/childes-tr GitHub (Türkçe CHILDES annotation düzeltmeleri). https://github.com/coltekin/childes-tr
- Variation sets in child-directed and child speech: A case study in Turkish. https://www.academia.edu/45976087/
- Functions of evidentials in Turkish child and child-directed speech. https://www.academia.edu/92766874/
- A Computational Analysis of Interaction Patterns in the Acquisition of Turkish. https://link.springer.com/article/10.1007/s11168-011-9072-7
- Çöltekin, Ç. et al. Resources for Turkish NLP: A critical survey. https://link.springer.com/article/10.1007/s10579-022-09605-4

### Storytelling / audiobook neural TTS
- Improving Speech Prosody of Audiobook TTS with Acoustic and Textual Contexts. arXiv 2211.02336. https://arxiv.org/pdf/2211.02336
- Text-aware and Context-aware Expressive Audiobook Speech Synthesis. arXiv 2406.05672. https://arxiv.org/pdf/2406.05672
- Prosody Analysis of Audiobooks. arXiv 2310.06930. https://arxiv.org/pdf/2310.06930
- Audiobook synthesis with long-form neural TTS. ResearchGate 373421017. https://www.researchgate.net/publication/373421017_Audiobook_synthesis_with_long-form_neural_text-to-speech
- Controllable neural TTS using intuitive prosodic features. arXiv 2009.06775. https://arxiv.org/pdf/2009.06775
- Text-driven Emotional Style Control and Cross-speaker Style Transfer. arXiv 2207.06000. https://arxiv.org/pdf/2207.06000
- A Multi-Agent AI Framework for Immersive Audiobook Production. arXiv 2505.04885. https://arxiv.org/pdf/2505.04885

### TTS modelleri + dataset
- XTTS Massively Multilingual Zero-Shot TTS. arXiv 2406.04904. https://arxiv.org/pdf/2406.04904
- Coqui XTTS docs. https://docs.coqui.ai/en/latest/models/xtts.html + HF model card https://huggingface.co/coqui/XTTS-v2
- LibriTTS. arXiv 1904.02882. https://arxiv.org/abs/1904.02882
- Hi-Fi Multi-Speaker English TTS Dataset. arXiv 2104.01497. https://arxiv.org/pdf/2104.01497
- Adapting TTS models For New Speakers using Transfer Learning. arXiv 2110.05798. https://arxiv.org/pdf/2110.05798
- Azure Neural TTS upgraded to 48kHz with HiFiNet2. https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/azure-neural-tts-voices-upgraded-to-48khz-with-hifinet2-vocoder/3665252
- Common Voice TR (Mozilla). https://datacollective.mozillafoundation.org/datasets/cmflnuzw71qkz8x3kil3tgjvk

### Voice talent (Türkiye)
- Oyuncular Sendikası — Marjinal İşler Tavsiye Taban Ücret Tarifesi V3.0 (Ocak 2024). https://www.oyuncularsendikasi.org/wp-content/uploads/2024/11/Marjinal_Isler_Tavsiye_Taban_Ucret_Tarifesi_V03_Ocak_2024.pdf
- Seslendirme.tv — 2025 ücretler ve sektörel yönelim. https://www.seslendirme.tv/seslendirme-ucretleri-teknoloji-ve-sektorel-yonelimler-2025/
- My Prodüksiyon — seslendirme ücretleri. https://www.myproduksiyon.com/seslendirme-ucretleri-ne-kadar/
- Maliyeti.com.tr — Dublaj sanatçıları 2025 kazanç. https://www.maliyeti.com.tr/dublaj-sanatcilari-ne-kadar-kazaniyor-2025/
- Eleman.net — Seslendirme sanatçısı maaşları 2025. https://www.eleman.net/meslek/seslendirme-sanatcisi/maas
- Seslendirme Evi (ajans). https://www.seslendirmeevi.com.tr/
- Bodalgo Türkçe katalog. https://www.bodalgo.com/en/voice-over-talents/turkish/tr

### Recording protocol + acoustics
- Joe Arden Narrator — Voice Acting for Audiobooks. https://joeardennarrator.com/voice-acting-for-audiobooks-techniques/
- Backstage — 10 Essential Audiobook Narration Skills. https://www.backstage.com/magazine/article/10-skills-you-need-for-audiobook-narration-voice-work-67133/
- Voice Actors News — Audiobook Narration: From Prep to Performance. https://www.voiceactorsnews.com/2025/08/06/audiobook-narration-preparation-performance/
- Sonarworks — How to treat vocal booths. https://www.sonarworks.com/blog/learn/how-to-treat-vocal-booths-and-live-rooms
- Soundref — Neumann U87 Ai 2026 review. https://soundref.com/neumann-u87-ai-review/
- Gearspace — Vocal mic shootout NT1/U87/TLM103/C414. https://gearspace.com/board/gear-shoot-outs-sound-file-comparisons-audio-tests/910383-vocal-mic-shootout-new-rode-nt1-u87-tlm-103-tlm-127-c414.html

### Augmentation + alignment
- SpeechBrain — Speech Augmentation. https://speechbrain.readthedocs.io/en/latest/tutorials/preprocessing/speech-augmentation.html
- A Comparison of Speech Data Augmentation Methods (S3PRL). arXiv 2303.00510. https://arxiv.org/pdf/2303.00510
- Montreal Forced Aligner v3 docs. https://montreal-forced-aligner.readthedocs.io/en/latest/user_guide/index.html
- MFA train acoustic model. https://montreal-forced-aligner.readthedocs.io/en/v3.2.3/user_guide/workflows/train_acoustic_model.html
- Generate Phonetic alignment with MFA for TTS (Medium / Osakuade). https://osakuadeopeyemi.medium.com/generate-forced-alignment-with-montreal-forced-aligner-mfa-383f91a6f2a1

### Ticari / pazar
- Tonies — Wikipedia. https://en.wikipedia.org/wiki/Tonies
- ElevenLabs Expressive Mode docs. https://elevenlabs.io/docs/eleven-agents/customization/voice/expressive-mode
- ElevenLabs Audiobooks. https://elevenlabs.io/use-cases/audiobooks
- Niloya — Vikipedi. https://tr.wikipedia.org/wiki/Niloya
- Pepee — Wikipedia. https://en.wikipedia.org/wiki/Pepee
- TRT Çocuk Kitaplık (sesli hikaye). https://www.trtcocuk.net.tr/trt-cocuk-kitaplik

---

## Açık sorular / ileri araştırma

1. **Türkçe CDS akustik pilot ölçüm**: Aksu-Koç korpusu ses kayıtlarına TalkBank academic erişimi nasıl alınır? Pilot 5-10 anne sesinden F0/rate/pause ölçümü → "Türkçe için ilk CDS akustik benchmark" hem bilimsel hem PR değeri. Şefika NEURO GEP 3005 ile entegrasyon noktası.
2. **AI training rights sözleşme şablonu**: Türkiye'de standart bir model yok; ALMAN Tonies / İngiliz audiobook sözleşmelerinden çevirip avukat (Esra Demir? — pending-decisions'a ekle) görüşü almalı.
3. **Voice talent kısa-listesi (Faz-1)**: Doğrudan 3-5 isim listesi çıkarmak için Berrak Nehir Ak agent + Voiz/Seslendirme Evi cast call + 2 freelance konservatuvar mezunu demo karşılaştırması. 2-3 hafta süreçle ses örneği toplama.
4. **Stüdyo opsiyonu MVP**: Ev içi treated room mı, İstanbul'da saatlik kiralama mı? Kira opsiyonu (Levent / Maslak / Kadıköy 800-1500 TL/sa) toplam 30-40 sa için ~30-60 bin TL → ev içi 25-35 bin yatırım vs. Esra/dialogue coach paylaşımı ile breakeven.
5. **MFA Türkçe acoustic model kalite testi**: Mevcut `turkish_mfa` modeli child-content fonem alignment'ında ne kadar iyi? Yetişkin korpus üzerinde eğitilmiş, CDS hyperarticulation pattern'ini yakalar mı? Pilot test.
