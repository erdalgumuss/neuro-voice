# Ses Klonlama Sektör Patternleri - NQAI İçin Uygulamalı Not

**Tarih:** 2026-05-24  
**Kapsam:** Ses klonlama nasıl yapılıyor, sektör hangi ürün katmanlarına ayrılıyor, base model üstünde nasıl bir kurgu var, NQAI'nin "bol insan konuşturma" stratejisi bunu nasıl kullanmalı.  
**Durum:** Araştırma ve referans dokümanı. Mimari karar değildir; B1/B2 öncesi yön belirleme notudur.

---

## 0. Tek Cümlelik Çıkarım

Sektördeki ana pattern şudur: **büyük bir base speech model + kısa referanstan speaker conditioning + daha ciddi kalite için adapter/fine-tune/PVC + ayrı style/prosody kontrol katmanı + güçlü consent/eval/serving hattı**.

Yani "10 farklı studio kayıt yaptık, 11. kullanıcıyı hızlı nasıl konuşuruz?" sorusunun cevabı tek bir model değil:

1. Base model genel konuşma, dil, akustik ve prosodi kabiliyetini taşır.
2. Kısa kullanıcı kaydı speaker identity/timbre çıkarır.
3. Hazır style/prosody havuzu konuşma biçimini temizler.
4. Premium/kalıcı seslerde küçük adaptör, LoRA veya PVC benzeri fine-tune devreye girer.
5. Her şey voice registry, consent, eval, watermark ve worker serving katmanıyla ürünleşir.

---

## 1. LoRA Amatör Mü?

Kısa cevap: **Hayır. LoRA amatör değil; yanlış yerde tek çözüm sanılırsa amatör hissettirir.**

LoRA/adaptör yaklaşımı büyük base model çağında gayet profesyonel bir pattern. Hatta açık modellerde bunun açık adı LoRA, ticari ürünlerde ise çoğu zaman "professional clone", "custom model", "PVC", "fine-tuned clone" gibi ürün diliyle anlatılıyor. Cartesia, Professional Voice Clone'ları Sonic üstünde voice data ile fine-tune edilen klonlar olarak tarif ediyor. MiniMax-Speech paper'ı da base modeli değiştirmeden LoRA ile emotion control ve ek veriyle professional voice cloning yapılabildiğini açıkça söylüyor.

LoRA'nın güçlü olduğu yer:

- Özgün karakter sesi, yani Neeko gibi uzun vadeli IP sesi.
- 30 dakika ile 6 saat arası temiz, tek konuşmacılı veri.
- Sürümleme, rollback, karakter başına küçük ağırlık, aynı base model üstünde çok ses.
- Studio kalitesi ve aynı karakterin aylar boyunca aynı kalması.

LoRA'nın zayıf olduğu yer:

- Kullanıcı "hemen şimdi 30 saniyelik kaydımla konuşsun" dediğinde.
- Referans kaydı kirli, duygusu tutarsız veya ortam gürültülü olduğunda.
- Style/prosody kontrolü yoksa; model sadece sesi değil kayıt anındaki garip ritmi de kopyalamaya çalışır.
- Eval yoksa; "bana yakın geldi" ile production kalite yönetilemez.

Bizim doğru çerçevemiz:

| Katman | Kullanım | Teknik |
|---|---|---|
| Instant voice | Hızlı kullanıcı klonu, demo, audition | Reference encoder / speaker embedding / in-context prompt |
| Premium voice | Daha stabil kullanıcı/marka sesi | Speaker profile + kalite filtresi + gerekirse lightweight adapter |
| Character voice | Neeko, NIVA, NARO gibi IP sesi | Studio corpus + LoRA/adaptör + eval + registry |
| Base/domain SFT | Türkçe/çocuk/call-center genel kalite | Base model üstünde domain SFT veya daha büyük adaptation |

Bu yüzden "LoRA amatör mü?" değil, "LoRA'yı hangi katmanda kullanıyoruz?" sorusu doğru soru.

---

## 2. Sektördeki Ürün Patternleri

### Pattern A - Instant Voice Clone

Amaç: Kullanıcı kısa bir ses örneği verir, sistem hemen kullanılabilir bir ses ID'si döner.

Gözlenen ürün sinyalleri:

