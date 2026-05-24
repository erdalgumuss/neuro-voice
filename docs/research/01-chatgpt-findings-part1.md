# Türkçe Çocuk-Yönelimli TTS Yığını — Araştırma Bulguları, Part 1

**Kapsam:** Alan 1 — açık-ağırlıklı TTS state-of-the-art; Alan 5 — düşük-latency streaming TTS  
**Tarih:** 2026-05-19  
**Bağlam:** NEEKO için Türkçe, çocuk-yönelimli, karakter-merkezli ve ticari olarak sürdürülebilir TTS altyapısı.  
**Karar sorusu:** “ElevenLabs bağımlılığını azaltmak ve Neeko karakter sesini sahiplenmek için hangi açık-ağırlıklı model/mimariyle ilk 90 gün başlamalıyız?”

---

## Yönetici özeti

Bu turdaki en önemli sonuç: Brief’te listelenen adaylar doğru, fakat Mayıs 2026 itibarıyla karar matrisine **VoxCPM2**, **OmniVoice**, **Qwen3-TTS** ve kısmen **MOSS-TTS** mutlaka eklenmeli. Özellikle **VoxCPM2**, resmi Türkçe desteği, Apache-2.0 lisansı, ticari kullanım iddiası, voice cloning/style control ve streaming desteği nedeniyle Neeko’nun ilk üretim-yakın deneyleri için en güçlü başlangıç adayıdır. **OmniVoice** 600+ dil desteği, Apache-2.0 lisansı ve çok düşük RTF iddiasıyla ikinci ana adaydır; ancak Türkçe kalite, çocuk-yönelimli prozodi ve aksan sızıntısı mutlaka yerel testle ölçülmelidir.

Leaderboard tarafında Artificial Analysis’in mevcut açık-ağırlık sıralamasında **Fish Audio S2 Pro** en yüksek açık-ağırlıklı model olarak görünüyor; onu Step Audio EditX, NVIDIA Magpie-Multilingual, Voxtral TTS ve Kokoro takip ediyor. Ancak bu sıralama Neeko için tek başına karar kriteri olamaz. Çünkü Fish S2 Pro, Voxtral, F5-TTS, XTTS-v2 ve ChatTTS gibi birçok güçlü modelin ön-eğitim ağırlıkları ticari kullanımda kısıtlı veya ayrı lisans gerektiriyor. NEEKO’nun hedefi yalnız kalite değil; karakter sesinin IP sahipliği, satıcı bağımsızlığı, Türkçe tutarlılık ve maliyet kontrolü.

İlk 90 gün için önerilen rota: **VoxCPM2 birincil backbone adayı**, **OmniVoice ikinci aday**, **XTTS-v2 Türkçe baseline**, **Fish S2 Pro kalite tavanı ama lisanslı değerlendirme**, **F5-TTS / X-Voice araştırma hattı**, **Kokoro/Piper/MOSS-TTS-Nano edge fallback/latency sandbox**. Nihai karar Türkçe çocuk-yönelimli 100–200 cümlelik bir Neeko eval setiyle, aynı referans ses ve aynı metin normalizasyon katmanı üzerinden verilmelidir.

---

## Alan 1 — Açık-ağırlıklı TTS state-of-the-art

### TL;DR

Açık-ağırlıklı TTS alanı 2025–2026 arasında iki yöne ayrıştı: yüksek kaliteli büyük codec/LLM tabanlı modeller ve deployment-first küçük/streaming modeller. Genel leaderboard kalitesinde Fish Audio S2 Pro güçlü; fakat Neeko açısından ticari lisans, Türkçe destek ve streaming daha belirleyici. Bu üç kriter birlikte değerlendirildiğinde **VoxCPM2** şu anda en iyi “başla ve ölç” adayıdır. **OmniVoice** çok geniş dil kapsamı ve Apache lisansıyla ciddi alternatif; **Qwen3-TTS** teknik olarak çok güçlü ama resmi Türkçe desteği yok. XTTS-v2 Türkçe baseline olarak hâlâ değerli, fakat CPML nedeniyle ticari üretim backbone’u olarak kullanılmamalı.