- ElevenLabs instant clone tarafında kısa kayıtlarla hızlı klon sunuyor; ürün sayfası instant/professional ayrımını açık yapıyor.
- Cartesia `/voices/clone` API'si yaklaşık 5 saniyelik clip ile high similarity voice clone oluşturmayı tarif ediyor ve response olarak doğrudan voice ID dönüyor.
- Resemble Rapid Clone için 10 saniye ile 3 dakika arası veri ve 1 dakikanın altında eğitim süresi veriyor.

Teknik karşılığı:

- Reference audio -> speaker encoder -> speaker embedding / timbre vector.
- Text + speaker embedding + style controls -> acoustic generation.
- Training yok veya kullanıcıya görünmeyecek kadar hafif bir embedding build var.
- Ses kimliği hızlı gelir ama uzun-form stabilite ve duygu aralığı sınırlı olabilir.

NQAI çıkarımı:

- Kullanıcı klonu için ilk ürün hattı LoRA olmamalı.
- Hızlı "11. kullanıcı" için `voice.kind = instant_clone` gibi bir kayıt gerekir.
- Bu yol referans kalitesine aşırı bağımlıdır: consent phrase, noise check, clipping check, speaker diarization, minimum duration şart.

### Pattern B - Professional Voice Clone / PVC

Amaç: Daha uzun ve temiz kayıtla, production kalitesinde, daha stabil ve daha duygulu ses üretmek.

Gözlenen ürün sinyalleri:

- ElevenLabs professional clone için instant'a göre daha uzun ve temiz kayıt ister; 30+ dakika minimum, 3 saat optimum gibi ürün dili kullanır.
- Resemble Professional Clone için 10-25+ dakika veri, yaklaşık 40 dakika eğitim ve full emotional range tarif ediyor.
- Cartesia PVC'leri Sonic üstünde voice data ile fine-tune edilen klonlar olarak anlatıyor.

Teknik karşılığı:

- Base model sabit kalır veya büyük ölçüde korunur.
- Speaker/timbre latenti, adapter, LoRA, küçük fine-tune veya voice-specific head eğitilir.
- Training async işler: upload -> validation -> build -> quality report -> voice ID ready.
- Serving tarafında adapter cache, voice registry, result quality state gerekir.

NQAI çıkarımı:

- Neeko/NIVA/NARO gibi sahiplenilecek sesler için doğru yol budur.
- Colab'daki 26 dakikalık Neeko LoRA tam olarak bu katmanın erken prototipidir; amatör olan LoRA değil, veri miktarı ve eval eksikliğidir.
- 3-6 saatlik stüdyo kayıt gerçekten fark yaratır: aynı karakterin farklı duygu/ritim/uzunluklarda sabit kalması için veri çeşitliliği gerekir.

### Pattern C - Voice Design / Style Prototype Pool

Amaç: Kullanıcı birebir birinin sesini klonlamadan, tarifle veya hazır profillerle yeni bir ses oluşturur.

Gözlenen ürün sinyalleri:

- ElevenLabs Voice Design ve voice library sunuyor.
- Resemble text description ile Voice Design yapıp birden fazla aday döndüğünü söylüyor.
- OpenVoice, referans speaker'ın tone color'ını alırken emotion, accent, rhythm, pauses ve intonation gibi style boyutlarını ayrı kontrol etmeyi hedefliyor.
- NaturalSpeech 3 speech'i content, prosody, timbre ve acoustic detail gibi alt uzaylara ayırmayı öneriyor.

Teknik karşılığı:

- "Kim konuşuyor?" ile "nasıl konuşuyor?" ayrılır.
- Speaker identity/timbre ayrı, style/prosody ayrı conditioning olur.
- Hazır stüdyo sesleri sadece "klonlanmış kişiler" değil, aynı zamanda style manifold'unu temizleyen referanslardır.

NQAI çıkarımı:

- "50 kişi kayıt edelim" demek 50 ayrı model kurmak değil.
- 50 temiz insan sesi, base modelin ve bizim registry'mizin style/timbre haritasını zenginleştiren ürün varlığıdır.
- 11. kullanıcı geldiğinde sistem onun timbre'ını alıp, konuşma biçimini bizim temiz style profillerimizden birine yaslayabilir.

### Pattern D - Voice Agent'a Doğru Kayma

TTS firmalarının yönü yalnız "text gir, wav al" değil; real-time voice agent:

- Düşük TTFB.
- Streaming.
- Turn-taking.
- STT + LLM + TTS + interruption handling.
- Voice identity + persona + tool use + monitoring.

Bu yüzden bizim worker/gateway ayrımı sadece maliyet optimizasyonu değil; voice agent yönüne geçişin altyapısıdır.

---

## 3. Base Model Üstünde Tipik Teknik Kurgu

Modern TTS/voice cloning stack'i kabaca şöyle okunabilir:

```text
Text
  -> Turkish text normalization / pronunciation hints
  -> semantic tokens or hidden text representation
  -> speaker/timbre conditioning from reference audio
  -> style/prosody conditioning
  -> acoustic token / latent generation
  -> decoder / vocoder
  -> watermark / safety / post-process
  -> streaming chunks
```

Farklı sistemler bu blokları farklı isimlendiriyor:

- VALL-E: speech'i neural codec discrete code'larına çevirip TTS'i conditional language modeling görevi gibi ele alıyor; 3 saniyelik acoustic prompt ile unseen speaker için kişisel speech üretebildiğini gösteriyor.
- Voicebox: audio context + text ile infilling ve zero-shot TTS yapıyor; future context de kullanabilen non-autoregressive flow matching yapısı var.
- BASE TTS: 1B parameter autoregressive Transformer + speechcodes + streamable decoder; speaker ID disentanglement sinyali önemli.
- CosyVoice: supervised semantic tokens + LLM + conditional flow matching ile content consistency ve speaker similarity'yi artırmayı hedefliyor.
- F5-TTS: flow matching + Diffusion Transformer ile daha basit, non-autoregressive ve hızlı zero-shot TTS hattı kuruyor.
- MiniMax-Speech: learnable speaker encoder ile transkriptsiz referans audiodan timbre features çıkarıyor; base model değişmeden LoRA emotion control ve PVC için timbre feature fine-tune gibi extensibility anlatıyor.
- NaturalSpeech 3: content/prosody/timbre/acoustic detail ayrımını explicit hale getiriyor.
- VoxCPM2: bizim seçtiğimiz açık base model; official model card full SFT ve LoRA fine-tuning'i desteklediğini, 5-10 dakika audio ile adaptation yapılabildiğini söylüyor. Bu production garantisi değil; hızlı adaptation sinyalidir.

Pratik çeviri:

- **Speaker encoder** hızlı klon için şart.
- **Style/prosody ayrımı** ElevenLabs hissini yakalamak için şart.
- **LoRA/adaptör** kalıcı karakter seslerinde doğru.
- **Text frontend** Türkçe kalite için modelden bağımsız en yüksek kaldıraç.
- **Streaming worker** ürün deneyimi ve maliyet için şart.

---

## 4. "Bize Bol Bol İnsan Lazım" Ne Demek?

Evet, ama "çok insan" rastgele veri demek değil. Bu işte veri sadece saat değil; **kapsam haritası**.

### 4.1 Kayıt Havuzu Rolleri

| Veri tipi | Amaç | Minimum | İdeal |
|---|---|---:|---:|
| Character hero voice | Neeko gibi ana IP sesi | 1-3 saat | 5-6 saat |
| Secondary character voice | Yan karakter/masal sesi | 30-60 dk | 2-3 saat |
| Style prototype | Sakin, neşeli, öğretici, call-center, masal | 15-30 dk | 1 saat |
| Instant clone eval speakers | Hızlı klon kalibrasyonu | 20-50 kişi x 3-10 dk | 100 kişi x 10 dk |
| Domain corpus | Türkçe çocuk/call-center/eğitim text-prosody | 10-30 saat | 100+ saat |

### 4.2 İnsanları Ne İçin Konuşturacağız?

Sadece "ses benzesin" diye değil:

- Türkçe fonetik kapsama: ünlü uyumu, yumuşama, özel isim, sayı, para, tarih, yaş, kısaltma.
- Prosody kapsama: fısıltıya yaklaşmadan sakin, neşeli, meraklı, şaşkın, uyarıcı, yatıştırıcı.
- Domain kapsama: çocukla konuşma, call-center, eğitmen anlatımı, kısa komut, uzun masal.
- Uzun-form stabilite: 5-10 dakika metinde drift, yoruculuk, tizleşme/kalınlaşma kontrolü.
- Cross-style transfer: aynı timbre ile farklı konuşma biçimi.
- Eval seti: modeli eğittiğimiz cümlelerden ayrı, gizli tutulan test cümleleri.