### Karar matrisi

| Öncelik | Model | Lisans / ticari durum | Türkçe durumu | Mimari / teknik özet | Kalite / performans sinyali | Neeko için karar |
|---:|---|---|---|---|---|---|
| 1 | **VoxCPM2** | Apache-2.0; üretim için ticari kullanım iddiası var | **Resmi destekli 30 dil içinde Türkçe var** | Tokenizer-free diffusion autoregressive; MiniCPM-4 backbone; 2B parametre; voice design + controllable cloning | 2M+ saat multilingual training; 48 kHz çıktı; HF kartında ~8 GB VRAM, RTX 4090’da RTF ~0.30 / Nano-vLLM ile ~0.13 | **Birincil üretim-yakın aday. İlk Neeko benchmark burada başlamalı.** |
| 2 | **OmniVoice** | Apache-2.0 | 600+ dil iddiası; Türkçe kapsanıyor olmalı fakat kalite doğrulanmalı | Diffusion language model-style discrete NAR; text→multi-codebook acoustic tokens | RTF 0.025 iddiası; 3–10 sn referans önerisi; voice cloning/design | **İkinci ana aday. Türkçe kalite ve aksan sızıntısı ölçülmeden production seçilmemeli.** |
| 3 | **Fish Audio S2 Pro / OpenAudio S2** | Fish Audio Research License; ticari kullanım için ayrı lisans | 80+ dil içinde Türkçe var; tier-1 değil | Dual-AR; 4B Slow AR + 400M Fast AR; SGLang serving | Artificial Analysis’te en yüksek açık-ağırlıklı model; TTFA ~100 ms ve H200’de RTF ~0.195 iddiası | **Kalite tavanı benchmark’ı. Ticari lisans netleşmeden ana rota yapma.** |
| 4 | **Qwen3-TTS** | Apache-2.0 | Resmi 10 dil içinde Türkçe yok | 0.6B/1.7B; dual-track AR; 12 Hz tokenizer; streaming first-packet 97–101 ms | 3 sn voice cloning, voice design, instruction control | **Mimari ve streaming referansı. Türkçe fine-tune yapılmadan Neeko için doğrudan uygun değil.** |
| 5 | **F5-TTS** | Kod MIT; pre-trained model CC-BY-NC | Resmi Türkçe güçlü değil; custom multilingual/fine-tune hattı mümkün | Non-autoregressive flow matching + DiT; Vocos/BigVGAN vocoder | L20 GPU’da TRT-LLM ile ortalama latency 253 ms, RTF 0.0394 raporlanmış | **Araştırma/fine-tune hattı. Ticari kullanım için lisans uyumlu checkpoint veya yeniden eğitim gerekir.** |
| 6 | **XTTS-v2** | Coqui Public Model License; ticari kullanım kısıtlı | **Resmi destekli diller içinde Türkçe var** | AR transformer + vocoder; reference-audio cloning; fine-tune destekli | <200 ms streaming latency iddiası; 6 sn referansla cloning | **Türkçe baseline ve hızlı demo için iyi. Ticari üretim için uygun değil.** |
| 7 | **NVIDIA Magpie-TTS Multilingual 357M** | NVIDIA Open Model License; commercial-ready ifadesi var | Resmi dillerde Türkçe yok | AR encoder-decoder + local transformer refinement; 357M | Streaming voice agent demosunda pipeline P50 ~101 ms RTX 5090 | **Latency mimarisi için iyi referans; Türkçe backbone değil.** |
| 8 | **Kokoro 82M** | Apache-2.0 | Resmi Türkçe yok | StyleTTS2/ISTFTNet çizgisi; küçük model | 82M parametre; ucuz/edge-friendly; Artificial Analysis açık modeller top-5 içinde | **Edge sandbox / fallback için değerli; Türkçe karakter sesi için tek başına yetmez.** |