### 4.3 Kayıt Script'i Nasıl Olmalı?

Her voice talent için script bölünmeli:

1. **Neutral phonetically rich:** Türkçe ses kapsaması ve temel telaffuz.
2. **Child-directed warm:** Neeko alanı.
3. **Storytelling:** uzun cümle, duraklama, ritim.
4. **Instructional:** kısa, net, emir-komut değil rehber ton.
5. **Emotion micro-set:** sevinç, merak, korku yatıştırma, özür, kutlama.
6. **Numbers/entities:** tarih, saat, para, yüzdeler, marka/ürün isimleri.
7. **Code-switch:** İngilizce/Türkçe marka ve teknoloji kelimeleri.
8. **Reserve eval:** hiç eğitime sokulmayacak gizli bölüm.

---

## 5. NQAI İçin Önerilen Ürün Katmanları

### Katman 1 - Instant Clone

Kullanıcı veya voice talent 30 saniye-3 dakika arası kayıt verir.

Beklenen sistem:

- Upload.
- Consent phrase ve legal flag.
- Audio quality check.
- Speaker diarization / tek konuşmacı kontrolü.
- Reference embedding veya reference bundle.
- Hemen kullanılabilir `voice_id`.
- Quality label: `draft`, `usable`, `needs_better_audio`.

Bu katmanda LoRA beklememeliyiz. Hızlı ve ucuz olmalı.

### Katman 2 - Premium Clone

10-60 dakika temiz kayıt.

Beklenen sistem:

- Async training job.
- Lightweight adapter veya timbre feature fine-tune.
- Webhook/callback.
- Quality report.
- Voice ID stable.

Bu katmanda LoRA/adaptör gayet mantıklı. Müşteriye "eğitim tamamlandı" deneyimi verilir.

### Katman 3 - Character Voice

Neeko gibi şirket IP'si.

Beklenen sistem:

- 3-6 saat studio corpus.
- Voice talent rider + model/output ownership.
- Neeko persona/style spec.
- LoRA/adaptör ablation: 30 dk / 1 saat / 3 saat / 6 saat.
- Human panel + objective metrics.
- Versiyonlu adapter registry.
- Rollback.
- Watermark/provenance.

Burada kalite hedefi instant clone değil, "aylarca aynı karakter" olmalı.

---

## 6. Repo ve Mimari İçin Pratik Çıkarımlar

Bu doküman mimari karar değil, ama B1/B2 işleri için doğru yönü gösteriyor.

### 6.1 Voice Registry Alanları

Bugünkü voice record ileride şu kavramları taşımalı:

```yaml
voice_id: neeko-v1
owner_tenant_id: ...
visibility: private | shared | public
kind: reference_clone | instant_clone | premium_clone | character_adapter
base_model_id: openbmb/VoxCPM2
reference_uri: r2://...
reference_quality:
  seconds: 42.5
  sample_rate: 16000
  noise_score: ...
  single_speaker: true
style_profile_id: child_warm_storyteller_v1
adapter:
  type: none | lora | timbre_adapter | full_sft
  uri: r2://...
  version: neeko-lora-2026-05-24-step0300
quality_status: draft | eval_passed | production | deprecated
consent:
  talent_id: ...
  contract_uri: r2://...
  commercial_use: true
  cloning_allowed: true
```

Not: B1'de hepsini yazmak gerekmiyor. Ama schema büyüyecek yer belli olmalı.

### 6.2 Worker Hattı

Worker iki üretim yolunu desteklemeli:

- `reference_only`: base model + reference audio/embedding.
- `adapter`: base model + LoRA/adaptör + optional reference/style.

Bu ayrım API'de müşteriye fazla gösterilmemeli. Müşteri sadece `voice_id` verir. İçeride registry doğru inference path'i seçer.

### 6.3 Cache ve Serving

Çok sesli sistemde pahalı şeyler:

- Base model cold start.
- Adapter load/unload.
- Reference preprocessing.
- Long-form generation.

Bu yüzden:

- Base model worker'da sıcak kalmalı.
- Adapter LRU cache devam etmeli.
- Reference audio R2 -> local cache deterministik olmalı.
- Instant clone embedding/cache ileride R2/Redis/DB'de saklanmalı.
- Queue job payload sadece `voice_id`, `text`, `style_overrides`, `request_id` taşımalı; engine detayını gateway bilmemeli.