### Diğer adayların durumu

| Model | Durum | Neeko notu |
|---|---|---|
| **Voxtral TTS** | CC BY-NC 4.0; 9 resmi dil, Türkçe yok; H200’de 70 ms latency / RTF 0.103 iddiası | Kalite/latency ilginç, fakat lisans ve Türkçe nedeniyle üretim dışı. |
| **Step Audio EditX / Step Audio 2.5** | Public leaderboard’da güçlü; özellikle ifade/stil/audio editing; ağırlık lisansı ve Türkçe kapsamı net değil | Neeko karakter prozodisi için araştırma referansı; ana backbone değil. |
| **OpenVoice v2** | MIT; zero-shot tone-color cloning; native diller EN/ES/FR/ZH/JA/KO | Tam TTS backbone yerine tone conversion/voice cloning katmanı gibi düşünülmeli; Türkçe için ana aday değil. |
| **MeloTTS** | MIT; EN/ES/FR/ZH/JA/KO; Türkçe PR’ı mainline değil | Üretim seviyesi Türkçe için zayıf; düşük-risk ticari baseline olabilir ama kalite tavanı düşük. |
| **Bark** | MIT; generative text-to-audio; prompttan sapma riski var | Çocuk karakteri için yaratıcı ses efektleri/demosu olabilir; deterministik ürün TTS’i için zayıf. |
| **ChatTTS** | Kod AGPLv3+, model CC BY-NC; akademik kullanım; EN/ZH | Ticari üretim ve Türkçe için uygun değil. |
| **VALL-E X reprodüksiyonları** | MIT reprodüksiyonlar var; EN/ZH/JA odaklı; Microsoft resmi ağırlık yayınlamadı | Araştırma referansı; Neeko üretim hattı için eski ve sınırlı. |
| **MOSS-TTS / MOSS-TTS-Nano** | 2026’da hızlı gelişen açık aile; Nano CPU/edge odaklı; multilingual/code-switching iddiaları var | Edge fallback ve dialog/audiobook senaryoları için izlenmeli; Türkçe kalite ayrıca ölçülmeli. |
| **X-Voice** | 0.4B flow-matching, 30 dil, IPA tabanlı; code MIT, ağırlık CC-BY-NC | Türkçe açıkça listelenmemiş; ama IPA/G2P yaklaşımı Alan 2 için önemli fikir veriyor. |

### Lisans açısından kırmızı çizgiler

NEEKO’nun “karakter sesi IP’si bizde olsun” hedefi açısından lisans, model kalitesi kadar kritik. Ticari ürün içinde kullanılacak modelin ağırlıkları, çıktı kullanım hakları, fine-tune hakkı ve türev model hakkı net olmalı. Bu yüzden aşağıdaki ayrım pratikte karar verici:

1. **Üretim adayı lisanslar:** Apache-2.0 / MIT / açık ticari kullanım izni net olan modeller. Bu grupta bugün en güçlü adaylar VoxCPM2, OmniVoice, Kokoro, OpenVoice v2, MeloTTS ve muhtemelen bazı MOSS varyantları.
2. **Araştırma ama production riskli:** XTTS-v2, F5-TTS pretrained, Fish S2 Pro, Voxtral TTS, ChatTTS, X-Voice ağırlıkları. Bu modeller benchmark ve teknik öğrenme için çok değerli; fakat ticari deployment, model fine-tune çıktısı ve karakter sesi sahipliği için ayrı hukuki/lisans incelemesi gerekir.
3. **Ayrı ticari lisansla mümkün:** Fish S2 Pro gibi modeller kalite açısından güçlü olabilir; ama lisans maliyeti/sözleşme bağımlılığı ElevenLabs bağımlılığından farklı fakat yine stratejik bir bağımlılık yaratabilir.

### Türkçe açısından teknik riskler

Türkçe desteği “model Türkçe konuşabiliyor” seviyesinde kalmamalı. Neeko için gereken şey; sayıları, tarihleri, kısaltmaları, özel isimleri, çocuk dili ritmini, vurgu düzenini ve karakter tutarlılığını doğru üreten dar-domain bir konuşma sistemi. VoxCPM2’nin resmi Türkçe desteği büyük avantaj; fakat yine de şu testler yapılmadan üretim seçilmemeli:

- **Sayı/tarih/kısaltma:** “23 Nisan”, “1.234”, “TBMM”, “Dr. Ayşe”, “%50”, “₺100”.
- **Türkçe karakterler ve ekler:** “iPhone’umu”, “oyuncağın”, “gelmeyeceğim”, “gözlüğünü”.
- **Çocuk-yönelimli prozodi:** kısa cümle, ritüel, tekrar, soru, şefkatli uyarı, heyecan, masal anlatımı.
- **Karakter sesi stabilitesi:** 5–10 dakikalık seans boyunca ses drift’i, pitch kayması, reference timbre kaybı.

### Bize özel öneri — Alan 1

1. **İlk sprintte VoxCPM2 + OmniVoice + XTTS-v2 baseline üçlüsünü kurun.** VoxCPM2 üretim-yakın aday; OmniVoice geniş dil ve hız adayı; XTTS-v2 Türkçe bilinen baseline. Fish S2 Pro yalnızca kalite tavanı olarak değerlendirilip lisans riski ayrı tutulmalı.
2. **Leaderboard değil, Neeko eval seti karar versin.** 100–200 cümlelik Türkçe çocuk-yönelimli test seti hazırlayın: oyun, uyku öncesi, duygu regülasyonu, soru-cevap, sayı/tarih/kısaltma, İngilizce marka-karışımı. Aynı referans sesle tüm modelleri koşturun.
3. **Ticari rota ile araştırma rotasını ayırın.** Ticari rota: VoxCPM2/OmniVoice. Araştırma rota: F5/X-Voice/Qwen3 mimarilerinden Türkçe frontend, IPA/G2P ve LoRA/fine-tune fikirleri devşirmek.

---

## Alan 5 — Düşük-latency streaming TTS

### TL;DR

NEEKO’nun interaktif oyuncak deneyiminde hedef yalnızca “ses üretmek” değil; çocuk beklerken boşluk yaratmadan, kesilebilir, doğal ve güvenli konuşabilmek. Bu nedenle TTFB hedefi <300 ms ise model seçimi kadar pipeline tasarımı da belirleyici. Gerçek streaming ile “metni chunk’lara bölüp tek-shot üretmek” aynı şey değildir. Neeko için ilk 6–9 ayda doğru mimari: bulutta sıcak GPU endpoint + persistent connection + erken cümle/phrase segmentation + cihazda küçük jitter buffer + interrupt/cancel desteği. Edge’de tam kaliteli Neeko sesi ilk aşamada gerçekçi değil; edge fallback daha küçük ve daha düşük kaliteli olmalıdır.

### Streaming adayları