---

## 7. LoRA İçin NQAI Kalite Reçetesi

LoRA'yı daha profesyonel hissettiren şey model değil, süreçtir:

1. **Veri temizlik kapısı:** clipping, noise, silence, speaker purity.
2. **Transcript doğruluğu:** ASR çıktısı yetmez; high-value kayıtlar insan review görmeli.
3. **Ablation:** 10 dk, 30 dk, 1 saat, 3 saat, 6 saat ayrı run.
4. **Sabit eval seti:** aynı 100-150 cümle ile her model kıyaslanmalı.
5. **Subjective panel:** Erdal tek kulak değil; 3-5 kişi MOS/CMOS.
6. **Long-form eval:** 20 saniye değil, 3-5 dakika masal/konuşma.
7. **Style stress test:** sakin, meraklı, sevinçli, uyarıcı, özür.
8. **Drift ölçümü:** pitch kalınlaşıyor/inceliyor mu, speed kayıyor mu, timbre değişiyor mu.
9. **Rollback:** her adapter version artifact olarak saklanmalı.
10. **Serving metric:** RTF, TTFB, error rate, retry_badcase oranı.

Colab'da yaptığımız 26 dakikalık Neeko run iyi bir sinyal verdi ama production kararı değil. 5-6 saat kayıt bu yüzden gerçekten fark yaratır: LoRA'nın "karaktere oturması" için modelin aynı sesi birçok duygu ve cümle yapısında görmesi gerekir.

---

## 8. 50 Ses Stratejisi

"50 tane temiz klonlanmış ses konuşma yapısı hazırla" cümlesini şöyle düzeltmek daha doğru:

**50 ayrı model değil; 50 temiz insan sesi + 10-15 style profile + bunları bağlayan registry/eval sistemi.**

Başlangıç grid'i:

| Boyut | Örnek kapsama |
|---|---|
| Timbre | ince, orta, kalın; parlak, sıcak, mat; genç/yetişkin/olgun |
| Cinsiyet algısı | kadın, erkek, androgynous/neutral |
| Enerji | sakin, orta, yüksek |
| Domain | çocuk, call-center, eğitim, masal, reklam, karakter |
| Duygu | neşe, merak, güven, sakinleştirme, özür, uyarı |
| Dil | Türkçe merkez; sınırlı İngilizce code-switch |

Bu havuzun faydası:

- User clone geldiğinde onun sesi "çıplak klon" kalmaz; temiz konuşma biçimine yaslanır.
- Style transfer ve voice design için referans uzayı oluşur.
- Neeko dışı ürünler aynı platformdan doğar.
- Eval paneli için insan baseline oluşur.
- İleride kendi base/domain SFT için lisanslı veri varlığı birikir.

---

## 9. Bize Düşen Yakın Dönem İşleri

B1 worker'a geçmeden bu dokümandan çıkan pratik research backlog:

1. **Clone taxonomy ekle:** `instant_clone`, `premium_clone`, `character_adapter` kavramlarını docs/architecture'a yansıt.
2. **Neeko v1 kayıt planını netleştir:** 5-6 saat hedef, script segmentleri, reserve eval seti.
3. **Ablation planı hazırla:** 30 dk / 1 saat / 3 saat / 6 saat LoRA karşılaştırması.
4. **Instant clone baseline çıkar:** VoxCPM2 reference-only, OpenVoice/F5-TTS/CosyVoice local baseline, Cartesia/Eleven/Resemble vendor benchmark.
5. **Style profile tasarla:** `child_warm_storyteller`, `calm_guardian`, `curious_friend`, `callcenter_clear`, `teacher_paced`.
6. **Eval harness kur:** aynı metin, aynı voice, aynı model params, wav output, MOS sheet, objective metrics.
7. **Consent/IP paketini kapat:** voice talent rider içinde dataset, embedding, adapter, output ve model weight hakları ayrı yazılmalı.
8. **Watermark/provenance kararını erteleme:** AudioSeal gibi yöntemler production gate olmasa bile plan içinde durmalı.

---

## 10. Kaynak Notları

### Vendor kaynakları