| Model / framework | Streaming tipi | Kaynak performans sinyali | Türkçe | Lisans / ticari durum | Neeko fit’i |
|---|---|---:|---|---|---|
| **VoxCPM2** | Real-time streaming iddiası; vLLM/Nano-vLLM serving | RTX 4090’da RTF ~0.30; Nano-vLLM ile ~0.13; ~8 GB VRAM | **Var** | Apache-2.0 | **Birincil streaming deneyi** |
| **OmniVoice** | Hızlı NAR/diffusion LM tarzı; RTF çok düşük iddiası | RTF 0.025 | 600+ dil kapsamı | Apache-2.0 | İkinci deney; TTFB ve Türkçe kalite ölçülmeli |
| **Qwen3-TTS** | Dual-track text/audio streaming | 0.6B’de first-packet 97 ms; 1.7B’de 101 ms | Yok | Apache-2.0 | Streaming mimarisi referansı; Türkçe için fine-tune gerekir |
| **Fish S2 Pro** | SGLang serving, continuous batching, paged KV cache | H200’de TTFA ~100 ms, RTF ~0.195 | Var ama tier-1 değil | Ticari ayrı lisans | Kalite/latency tavanı; lisanslı değerlendirme |
| **NVIDIA Magpie-TTS** | Streaming checkpoint + Pipecat/WebSocket örnekleri | RTX 5090 pipeline P50 ~101 ms; DGX Spark pipeline P50 ~186 ms | Yok | NVIDIA Open Model License | Voice-agent streaming mimarisi için referans |
| **XTTS-v2** | Streaming inference | <200 ms latency iddiası | **Var** | CPML; ticari kısıtlı | Türkçe streaming baseline; production değil |
| **F5-TTS** | Chunk inference / TRT-LLM optimized serving | L20’de avg latency 253 ms, RTF 0.0394 | Doğrudan güçlü değil | Pretrained CC-BY-NC | NAR hız/fine-tune araştırması; gerçek streaming dikkatle test edilmeli |
| **Piper** | Local ONNX/VITS; raw audio pipe edilebilir | CPU/embedded hızlı; repo 2025’te arşivlenmiş | Türkçe voice var ama özel karakter issue’ları raporlanmış | MIT, voice lisansları değişebilir | Edge fallback, demo ve prototip; karakter kalitesi düşük |
| **Kokoro 82M** | Küçük model, chunk/streaming kullanılabilir | <2 GB VRAM/CPU dostu kaynaklar; 82M | Yok | Apache-2.0 | Edge latency sandbox; Türkçe için doğrudan uygun değil |
| **RealtimeTTS** | Model değil framework; generator/LLM token stream → audio | Çoklu engine, chunk callback, fallback | Engine’e bağlı | MIT | Orchestrator prototipi için kullanılabilir |

### Autoregressive, flow-matching ve diffusion streaming farkı

**Autoregressive codec/LLM modeller** ses token’larını sırayla ürettiği için teknik olarak streaming’e daha doğal oturur. Qwen3-TTS, Fish S2, Magpie ve bazı MOSS tarzı modeller bu aileye yakın. Avantajı ilk token/ilk frame üretiminin erken başlamasıdır. Dezavantajı uzun sekanslarda drift, tekrar, hata birikimi ve sampling hassasiyetidir.

**Flow-matching / diffusion / NAR modeller** kalite ve hızda güçlü olabilir; fakat çoğu pratikte önce metnin tamamını veya anlamlı bir chunk’ını ister. F5-TTS gibi modellerin chunk inference performansı çok iyi olabilir, ama bu her zaman “LLM ilk kelimeyi üretir üretmez audio başlasın” anlamına gelmez. Qwen3-TTS ve bazı yeni modeller bu açığı block-wise/causal decoder tasarımlarıyla kapatmaya çalışıyor.

**Tokenizer-free / hybrid diffusion autoregressive modeller** VoxCPM2 gibi yeni ailelerde görülüyor. Burada amaç discrete tokenizer’ın kalite kaybını azaltırken real-time serving’i korumak. Neeko açısından bu sınıf önemli çünkü Türkçe destek + ticari lisans + streaming dengesi şu an en iyi burada görünüyor.

### Neeko için önerilen streaming mimarisi

İlk üretim-yakın mimari edge’de tam TTS değil, **cloud TTS + edge playback** olmalı:

```text
LLM response stream
  -> clause/sentence segmenter
  -> Turkish text normalization + G2P/frontend
  -> TTS provider router
  -> streaming audio chunks
  -> device jitter buffer
  -> speaker playback
  -> interruption/cancel controller
```

Bu pipeline’da kritik kararlar:

1. **Model sıcak kalmalı.** Her request’te model yükleme veya speaker embedding çıkarma TTFB’yi öldürür. Neeko karakter embedding’i/process state’i cache’lenmeli.
2. **Persistent transport kullanılmalı.** WebSocket, HTTP/2 bidi stream veya WebRTC/DataChannel + audio transport tercih edilmeli. Klasik request/response WAV üretimi interaktif oyuncak için geç kalır.
3. **Text segmentation agresif ama semantik olmalı.** LLM’den gelen ilk tam cümleyi beklemek yerine virgül, ünlem, soru ve çocuk konuşması ritmine göre phrase-level segmentation yapılmalı. Ancak çok kısa chunk’lar prozodi kırılması yaratır; çocuk sesi için 0.8–2.5 sn’lik phrase’lar iyi başlangıç aralığıdır.
4. **Cihazda küçük jitter buffer olmalı.** İlk chunk geldiğinde çalma başlamalı; sonraki chunk’lar buffer’a dolmalı. Buffer hedefi 80–200 ms arası tutulabilir; ağ dalgalanması yüksekse adaptif olmalı.
5. **Interrupt/cancel birinci sınıf özellik olmalı.** Çocuk araya girerse model üretimi, transport ve playback aynı anda kesilebilmeli. Bu oyuncak deneyiminde kalite kadar önemlidir.

### TTFB bütçesi

<300 ms hedef için kaba ama uygulanabilir başlangıç bütçesi:

| Bileşen | Hedef |
|---|---:|
| LLM ilk anlamlı phrase | 50–120 ms |
| Text normalization / G2P | 5–20 ms |
| TTS first audio | 80–180 ms |
| Network + protocol overhead | 20–60 ms |
| Edge jitter buffer | 40–80 ms |
| **Toplam** | **195–380 ms** |

Bu tablo şunu gösterir: TTFB yalnız TTS modeliyle çözülmez. LLM’in ilk phrase üretimi gecikirse, model ne kadar hızlı olursa olsun çocuk bekler. Bu yüzden NEEKO dialog motoru, TTS’e uygun “erken konuşulabilir phrase” üretmek üzere tasarlanmalı.

### Provider abstraction önerisi

İlk benchmark ve production prototipi için TTS provider’ları tek bir arayüz arkasına alınmalı. Böylece VoxCPM2, OmniVoice, XTTS, Fish ve ileride vendor TTS aynı test koşullarında ölçülür.

```python
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

@dataclass(frozen=True)
class TtsRequest:
    text: str
    character_id: str
    language: str = "tr"
    style: str | None = None
    reference_audio_id: str | None = None
    sample_rate: int = 24000
    streaming: bool = True

@dataclass(frozen=True)
class AudioChunk:
    pcm: bytes
    sample_rate: int
    is_final: bool
    ttfb_ms: float | None = None

class TtsProvider(Protocol):
    async def synthesize_stream(self, request: TtsRequest) -> AsyncIterator[AudioChunk]:
        ...
```

Bu arayüzde `character_id` ayrı tutulmalı; çünkü Neeko v1, Neeko v2 ve yan karakterler ileride adapter/embedding/LoRA switching ile yönetilecek. `style` alanı “şefkatli”, “heyecanlı”, “uyku öncesi”, “fısıltı değil ama yumuşak” gibi çocuk-yönelimli prozodi kontrolüne ayrılmalı. `reference_audio_id` üretim ortamında key/value store’dan çekilen onaylı referans sese bağlanmalı; raw kullanıcı sesi veya yetkisiz sample asla doğrudan provider’a geçmemeli.

### İlk 30 günlük benchmark planı

**Amaç:** Hangi modelin Türkçe + çocuk-yönelimli + streaming + lisans açısından ana aday olacağını belirlemek.

**Modeller:** VoxCPM2, OmniVoice, XTTS-v2, F5-TTS, Fish S2 Pro eval, Kokoro/Piper fallback. Qwen3-TTS doğrudan Türkçe olmadığı için yalnız mimari/latency referansı olarak opsiyonel.