- [ElevenLabs Voice Cloning](https://elevenlabs.io/voice-cloning) - instant/professional clone ayrımı, IVC/PVC ürün dili, 1-5 dakika instant, 30+ dakika professional, 3 saat optimum kayıt önerisi.
- [ElevenLabs Voice Cloning Docs](https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning) - voice cloning ürün dokümantasyonu ve IVC/PVC alt sayfaları.
- [Cartesia Clone Voice API](https://docs.cartesia.ai/api-reference/voices/clone) - yaklaşık 5 saniyelik clip ile clone endpoint'i, `voice_id` dönen API yüzeyi.
- [Cartesia Professional Voice Cloning](https://cartesia.ai/blog/pro-voice-cloning) - Sonic üstünde fine-tuned Professional Voice Clone yaklaşımı, self-serve PVC ürünü.
- [Resemble Clone a Voice Overview](https://docs.resemble.ai/voice-creation/voices/clone-overview) - Rapid Clone ve Professional Clone ayrımı; 10 saniye-3 dakika rapid, 10-25+ dakika professional, async build/webhook akışı.
- [Resemble Voice Creation](https://www.resemble.ai/products/voice-creation) - voice design, professional clone, multi-language clone, consent ve deployment seçenekleri.

### Araştırma kaynakları

- [VALL-E: Neural Codec Language Models are Zero-Shot Text to Speech Synthesizers](https://arxiv.org/abs/2301.02111) - 3 saniyelik acoustic prompt ile zero-shot personalized speech fikrini popülerleştiren temel paper.
- [Voicebox: Text-Guided Multilingual Universal Speech Generation at Scale](https://arxiv.org/abs/2306.15687) - flow matching, speech infilling, zero-shot TTS ve style conversion için ölçekli yaklaşım.
- [OpenVoice: Versatile Instant Voice Cloning](https://arxiv.org/abs/2312.01479) - short audio clip ile timbre clone, style kontrolünü timbre'dan ayırma, cross-lingual clone.
- [NaturalSpeech 3](https://arxiv.org/abs/2403.03100) - content/prosody/timbre/acoustic detail factorization; bizim style/timbre ayrımı için iyi referans.
- [BASE TTS](https://arxiv.org/abs/2402.08093) - 1B parametre, 100K saat veri, speechcodes ve streamable decoder; ölçek ve veri miktarının doğal prosodiye etkisi.
- [CosyVoice](https://arxiv.org/abs/2407.05407) - supervised semantic tokens + LLM + conditional flow matching; zero-shot speaker similarity/content consistency.
- [F5-TTS](https://arxiv.org/abs/2410.06885) - flow matching + DiT, non-autoregressive, hızlı zero-shot TTS ve multilingual/code-switch sinyali.
- [MiniMax-Speech](https://arxiv.org/abs/2505.07916) - learnable speaker encoder, zero-shot/one-shot cloning, LoRA emotion control ve PVC için timbre feature fine-tune yaklaşımı.
- [VoxCPM2 model card](https://huggingface.co/openbmb/VoxCPM2) - NQAI base model; LoRA ve full SFT support, 5-10 dakika audio ile adaptation iddiası.
- [VoxCPM2 Fine-Tuning Guide](https://voxcpm.readthedocs.io/en/latest/finetuning/finetune.html) - LoRA/full fine-tune operasyonel referansı.
- [AudioSeal paper](https://arxiv.org/abs/2401.17264) ve [AudioSeal repo](https://github.com/facebookresearch/audioseal) - AI-generated speech için localized watermarking; provenance/safety katmanı için referans.

---

## 11. Son Karar Değil, Net Yön

Bu dokümandan çıkan yön net:

- Hızlı kullanıcı klonu için **reference/speaker encoder hattı** gerekir.
- Kalıcı karakter sesi için **LoRA/adaptör/PVC hattı** gerekir.
- Çok sayıda insan kaydı, modeli "kalabalıklaştırmak" için değil, **style/timbre/prosody uzayını temiz ve lisanslı doldurmak** için gerekir.
- NQAI'nin avantajı genel TTS yarışı değil; Türkçe, çocuk/call-center/eğitim domain'i, karakter tutarlılığı ve sahipli ses varlıklarıdır.

LoRA bu hikayede amatör değil. LoRA bizim karakter belleğimiz. Ama instant clone, style control, eval ve serving olmadan tek başına ürün değildir.