**Test seti:** 120 Türkçe cümle.

- 30 oyun/etkileşim cümlesi: “Hadi beraber bulalım!”, “Sence sıradaki hayvan hangisi?”
- 20 uyku/ritüel cümlesi: “Şimdi gözlerini kapatıp derin bir nefes alalım.”
- 20 güvenli uyarı: “Bunu bir büyüğünle birlikte yapalım.”
- 20 sayı/tarih/kısaltma: “23 Nisan’da ne kutlarız?”, “%50 indirim ne demek?”
- 20 duygu regülasyonu: “Kızgın olman normal, önce sakinleşelim.”
- 10 kod-karışımı / marka: “iPhone’unu annenin yanına bırakabilir misin?”

**Ölçümler:**

- TTFB p50/p95
- RTF p50/p95
- WER veya Whisper-WER benzeri anlaşılabilirlik proxy’si
- Türkçe normalization hata oranı
- Speaker similarity
- 5 kişilik yetişkin MOS/CMOS ön testi
- Çocuk-yönelimli prozodi rubric’i: şefkat, hız, abartı, monotonluk, korkutuculuk, dikkat çekicilik

**Karar eşiği:**

Bir model ana aday olabilmek için şu minimumları sağlamalı:

- Türkçe cümlelerin en az %95’inde anlaşılır telaffuz.
- Sayı/tarih/kısaltma hataları manuel frontend ile düzeltilebilir olmalı.
- TTFB p50 <300 ms, p95 <700 ms hedeflenmeli.
- Karakter sesi 5 dakikalık ardışık konuşmada belirgin drift yapmamalı.
- Lisans ticari kullanım, fine-tune ve output ownership açısından kabul edilebilir olmalı.

### Bize özel öneri — Alan 5

1. **Streaming deneyini VoxCPM2 ile başlatın; OmniVoice’u paralel ikinci aday yapın.** İkisi de lisans açısından daha temiz ve Türkçe/çok-dil kapsama açısından brief’teki birçok eski adaydan daha uygun.
2. **“Gerçek streaming” ve “chunked batch” ayrımını benchmark’ta ayrı ölçün.** TTFB, first meaningful audio, inter-chunk gap ve interruption latency ayrı metrik olmalı.
3. **Edge TTS’i ilk aşamada ana kalite yolu yapmayın.** Neeko cihazında ilk hedef düşük gecikmeli playback, jitter buffer, cancel ve offline fallback olsun. Tam Neeko-grade karakter sesi bulutta sıcak GPU endpoint’te çalışmalı.

---

## İlk teknik karar önerisi

**ADR taslağı:** “NEEKO-TTS-001 — İlk açık-ağırlıklı TTS backbone benchmark seti”

**Karar:** İlk 30 gün için birincil model adayı olarak VoxCPM2, ikinci aday olarak OmniVoice, Türkçe baseline olarak XTTS-v2, kalite tavanı olarak Fish S2 Pro, araştırma/fine-tune hattı olarak F5-TTS seçilecek.

**Gerekçe:** VoxCPM2 Türkçe + Apache-2.0 + streaming + voice cloning/design kriterlerini aynı anda karşılıyor. OmniVoice Apache-2.0 ve 600+ dil kapsamıyla güçlü alternatif. XTTS-v2 Türkçe baseline sağlıyor fakat ticari üretim için uygun değil. Fish S2 Pro leaderboard’da güçlü fakat lisans bağımlılığı yaratıyor. F5-TTS fine-tune ekosistemi güçlü fakat pretrained model lisansı ticari kullanımda uygun değil.

**Riskler:**

- VoxCPM2’nin Türkçe kalite iddiası çocuk-yönelimli domain’de doğrulanmamış olabilir.
- OmniVoice’ta Türkçe aksan/telaffuz ve voice design stabilitesi zayıf olabilir.
- Streaming benchmark vendor/model kartı iddialarından düşük çıkabilir.
- Türkçe text normalization/G2P katmanı yapılmadan hiçbir model kararlı ürün kalitesi vermez.
- Voice talent IP sözleşmeleri ve model output ownership hukuki olarak ayrı doğrulanmalı.

**Sonraki araştırma bloğu:** Alan 2 + Alan 3 + Alan 8. Yani Türkçe text frontend, speaker adaptation/LoRA/voice cloning ve multi-character/IP koruma. Bu blok doğrudan VoxCPM2/OmniVoice üzerinde uygulanacak ilk fine-tune ve karakter sesi sahipliği protokolünü belirlemeli.

---

## Kaynaklar

- Artificial Analysis — Text-to-Speech Leaderboard, Mayıs 2026. https://artificialanalysis.ai/text-to-speech/leaderboard
- TTS Arena V2 — Hugging Face Space, 2026. https://huggingface.co/spaces/TTS-AGI/TTS-Arena-V2
- VoxCPM2 GitHub / Hugging Face / Docs, Nisan–Mayıs 2026. https://github.com/OpenBMB/VoxCPM ; https://huggingface.co/openbmb/VoxCPM2 ; https://voxcpm.readthedocs.io/en/latest/models/voxcpm2.html
- OmniVoice GitHub / paper, Nisan 2026. https://github.com/k2-fsa/OmniVoice ; https://arxiv.org/abs/2604.00688
- Qwen3-TTS GitHub / Technical Report, Ocak 2026. https://github.com/QwenLM/Qwen3-TTS ; https://arxiv.org/html/2601.15621v1
- Fish Audio S2 Pro GitHub / Hugging Face / license notes, 2026. https://github.com/fishaudio/fish-speech ; https://huggingface.co/fishaudio/fish-speech-2.0 ; https://fish.audio/blog/fish-speech-2-release/
- F5-TTS GitHub / TRT-LLM benchmark, 2025–2026. https://github.com/SWivid/F5-TTS
- XTTS-v2 Hugging Face / Coqui docs / CPML, 2024–2026. https://huggingface.co/coqui/XTTS-v2 ; https://docs.coqui.ai/en/latest/models/xtts.html
- NVIDIA Magpie-TTS Hugging Face / docs / Daily.co voice-agent benchmark, 2026. https://huggingface.co/nvidia/magpie-tts-multilingual ; https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/tts/models.html ; https://www.daily.co/blog/building-voice-agents-with-nvidia-open-models/
- Mistral Voxtral TTS blog / Hugging Face / arXiv, 2026. https://mistral.ai/news/voxtral-tts ; https://huggingface.co/mistralai/Voxtral-TTS ; https://arxiv.org/abs/2604.01412
- Kokoro Hugging Face / GitHub, 2025–2026. https://huggingface.co/hexgrad/Kokoro-82M ; https://github.com/hexgrad/kokoro
- OpenVoice v2 GitHub / Hugging Face, 2024–2026. https://github.com/myshell-ai/OpenVoice ; https://huggingface.co/myshell-ai/OpenVoiceV2
- MeloTTS GitHub / Turkish support PR, 2024–2026. https://github.com/myshell-ai/MeloTTS ; https://github.com/myshell-ai/MeloTTS/pull/223
- ChatTTS GitHub, 2024–2026. https://github.com/2noise/ChatTTS
- Bark GitHub, 2023–2026. https://github.com/suno-ai/bark
- VALL-E X GitHub / Microsoft VALL-E page, 2023–2026. https://github.com/Plachtaa/VALL-E-X ; https://www.microsoft.com/en-us/research/project/vall-e-x/
- RealtimeTTS GitHub, 2026. https://github.com/KoljaB/RealtimeTTS
- Piper GitHub / samples, 2025–2026. https://github.com/rhasspy/piper ; https://rhasspy.github.io/piper-samples/
