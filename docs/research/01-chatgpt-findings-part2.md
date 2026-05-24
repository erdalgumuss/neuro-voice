# 01-chatgpt-findings-part2 — Türkçe TTS Frontend, Speaker Adaptation ve Karakter/IP Katmanı

**Kapsam:** Alan 2 + Alan 3 + Alan 8  
**Tarih:** 2026-05-19  
**Bağlam:** NEEKO için Türkçe, çocuk-yönelimli, karakter-merkezli TTS yığını. Bu parça, ilk çıktıdaki model/streaming kararını tamamlayarak “Neeko sesi nasıl kontrol edilir, sahiplenilir, çoğaltılır ve korunur?” sorusuna odaklanır.

---

## Yönetici kararı

Bu blokta en yüksek kaldıraç, tek bir model seçmekten çok **modelden bağımsız Türkçe text frontend + karakter adapter registry + hukuki/teknik ses sahipliği hattı** kurmaktır. Türkçe TTS hatalarının önemli bir kısmı modelin akustik kalitesinden değil; sayı, tarih, kısaltma, yabancı marka, özel isim, ek/apostrof ve kod-karışımı gibi metin girişlerinin yanlış verbalize edilmesinden doğar. Bu nedenle NEEKO’nun çekirdek varlığı yalnızca “fine-tune edilmiş model” değil, aşağıdaki üç katman olmalıdır:

1. **`neeko-tts-frontend`**: Türkçe text normalization, semiotic token expansion, özel isim/marka sözlüğü, kod-karışımı işaretleme, opsiyonel G2P/prosody hint üreten deterministic ve testli frontend.
2. **`voice-adapter-registry`**: Her karakter için base model, LoRA/adaptor, referans kayıtlar, veri seti, kontrat, watermark key, eval sonucu ve sürüm manifestini tutan sistem.
3. **`voice-governance-layer`**: Ses yeteneği sözleşmesi, açık rıza, KVKK/FSEK uyumu, cross-border GPU işleme kontrolü, watermark/fingerprint, erişim kontrolü ve audit log.

Önerilen üretim rotası: **VoxCPM2 ana model + Türkçe frontend + Neeko LoRA/adaptor + karakter manifest registry**. XTTS-v2/OpenVoice/F5-TTS bu blokta üretim backbone’u değil, zero-shot audition, regresyon baseline ve araştırma referansı olarak kullanılmalı. Neeko sesi için “5–30 saniyelik referansla clone” ürün demosuna yeter; ama 12 aylık IP ve tutarlılık hedefi için **en az 30 dakika, idealde 1–3 saat profesyonel, sözleşmeli, çocuk-yönelimli karakter kaydı** gerekir.

---

## Alan 2 — Türkçe G2P ve text frontend

### TL;DR

Türkçe için asıl problem klasik anlamda G2P değil; **text normalization + semiotic token expansion + kod-karışımı + özel isim/marka telaffuzu** problemidir. Türkçe yazı-ses ilişkisi İngilizce’ye göre daha düzenli olduğu için çoğu modern multilingual TTS modeli düz Türkçe metni kabul edebilir; fakat “%50”, “23 Nisan”, “1.234 TL”, “Dr.”, “iPhone’umu”, “Neeko’nun”, “3+ yaş” gibi girişler test edilmeden bırakılırsa kalite hızlı düşer. Bu yüzden NEEKO’nun v1 frontend’i modelden bağımsız, deterministik, birim-testli ve auditable olmalıdır. eSpeak NG/Phonemizer hızlı baseline ve debug aracı; Zemberek/num2words/TRNorm benzeri araçlar bileşen; asıl ürün değeri NEEKO’ya özel normalizer ve telaffuz sözlüğüdür.

### Özet

Türkçe TTS frontend’i iki ayrı problemi karıştırmadan çözmelidir. Birincisi, **normalization/verbalization**: yazılı metni konuşulacak forma çevirmek. İkincisi, **pronunciation/G2P**: konuşulacak formu fonem veya modelin beklediği sembollere çevirmek. NEEKO bağlamında birinci problem daha kritiktir. Çünkü çocuk oyuncağı konuşmasında sistem genellikle kısa, ritüel odaklı, eğitsel ve güvenli metinler üretecek; bunların içinde sayılar, yaş etiketleri, tarih, para birimi, yüzdelik ifade, marka, karakter adı, İngilizce terim ve kısaltma bulunacaktır. Model akustik olarak iyi olsa bile yanlış normalizasyon “çocuğun anlayamadığı” veya “ebeveynin güvenmediği” çıktı üretir.

Türkçe, görece düzenli bir ortografiye sahip olduğu için tam IPA tabanlı G2P, v1’de en yüksek kaldıraç değildir. Daha önemli olan şey, **girdinin konuşma formunu doğru ve tutarlı üretmek** ve model destekliyorsa bunu düz metin olarak vermektir. Yani frontend’in ilk hedefi “Türkçe IPA üretmek” değil; `"1.234.567 TL" -> "bir milyon iki yüz otuz dört bin beş yüz altmış yedi Türk lirası"`, `"Dr. Ayşe" -> "doktor Ayşe"`, `"%50" -> "yüzde elli"`, `"3+ yaş" -> "üç yaş ve üzeri"` gibi dönüşümleri deterministik yapmaktır. Bu çıktı daha sonra VoxCPM2 gibi tokenizer-free/multilingual bir modele düz metin olarak verilebilir; fonemleşme gerekirse backend’e opsiyonel katman olarak eklenir.

Türkçe vurgu, model fine-tune ve veri tasarımıyla birlikte düşünülmelidir. Genel kural birçok sözcükte son hece vurgusudur; fakat yer adları, bazı yabancı kökenli kelimeler, özel adlar, zarf/sonek yapıları ve istisnalar vardır. V1’de elle tüm vurgu sistemini kodlamak yerine, belirli çocuk-yönelimli prompt türleri için **prosody tag + örnek veri + eval** yaklaşımı daha iyi sonuç verir. “Neeko bugün çok heyecanlı!”, “Hadi birlikte sayalım!”, “Uyku vakti geldi.” gibi domain cümlelerinde vurgu ve ritim, G2P motorundan çok kayıt yönlendirmesi ve adaptation verisiyle yakalanmalıdır.

### Araç karşılaştırması

| Araç / yaklaşım | Kullanım rolü | Güçlü taraf | Risk / sınırlama | NEEKO kararı |
|---|---:|---|---|---|
| Custom Turkish TN/WFST + test suite | Ana production frontend | Deterministik, auditable, çocuk güvenliği için kontrol edilebilir | İlk geliştirme maliyeti var | **Zorunlu çekirdek bileşen** |
| NVIDIA NeMo text processing / WFST yaklaşımı | Mimari referans, bazı bileşenler | TN/ITN için olgun WFST yaklaşımı, Apache-2.0 | Hazır Türkçe grammar bulgusu sınırlı; Türkçe kurallar yazılmalı | **Tasarım şablonu olarak kullan** |
| Zemberek / Zeyrek | Tokenization, morphology, noisy text normalization, deasciification | Türkçe NLP için güçlü altyapı | TTS verbalizer değildir | **Frontend’e yardımcı modül** |
| num2words `tr` | Sayı, ordinal, currency başlangıcı | Türkçe dahil çok dil destekli; hızlı prototip | Tarih, bağlam, çocuk üslubu ve Türkçe özel durumlar için tek başına yetmez | **Wrapper + golden test ile kullan** |
| TRNorm | Türkçe normalization bileşeni | Türkçe sayı/ordinal/sembol standardizasyonuna odaklı | ASR evaluation odaklı olabilir; TTS konuşma formu için doğrulanmalı | **A/B bileşen olarak test et** |
| eSpeak NG | Baseline phonemizer / offline fallback | Türkçe dahil çok dil desteği; IPA/debug üretimi | Ses kalitesi değil; GPL lisans dikkati; vurgu/özel isim sınırlı | **Debug/baseline; production kararını lisansla incele** |
| Phonemizer | eSpeak/segments backend wrapper | IPA ve custom mapping için pratik | Kalite backend’e bağlı | **G2P deney harness’i** |
| CharsiuG2P | Çok dilli neural G2P karşılaştırması | 100 dil için transformer tabanlı G2P | Türkçe domain doğrulaması şart; production determinism zayıf | **Regresyon/probing aracı** |
| MaryTTS Turkish | Legacy referans | Türkçe modül geçmişi var | Modern neural TTS kalitesi beklenmemeli | **Kaynak/karşılaştırma; ana rota değil** |

### Önerilen frontend mimarisi

Frontend, TTS modelinden bağımsız bir Python paket veya mikroservis olarak tasarlanmalı. Model değiştirilse bile Neeko’nun dil mantığı korunmalıdır.

```text
Raw text
  -> Unicode/NFKC normalization
  -> punctuation and whitespace normalization
  -> sentence segmentation
  -> token/span extraction
  -> language/code-mix tagging
  -> semiotic class detection
       number, ordinal, date, time, currency, percent,
       abbreviation, unit, emoji/symbol, age label, URL/email if any
  -> Turkish verbalization
  -> custom lexicon / pronunciation hints
  -> child-directed style hints
  -> output contract
       normalized_text,
       spans,
       warnings,
       pronunciation_hints,
       style_tags,
       safety_flags
```

Örnek çıktı sözleşmesi:

```json
{
  "input": "Dr. Ayşe, 23 Nisan'da %50 indirimli 3+ yaş setini anlattı.",
  "normalized_text": "Doktor Ayşe, yirmi üç Nisan'da yüzde elli indirimli üç yaş ve üzeri setini anlattı.",
  "language_spans": [
    {"text": "Doktor Ayşe", "lang": "tr"}
  ],
  "semiotic_spans": [
    {"raw": "Dr.", "class": "abbreviation", "spoken": "Doktor"},
    {"raw": "23 Nisan", "class": "date", "spoken": "yirmi üç Nisan"},
    {"raw": "%50", "class": "percent", "spoken": "yüzde elli"},
    {"raw": "3+ yaş", "class": "age_label", "spoken": "üç yaş ve üzeri"}
  ],
  "style_tags": ["child_directed", "clear", "warm"],
  "warnings": []
}
```

Bu sözleşme iki sebeple önemlidir. Birincisi, TTS modelinin ürettiği hatayı frontend hatasından ayırmayı sağlar. İkincisi, ileride eval pipeline’da “hangi semiotic class daha çok hata üretiyor?” sorusunu ölçülebilir yapar.

### Türkçe semiotic sınıflar için minimum kapsam

| Sınıf | Örnek | Konuşma formu hedefi | Not |
|---|---|---|---|
| Cardinal number | `1.234.567` | `bir milyon iki yüz otuz dört bin beş yüz altmış yedi` | Nokta/binlik ve ondalık ayrımı bağlama bağlı test edilmeli |
| Ordinal | `1.`, `23.` | `birinci`, `yirmi üçüncü` | Tarihte gün ile ordinal farklı olabilir |
| Date | `23 Nisan 2026` | `yirmi üç Nisan iki bin yirmi altı` | Çocuk içeriğinde bayram/tarih sık geçer |
| Time | `14:30` | `on dört otuz` veya `saat iki buçuk` | Üslup bağlama göre seçilmeli |
| Currency | `₺99`, `99 TL` | `doksan dokuz Türk lirası` | Çocuk içeriğinde az kullanılmalı ama backend bilmeli |
| Percent | `%50` | `yüzde elli` | Eğitsel içerikte sık olabilir |
| Age label | `3+`, `3-7 yaş` | `üç yaş ve üzeri`, `üç ile yedi yaş arası` | Ürün içeriklerinde kritik |
| Abbreviation | `Dr.`, `Sn.`, `TBMM`, `AB` | `doktor`, `sayın`, `Türkiye Büyük Millet Meclisi`, `Avrupa Birliği` | Harf harf mi açılım mı karar sözlüğü ister |
| Units | `5 cm`, `2 kg` | `beş santimetre`, `iki kilogram` | Eğitsel oyunlarda gerekebilir |
| Proper noun + suffix | `Neeko'nun`, `Ayşe'ye` | doğru kök + ek akışı | Türkçe apostrof ve ek ayrımı korunmalı |
| Code-mix | `iPhone'umu`, `Bluetooth'u` | marka telaffuz sözlüğü + Türkçe ek | Kritik hata kaynağı |

### G2P ve telaffuz stratejisi

NEEKO için v1 stratejisi şu olmalı:

- **Primary path:** Modelin desteklediği Türkçe düz metin. Frontend yalnızca konuşma formunu düzeltir.
- **Secondary path:** Özel isim, marka ve yabancı kelimeler için pronunciation lexicon. Örneğin `Neeko`, `Niko` gibi karışabilecek isimler; `iPhone`, `Bluetooth`, `Spider-Man`, karakter adları.
- **Debug path:** eSpeak NG/Phonemizer/CharsiuG2P ile IPA veya phoneme karşılaştırması. Bu, production çıktısı değil; hata teşhisi ve regression için kullanılır.
- **Future path:** Model fine-tune sırasında phoneme-aware input desteklenirse, Turkish phoneme layer eklenebilir. Ancak bu katman, normalizer’dan sonra gelmelidir.

Bu ayrım önemlidir; çünkü phoneme layer kötü normalizasyonu kurtarmaz. `"23 Nisan"` doğru verbalize edilmemişse G2P yalnızca yanlış girdiyi daha tutarlı şekilde seslendirir.

### Test tasarımı

Frontend için ilk 2 haftada en az 300 golden test yazılmalı. Bunlar statik unit test gibi koşmalı ve her model karşılaştırmasının parçası olmalıdır.

Önerilen başlangıç seti:

| Kategori | Minimum örnek sayısı | Örnek |
|---|---:|---|
| Sayı ve ordinal | 50 | `0`, `7`, `10`, `101`, `1.001`, `23.` |
| Tarih/saat | 40 | `23 Nisan`, `29 Ekim 2026`, `14:30` |
| Para/yüzde/birim | 40 | `%50`, `₺99`, `5 cm`, `2 kg` |
| Kısaltma | 50 | `Dr.`, `Av.`, `Sn.`, `TBMM`, `MEB`, `AB` |
| Çocuk ürün dili | 40 | `3+ yaş`, `3-7 yaş`, `uyku vakti`, `hadi sayalım` |
| Özel isim + ek | 40 | `Neeko'nun`, `Ayşe'ye`, `Ankara'da` |
| Kod-karışımı | 40 | `iPhone'umu`, `Bluetooth'u`, `Spider-Man'i` |
| Güvenlik/noisy input | 30 | emoji, fazla boşluk, hatalı noktalama, all-caps |

### Kaynaklı bulgular

- eSpeak NG, 100+ dili destekleyen açık kaynak TTS/phonemization altyapısıdır; Turkish voice `trk tr Turkish` olarak listelenir. Kaynak: eSpeak NG GitHub ve languages docs, 2026 erişim. https://github.com/espeak-ng/espeak-ng
- Phonemizer; eSpeak, eSpeak-MBROLA, Festival ve custom `segments` backend’leriyle çok dilli phonemization yapabilir; eSpeak backend’i IPA üretir. Kaynak: Phonemizer docs, 2026 erişim. https://bootphon.github.io/phonemizer/
- CharsiuG2P, ByT5 tabanlı multilingual G2P yaklaşımıyla 100 dil için pronunciation prediction sunar. Kaynak: CharsiuG2P GitHub / Interspeech 2022, 2026 erişim. https://github.com/lingjzhu/CharsiuG2P
- NeMo text processing, TTS öncesi written-to-spoken text normalization ve inverse text normalization için WFST tabanlı bir paket olarak ayrıştırılmıştır; lisansı Apache-2.0’dır. Kaynak: NVIDIA NeMo-text-processing docs/GitHub, 2026 erişim. https://github.com/NVIDIA/NeMo-text-processing
- `num2words`, Türkçe (`tr`) dahil birçok dil için cardinal, ordinal, year ve currency dönüşümleri destekleyen LGPL-2.1 lisanslı bir pakettir. Kaynak: num2words GitHub, 2026 erişim. https://github.com/savoirfairelinux/num2words
- Zemberek, Türkçe tokenization, morphological analysis, disambiguation, word generation, sentence boundary detection ve normalization bileşenleri sunar; ancak TTS verbalization motoru değildir. Kaynak: Zemberek GitHub, 2026 erişim. https://github.com/ahmetaa/zemberek-nlp
- fastText language identification modelleri 176 dili tanır; CLD3 karakter n-gram tabanlı neural language identification yaklaşımı sunar. Kaynaklar: fastText docs ve Google CLD3 GitHub, 2026 erişim. https://fasttext.cc/docs/en/language-identification.html / https://github.com/google/cld3

### Bize özel öneri

1. **V1’de G2P’ye değil, Turkish TN’ye yatırım yapın.** İlk sprintte `neeko-tts-frontend` paketini kurun; 300+ golden testle sayı, tarih, yaş etiketi, kısaltma, özel isim ve kod-karışımı dönüşümlerini sabitleyin. Bu paket modelden bağımsız kalmalı.
2. **Fonem motorlarını production oracle gibi kullanmayın.** eSpeak/Phonemizer/CharsiuG2P yalnızca baseline, debug ve regression karşılaştırması olsun. Asıl kaliteyi Neeko eval seti ve insan dinleme testleri belirlemeli.
3. **Neeko sözlüğünü ürün varlığı olarak görün.** Karakter adları, yan karakterler, marka/oyun terimleri, masal isimleri, şehirler ve sık kullanılan İngilizce çocuk kelimeleri için custom lexicon oluşturun. Bu sözlük ileride multi-character sistemin de parçası olacak.

---

## Alan 3 — Speaker adaptation, voice cloning ve karakter sesi sahipliği

### TL;DR

Zero-shot voice cloning demo ve hızlı prototip için yeterlidir; fakat Neeko gibi karakter IP’si üzerine kurulu ürün için tek başına yeterli değildir. Üretim hedefi, sözleşmeli ve özgün bir voice talent’tan alınan kontrollü veriyle **speaker LoRA/adaptor + karakter manifest + düzenli eval** hattı olmalıdır. VoxCPM2’nin LoRA/full fine-tune desteği ve az veriyle adaptasyon iddiası bu rota için uygun görünür; fakat 5–10 dakikalık adaptasyon iddiası production tutarlılığı anlamına gelmez. Neeko sesi için minimum 30 dakika, makul hedef 1–3 saat, uzun vadeli ideal ise farklı duygu/prosodi kategorileriyle 3+ saatlik temiz kayıt havuzudur.

### Özet

Speaker adaptation üç kademede ele alınmalı: zero-shot, few-shot/adaptor ve full fine-tune. **Zero-shot cloning**, 5–30 saniyelik referansla hızlı ses karakteri denemesi yapar. XTTS-v2, OpenVoice ve F5-TTS benzeri sistemler bu alanda güçlü prototip araçlarıdır. Ancak zero-shot sistemlerde uzun metinde drift, referans sesin prosodisini fazla kopyalama, cross-lingual aksan sızıntısı, oturumdan oturuma tutarsızlık ve güvenlik/IP riski vardır. Bu yüzden zero-shot, “Neeko’nun nihai sesi” değil, casting ve ürün demo aracıdır.

**Few-shot/adaptor/LoRA** yaklaşımı, Neeko için daha doğru üretim rotasıdır. Base model genel dil ve akustik yeteneği taşır; karaktere özel küçük bir adaptor/LoRA sesi, ritmi ve üslubu stabilize eder. Böylece tek base model üzerinde Neeko, yan karakterler ve ileride DLC karakterleri yönetilebilir. Ayrıca adapter registry kurulduğunda her sürüm ölçülebilir, rollback yapılabilir ve eski-yeni Neeko sesi karşılaştırılabilir.

**Full fine-tune**, yalnızca iki durumda değerli olur: birincisi, Türkçe çocuk-yönelimli domain’de base model sistematik hata yapıyorsa; ikincisi, Neeko ana karakteri için LoRA’nın taşıyamadığı kadar kapsamlı bir üslup/prosodi ihtiyacı varsa. Aksi halde full fine-tune, maliyet, overfit, catastrophic forgetting, lisans ve operasyonel bakım açısından gereksiz risk üretir.

Ses sahipliği teknik olduğu kadar hukuki ve operasyonel bir problemdir. “Bir kayıt aldık, modele bastık” yeterli değildir. Ses yeteneği sözleşmesi; synthetic voice/model training rızası, kullanım alanı, süre, münhasırlık, ödeme, revizyon, veri saklama, güvenlik, model çıktılarının hakları, adapter ağırlıklarının mülkiyeti, ileride yan karakterlerde kullanım yasağı/izni ve olası ayrılık senaryosunu açıkça içermelidir.

### Adaptation seçenekleri

| Yaklaşım | Veri ihtiyacı | GPU/maliyet | Tutarlılık | IP kontrolü | NEEKO rolü |
|---|---:|---:|---:|---:|---|
| Zero-shot cloning | 5–30 sn / birkaç dk | Düşük | Orta-düşük; drift riski | Zayıf; referansa bağımlı | Casting, demo, baseline |
| Professional clone | 30 dk–3 saat | Orta | Orta-yüksek | Sözleşmeye bağlı | İlk kaliteli karakter demosu |
| Speaker LoRA/adaptor | 10 dk–3 saat | Orta | Yüksek olabilir | Yüksek; adapter sahipliği mümkün | **Önerilen production path** |
| Multi-speaker LoRA | 1–2 saat/speaker araştırma ölçeği | Orta-yüksek | Çok karakter için iyi | Yüksek | İleride yan karakterler |
| Full speaker/domain fine-tune | Saatler–onlarca saat | Yüksek | En yüksek ama overfit riski | Yüksek | Sadece ana karakter/domain için gerekirse |

### Model bazlı adaptation notları

| Model / aile | Adaptation sinyali | Ticari/IP notu | NEEKO kararı |
|---|---|---|---|
| VoxCPM2 | LoRA ve full fine-tune dokümantasyonu; 5–10 dakika audio ile speaker/language/domain adaptation iddiası; 2B model için LoRA yaklaşık 20 GB GPU tahmini | Apache-2.0; ticari rota için güçlü | **Ana üretim adayı** |
| XTTS-v2 | 6 saniyelik referansla voice cloning; multilingual/cross-language; çoklu referans/interpolation | Coqui Public Model License production için kısıtlayıcı olabilir | Baseline ve audition |
| OpenVoice v2 | Kısa referansla tone color cloning; emotion/accent/rhythm/pause/intonation gibi style controls; zero-shot cross-lingual | Lisans ve model parçaları ayrıca incelenmeli | Audition/style transfer referansı |
| F5-TTS | Non-autoregressive flow matching; hızlı inference; fine-tuning topluluğu güçlü | Kod MIT, bazı pretrained modeller CC-BY-NC | Araştırma ve teknik baseline |
| Fish / OpenAudio | Kalite tavanı güçlü | Ticari lisans/sözleşme riski | Quality ceiling, production backbone değil |

### Neeko sesi için önerilen kayıt/veri stratejisi

Neeko’nun sesi “voice cloning” olarak değil, **özgün karakter yaratımı** olarak ele alınmalı. Bir ünlünün, başka bir oyuncak karakterinin, çocuk sesinin veya tanınabilir üçüncü kişinin sesine benzetmek uzun vadede risklidir. Daha güvenli rota, yetişkin profesyonel bir ses oyuncusunun çocuk-yönelimli, sıcak ama yapay olmayan bir karakter performansı üretmesidir.

Önerilen aşamalar:

1. **Casting pack — 3–5 aday, kişi başı 10 dakika.** Aynı 80–120 prompt okutulur. Nötr, heyecanlı, sakinleştirici, oyun yönlendiren, masal anlatan, soru soran, özür dileyen, güven veren tonlar dahil edilir.
2. **Zero-shot audition.** Her aday için VoxCPM2/XTTS/OpenVoice ile kısa klon denemesi yapılır. İnsan paneli “çocuğa uygunluk, karakter sıcaklığı, anlaşılırlık, yorucu olmama, ebeveyn güveni” ekseninde puanlar.
3. **Final recording — minimum 30 dakika, hedef 1–3 saat.** Final talent ile kontrollü stüdyo kaydı yapılır. Veri yalnızca TTS eğitimi için değil, ileride regresyon/eval için de ayrılır.
4. **LoRA ablation.** 10 dk / 30 dk / 1 saat / 3 saat veri ile ayrı LoRA deneyleri yapılır. Aynı eval setinde speaker similarity, pronunciation error, human MOS/CMOS ve long-form drift ölçülür.
5. **Reserve set.** Kayıtların bir kısmı eğitimde kullanılmaz. Bu set, “model gerçekten karakteri öğrendi mi, yoksa eğitim cümlelerini mi ezberledi?” sorusunu ölçer.

### Speaker similarity ve drift ölçümü

Sadece MOS ile speaker adaptation yönetilemez. Neeko için üçlü ölçüm gerekir:

1. **Speaker similarity:** ECAPA-TDNN, WavLM speaker embeddings, Resemblyzer gibi modellerle hedef referans ve generated output arasındaki embedding similarity. Bu değer tek başına nihai karar değildir; ama drift ve sürüm farkı için iyi sinyal verir.
2. **Character consistency:** Aynı prompt sınıflarında v1-Neeko, v2-Neeko, zero-shot, LoRA ve human reference karşılaştırılır. Uzun masal, kısa komut, soru, şarkımsı ritim, sakin uyku tonu ayrı ölçülür.
3. **Human perceptual test:** Ebeveyn/adult paneli “aynı karakter mi?”, “çocuk için anlaşılır mı?”, “rahatsız edici/robotik mi?”, “fazla yetişkin/çocuk taklidi mi?” sorularını puanlar.

Önemli uyarı: Speaker similarity’yi körlemesine maksimize etmek doğru hedef değildir. Model, voice talent’ın birebir kişisel sesini değil, NEEKO’nun sözleşmeyle tanımlanmış karakter sesini tutarlı üretmelidir. Bu yüzden kontratta “talent vocal likeness” ile “Neeko synthetic character voice” ayrımı netleşmelidir.

### Sözleşme ve sahiplik şartları

Ses IP’sinde minimum sözleşme kapsamı:

| Başlık | Neden kritik? | Sözleşmede netleşmesi gerekenler |
|---|---|---|
| Açık synthetic voice rızası | Klasik seslendirme sözleşmesi AI eğitimi için yeterli olmayabilir | Model training, fine-tune, cloning, synthetic generation izni |
| Kullanım kapsamı | Oyuncak, app, web, reklam, DLC farklı hak doğurabilir | Medya, ülke, dil, süre, platform, ürün ailesi |
| Münhasırlık | Aynı sesin rakip üründe kullanılması karakteri bozar | Kategori bazlı exclusivity, süre, ücret |
| Veri ve model mülkiyeti | Kayıt, embedding, adapter, generated output ayrılmalı | Dataset, trained weights, LoRA, output ownership |
| Revizyon ve yeni kayıt | Karakter büyüdükçe yeni cümle gerekir | Ek kayıt günleri, ücret, SLA |
| Güvenlik ve saklama | Ses datası kişisel veri olabilir | Erişim, şifreleme, retention, silme prosedürü |
| Ayrılık/çekilme | Kişilik hakkı ve itibar senaryoları | Önceden üretilmiş içerik, yeni üretim, buyout, sunset |
| Yasaklı kullanım | Talent’ın itibarını korur, şirket riskini azaltır | Politik, yetişkin, yanıltıcı, üçüncü kişi taklidi yasakları |

### Kaynaklı bulgular

- XTTS-v2, 6 saniyelik referans clip ile multilingual voice cloning, emotion/style transfer ve cross-language voice cloning yetenekleri sunduğunu belirtir. Kaynak: Coqui XTTS-v2 Hugging Face model card, 2026 erişim. https://huggingface.co/coqui/XTTS-v2
- OpenVoice, kısa referansla tone color cloning, flexible style control ve zero-shot cross-lingual cloning hedeflerini raporlar. Kaynak: OpenVoice GitHub ve arXiv, 2024/2026 erişim. https://github.com/myshell-ai/OpenVoice / https://arxiv.org/html/2312.01479v6
- VoxCPM2, 2B parametreli, 30 dil destekli, Voice Design ve Controllable Voice Cloning özellikli model olarak tanıtılır; fine-tuning dokümanında LoRA ve full SFT desteği, 5–10 dakika audio ile adaptasyon örnekleri ve VoxCPM2 LoRA için yaklaşık 20 GB GPU gereksinimi verilir. Kaynak: VoxCPM GitHub/docs, 2026 erişim. https://github.com/OpenBMB/VoxCPM / https://voxcpm.readthedocs.io/en/latest/finetuning/finetune.html
- F5-TTS, Diffusion Transformer + ConvNeXt V2 mimarisine dayalı flow-matching TTS yaklaşımıdır; GitHub dokümanı L20 üzerinde düşük RTF/latency benchmark’ları ve pretrained model lisans sınırlamalarını açıklar. Kaynak: F5-TTS GitHub/arXiv, 2024–2026 erişim. https://github.com/SWivid/F5-TTS
- ElevenLabs, instant voice cloning için 1–5 dakika; professional voice cloning için minimum 30 dakika, optimum 3 saat temiz tek-speaker audio önerir. Kaynak: ElevenLabs voice cloning docs, 2026 erişim. https://elevenlabs.io/voice-cloning
- Resemble AI, professional voice clone için 10–25+ dakika kayıt ve açık doğrulanabilir rıza gerektirdiğini belirtir. Kaynak: Resemble AI voice creation docs, 2026 erişim. https://www.resemble.ai/products/voice-creation
- NAVA’nın synthetic voice sözleşme rehberi; consent, kullanım limiti, compensation, exclusivity, safe storage, term ve opt-out gibi maddeleri standardize edilmesi gereken başlıklar olarak listeler. Kaynak: NAVA Synthetic/AI Voices, 2026 erişim. https://navavoices.org/synth-ai-info/
- ECAPA-TDNN, VoxCeleb speaker verification benchmark’larında güçlü performans raporlayan speaker embedding mimarisidir; SpeechBrain pretrained ECAPA modeli speaker verification/embedding için kullanılabilir. Kaynak: ECAPA paper ve SpeechBrain model card, 2020/2026 erişim. https://arxiv.org/abs/2005.07143 / https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- WavLM, SUPERB ve speaker verification/separation/diarization gibi konuşma görevlerinde güçlü self-supervised speech backbone olarak raporlanmıştır. Kaynak: WavLM paper, 2021. https://arxiv.org/abs/2110.13900

### Bize özel öneri

1. **Zero-shot clone’u üretim sesi sanmayın.** İlk ayda zero-shot audition yapın; ama production roadmap’i VoxCPM2 LoRA/adaptor + kayıtlı/sözleşmeli Neeko sesi üzerine kurun.
2. **Ses IP’sini sözleşme + registry + eval üçlüsüyle sahiplenin.** Dataset ve adapter ağırlıklarının şirket varlığı olduğunu teknik manifest ve hukuki dokümanda açıkça bağlayın. Her adapter sürümü kontrat, veri seti, watermark key ve eval sonucu olmadan release edilmemeli.
3. **İlk ciddi kayıt hedefi 1–3 saat olsun.** 10 dakikalık LoRA deneyleri hızlı sinyal verir; fakat çocuk-yönelimli, uzun vadeli karakter için duygu/prosodi çeşitliliği ve reserve eval seti şarttır.

---

## Alan 8 — Multi-character, karakter kimliği ve IP koruması

### TL;DR

Neeko ileride tek bir ses değil, karakter ailesi olacak. Bu yüzden mimari bugün “tek model + tek clone” olarak değil, **base model + per-character adapter + per-character frontend lexicon + versioned manifest** olarak kurulmalı. Karakter sesi IP koruması da yalnızca hukuki sözleşme değildir; watermarking, voice fingerprinting, erişim kontrolü, adapter şifreleme, audit log ve release gating gerektirir. Türkiye bağlamında ses kaydı, speaker embedding ve synthetic voice verisi KVKK/FSEK/komşu haklar/kişilik hakkı ekseninde dikkatli yönetilmelidir; özellikle yurt dışı GPU işleme ve açık rıza süreçleri tasarıma baştan dahil edilmelidir.

### Özet

Multi-character TTS sisteminde en sık yapılan hata, her karakteri bağımsız bir model gibi düşünmektir. Bu yaklaşım kısa vadede çalışır ama maliyet, latency, sürüm yönetimi, veri güvenliği ve kalite kontrol açısından hızla bozulur. NEEKO için daha iyi mimari, güçlü bir base model üzerinde karakter başına küçük adapter/LoRA ve karakter konfigürasyonu kullanmaktır. Böylece ana Neeko, yan karakterler, özel figürler, masal anlatıcıları ve DLC karakterleri aynı deployment hattından geçer.

Her karakter için yalnızca ses ağırlığı değil; konuşma sözlüğü, konuşma üslubu, yasaklı kelimeler, prosody hints, eval seti ve hukuki izinler de farklı olabilir. Örneğin Neeko sıcak, güven veren, oyun kuran bir tonda konuşurken; yan karakter daha komik, hızlı veya bilge olabilir. Bu farkı yalnızca prompt ile kontrol etmek uzun vadede güvenilir değildir. Karakter manifesti, teknik ve hukuki tüm parametreleri tek yerde tutmalıdır.

IP koruması katmanlı yapılmalıdır. Birinci katman sözleşme ve kayıt sahipliğidir. İkinci katman teknik erişim kontrolüdür: raw dataset, referans kayıtlar, speaker embeddings, LoRA ağırlıkları ve watermark key’leri ayrı güvenlik alanlarında tutulmalıdır. Üçüncü katman provenance: generated output watermarking ve voice fingerprint logging. Dördüncü katman detection ve enforcement: internette veya rakip üründe Neeko’ya aşırı benzeyen sesler için speaker similarity araması, kayıt karşılaştırması ve hukuki takedown süreci.

Watermarking yararlı ama tek başına yeterli değildir. AudioSeal gibi yöntemler AI-generated speech için lokalize watermark detection sunar; VoiceMark gibi araştırmalar zero-shot voice cloning’e dayanıklı watermarking yönünü araştırır. Ancak watermark’lar saldırı, sıkıştırma, yeniden sentezleme veya removal yöntemleri karşısında her zaman garanti sağlamaz. Bu yüzden watermark, “kanıt zinciri ve caydırıcılık” katmanı olarak görülmeli; erişim kontrolü ve sözleşme yerine geçmemelidir.

### Multi-character mimari seçenekleri

| Mimari | Ölçeklenebilirlik | Ses tutarlılığı | Operasyon riski | NEEKO kararı |
|---|---:|---:|---:|---|
| Tek base model + speaker prompt/reference | Yüksek | Orta; drift ve prompt bağımlılığı | Düşük başlangıç, yüksek kalite riski | Prototip/audition |
| Tek base model + speaker embedding bank | Yüksek | Orta | Embedding collision/drift riski | Ara katman, hızlı karakter denemesi |
| Tek base model + per-character LoRA/adaptor | **Yüksek** | **Yüksek** | Registry ve hot-swap yönetimi gerekir | **Önerilen production mimarisi** |
| Karakter başına full fine-tune | Düşük-orta | Yüksek olabilir | Maliyet, bakım, overfit yüksek | Yalnızca lead character istisnası |
| Ayrı vendor/model per character | Düşük | Tutarsız | Vendor lock-in ve lisans riski | Kaçınılmalı |

### Character manifest önerisi

Her karakter bir manifest ile release edilmeli. Bu manifest git-benzeri versioning veya internal registry’de tutulabilir.

```yaml
character_id: neeko
character_version: v1.2.0
base_model:
  name: VoxCPM2
  version: 2026-xx
  license: Apache-2.0
adapter:
  type: lora
  adapter_id: neeko-lora-v1.2.0
  storage_uri: s3://secure-voice-models/neeko/v1.2.0/adapter.safetensors
  encrypted: true
  checksum: sha256:...
voice_dataset:
  dataset_id: neeko_voice_2026_06_session_a_b
  hours_train: 2.4
  hours_eval_reserved: 0.4
  sample_rate: 48000
  talent_contract_id: contract_neeko_voice_2026_001
frontend:
  lexicon_version: neeko_lexicon_v1.4
  normalization_rules_version: tr_tn_v0.7
style:
  default_tags: [child_directed, warm, clear, playful]
  forbidden_tags: [celebrity_imitation, political_persuasion, adult_content]
watermark:
  provider: audioseal_or_internal
  key_id: wm_neeko_v1
fingerprint:
  reference_embedding_set: neeko_ref_embed_v1
  similarity_thresholds:
    same_character_min: 0.72
    cross_character_max: 0.55
eval:
  eval_set_version: neeko_tts_eval_v0.3
  pronunciation_error_rate: 0.04
  speaker_similarity_mean: 0.76
  mos_internal: 4.2
  longform_drift_passed: true
release:
  status: production
  approved_by: [ml_lead, product, legal]
  created_at: 2026-xx-xx
```

Bu manifest, kalite kadar hukuki ve güvenlik gereksinimleri için de önemlidir. Bir ses çıktısı sorunlu bulunursa, hangi model, hangi adapter, hangi sözlük, hangi kontrat ve hangi watermark key ile üretildiği geriye dönük bulunabilmelidir.

### Karakter sesi versiyonlama

Versiyonlama yalnızca “daha iyi ses” için değil, ürün güvenliği ve marka sürekliliği için gerekir. Çocuklar karakter sesindeki ani değişimi fark edebilir; ebeveynler de “oyuncağın sesi değişti” hissiyle güven kaybedebilir.

Önerilen sürümleme kuralı:

- **Patch (`v1.2.1`)**: Telaffuz düzeltmesi, frontend sözlük güncellemesi, küçük stabilite iyileştirmesi. Ses kimliği değişmemeli.
- **Minor (`v1.3.0`)**: Yeni duygu/prosodi alanı, daha iyi uyku tonu, masal anlatımı gibi ek kapasite. Ses kimliği büyük oranda aynı kalmalı.
- **Major (`v2.0.0`)**: Yeni voice talent, base model değişimi, belirgin karakter yaşı/kimliği değişimi. Ürün tarafında kontrollü geçiş gerekir.

Her sürümde eski Neeko ile yeni Neeko arasında pairwise human test yapılmalı. “Aynı karakter gibi geliyor mu?” sorusu özellikle çocuk/ebeveyn panelinde ölçülmelidir.

### Voice fingerprinting ve collision kontrolü

Character registry’de her karakter için referans embedding dağılımı tutulmalı. Bir generated output release edilmeden önce şu kontroller yapılabilir:

1. **Same-character similarity:** Generated Neeko, Neeko referans dağılımına yeterince yakın mı?
2. **Cross-character separation:** Generated Neeko, yan karakter veya yasaklı referans seslere fazla benziyor mu?
3. **Long-form drift:** 2–5 dakikalık masal çıktısında embedding zaman içinde hedef sesten uzaklaşıyor mu?
4. **Prompt leakage:** Model, referans ses dışındaki aktör/speaker kimliğini veya başka kaynak sesi taklit ediyor mu?

ECAPA-TDNN, WavLM ve Resemblyzer gibi araçlar bu kontrollerde birlikte kullanılabilir. Tek bir embedding modeline güvenmek yerine ensemble sinyal daha güvenlidir; özellikle Türkçe ve çocuk-yönelimli konuşma domain’i için bias olabilir.

### Watermarking ve provenance

Önerilen minimum IP koruma hattı:

```text
TTS request
  -> authenticated service account
  -> character manifest resolve
  -> frontend normalize
  -> model inference
  -> output watermark / provenance stamp
  -> audio fingerprint log
  -> signed response metadata
  -> storage / device delivery
```

Watermark iki amaç taşır: Neeko tarafından üretilmiş sesleri kanıtlamak ve kötüye kullanım/kaçak üretim durumunda iz sürebilmek. Ancak watermark, child product UX’ine zarar vermemeli. Çocuğa her cümlede “bu ses yapaydır” denmesi ürün deneyimini bozabilir; bunun yerine ürün kutusu, ebeveyn uygulaması ve kullanım koşullarında şeffaf disclosure; ses dosyasında teknik watermark/provenance daha doğru dengedir.

Teknik uyarılar:

- Watermark key’leri karakter adapter’larından ayrı tutulmalı.
- Watermark release öncesi codec, Bluetooth, düşük kaliteli hoparlör, oyuncak mikrofonu ve yeniden kayıt koşullarında test edilmeli.
- Watermark kaldırılabilir varsayılmalı; sözleşme, erişim kontrolü ve fingerprinting ile desteklenmeli.
- Tüm generated output’lar merkezi log’a gitmeyebilir; edge/offline senaryolarda cihaz kimliği ve içerik hash’i ayrı tasarlanmalı.

### Türkiye hukuki çerçevesi: teknik tasarıma etkisi

Bu bölüm hukuki danışmanlık değildir; ürün tasarımında erken risk haritası olarak kullanılmalıdır.

KVKK açısından ses kaydı kişisel veri niteliği taşıyabilir; speaker identification veya speaker embedding ile kişiyi tanımlama/ayırma yapılırsa biyometrik veri tartışması daha da güçlenir. KVKK’nın temel ilkeleri; hukuka ve dürüstlük kurallarına uygun işleme, doğru/güncel veri, belirli/açık/meşru amaç, amaçla sınırlılık ve gerekli süre kadar saklamadır. Açık rıza, aydınlatma, veri güvenliği, veri sahibi hakları ve yurt dışı aktarım koşulları tasarıma baştan dahil edilmelidir. Cloud GPU, yabancı inference endpoint veya yurt dışı storage kullanılıyorsa cross-border data transfer konusu ayrıca ele alınmalıdır.

FSEK ve komşu haklar açısından seslendirme performansı, icracı sanatçı/performer hakları ve fonogram/tespit kullanımı ekseninde ele alınmalıdır. Yazılı izin, kullanım alanı, çoğaltma, yayınlama, işleme, temsil ve dijital/synthetic üretim hakları netleşmeden kayıtların model training için kullanılması risklidir. Ayrıca performerin kamuya mal olmuş kişiliğine veya mesleki itibarına zarar verecek kullanım sınırlanmalıdır.

Kişilik hakkı boyutu da teknik mimariyi etkiler. Bir voice talent ile çalışırken bile şirketin amacı “kişinin gerçek hayattaki kimliğini sonsuza kadar sentetikleştirmek” değil, sözleşmeyle tanımlanmış özgün bir karakter sesi yaratmak olmalıdır. Bu ayrım; kontrat, veri etiketleme, marketing metinleri ve model access control içinde korunmalıdır.

### IP ve güvenlik kontrol listesi

| Risk | Teknik kontrol | Hukuki/operasyonel kontrol |
|---|---|---|
| Raw voice dataset sızması | Şifreli storage, least privilege, signed URL yok, audit log | NDA, vendor DPA, retention policy |
| Adapter ağırlığı sızması | Encrypted artifact, access-scoped registry, checksum | Model ownership clause |
| Talent rızası belirsizliği | Contract ID olmadan training job çalışmaması | Synthetic voice rider, açık rıza |
| Yurt dışı GPU aktarımı | Data residency flag, anonymization/pseudonymization, transfer log | KVKK cross-border assessment |
| Karakter drift | Eval gate, speaker fingerprint threshold | Release approval process |
| Başka sese benzerlik | Cross-character/prohibited-voice similarity check | No-imitation clause |
| Kaçak Neeko sesi | Watermark, fingerprint crawler, provenance logs | Takedown workflow |
| Çocuk ürünü güven kaybı | Safe style tags, prompt guardrails, parent disclosure | Product policy and QA |

### Kaynaklı bulgular

- AudioSeal, AI-generated speech için localized watermark detection hedefleyen bir audio watermarking yaklaşımı olarak yayınlanmıştır; resmi uygulama MIT lisanslı GitHub reposunda bulunur. Kaynak: AudioSeal paper/GitHub, 2024/2026 erişim. https://arxiv.org/abs/2401.17264 / https://github.com/facebookresearch/audioseal
- VoiceMark, zero-shot voice cloning’e dayanıklı speaker-specific watermarking yönünü araştırır ve mevcut watermarking yaklaşımlarının zero-shot VC saldırılarında zayıf kalabileceğini tartışır. Kaynak: VoiceMark paper, 2025. https://arxiv.org/abs/2505.21568
- 6698 sayılı KVKK; kişisel verilerin işlenmesinde açık rıza, veri işleme ilkeleri, özel nitelikli veriler, aydınlatma yükümlülüğü, veri sahibi hakları, veri güvenliği ve yurt dışı aktarım koşullarını düzenler. Kaynak: Lexpera KVKK metni, güncel erişim 2026. https://www.lexpera.com.tr/mevzuat/kanunlar/kisisel-verilerin-korunmasi-kanunu-6698
- 5846 sayılı FSEK; eser sahipleri, icracı sanatçılar, fonogram yapımcıları ve bağlantılı/komşu haklar bağlamında kullanım koşullarını düzenler. Kaynak: Lexpera FSEK metni, 2026 erişim. https://www.lexpera.com.tr/mevzuat/kanunlar/fikir-ve-sanat-eserleri-kanunu-5846
- Eser Sahibinin Haklarına Komşu Haklar Yönetmeliği; icracı sanatçıların yazılı izin, tespit, çoğaltma, kiralama, yayınlama ve kişilik/itibar koruması gibi haklarını düzenler. Kaynak: T.C. Kültür ve Turizm Bakanlığı mevzuat metni, 2026 erişim. https://teftis.ktb.gov.tr/TR-263884/eser-sahibinin-haklarina-komsu-haklar-yonetmeligi.html
- NAVA synthetic voice rehberi, AI voice sözleşmelerinde consent, usage scope, compensation, exclusivity, safe storage ve term gibi başlıkları açıkça tanımlama gereğini vurgular. Kaynak: NAVA, 2026 erişim. https://navavoices.org/synth-ai-info/

### Bize özel öneri

1. **Bugünden per-character adapter mimarisi kurun.** Tek Neeko sesi için bile `character_id`, `adapter_id`, `lexicon_version`, `contract_id`, `watermark_key_id` ve `eval_set_version` tutun. Yan karakterler geldiğinde sistem değişmemeli.
2. **Watermark + fingerprint + access control üçlüsünü birlikte kullanın.** Watermark tek başına güvenlik değildir. Raw kayıt, adapter ve watermark key ayrı güvenlik alanlarında tutulmalı; tüm inference/release işlemleri audit log üretmeli.
3. **KVKK/FSEK konusunu model eğitiminden önce çözün.** Özellikle yurt dışı cloud GPU, synthetic voice rızası, dataset/model mülkiyeti ve performer kişilik hakkı sözleşmeye girmeden final kayıtla training job başlatmayın.

---

## 30 / 90 günlük uygulama planı

### İlk 30 gün

| İş | Çıktı | Başarı kriteri |
|---|---|---|
| `neeko-tts-frontend` v0.1 | Python package + CLI + JSON output contract | 300 golden test pass |
| Turkish eval set v0.1 | 200 cümle: semiotic + çocuk tonu + kod-karışımı | Her model aynı setle çalışır |
| Casting prompt pack | 80–120 cümlelik kayıt metni | Nötr/oyun/uyku/masal/soru tonları var |
| 3–5 voice talent audition | Kişi başı 10 dk kayıt | Zero-shot clone ve human panel yapılır |
| Hukuki rider taslağı | Synthetic voice / AI training sözleşme eki | Legal review başlar |
| Registry tasarımı | Character manifest schema | Neeko v0 manifest elle doldurulur |

### 31–90 gün

| İş | Çıktı | Başarı kriteri |
|---|---|---|
| Final talent kaydı | 1–3 saat temiz kayıt + reserve set | Dataset versioned, contract linked |
| VoxCPM2 LoRA ablation | 10dk / 30dk / 1s / 3s deneyleri | En az bir LoRA XTTS baseline’ı geçer |
| Frontend v0.2 | Kısaltma, tarih, yaş, marka sözlüğü genişletme | Eval cümlelerinde normalization error < %3 |
| Speaker eval harness | ECAPA/WavLM/Resemblyzer + human panel | Drift ve similarity raporu çıkar |
| Watermark/fingerprint pilot | AudioSeal veya alternatif POC | Codec/Bluetooth/yeniden kayıt testleri yapılır |
| Release gate | ML + product + legal onay akışı | Manifest olmadan prod release engellenir |

---

## Karar matrisi

| Karar alanı | Önerilen karar | Neden |
|---|---|---|
| Türkçe frontend | Custom deterministic TN + lexicon + test suite | Türkçe hataların büyük kısmı non-standard text kaynaklı; modelden bağımsız değer üretir |
| G2P | V1’de opsiyonel/debug; production primary değil | Türkçe düz metin yeterli olabilir; önce normalization çözülmeli |
| Ana adaptation modeli | VoxCPM2 LoRA | Apache-2.0, Türkçe/multilingual destek, fine-tune dokümanı ve adapter rotası güçlü |
| Zero-shot araçları | XTTS/OpenVoice/F5/VoxCPM audition | Hızlı casting ve baseline için değerli |
| Veri hedefi | 1–3 saat final talent + reserve eval | 5–10 dk demo sinyali verir ama karakter IP’si için yetersiz |
| Multi-character | Tek base + per-character LoRA/adaptor registry | Ölçek, kalite, rollback ve IP yönetimi dengeli |
| IP koruma | Sözleşme + access control + watermark + fingerprint | Tek katman kırılabilir; savunma derinliği gerekir |
| Hukuki süreç | Training öncesi synthetic voice rider + KVKK/FSEK review | Sonradan düzeltmesi pahalı ve riskli |

---

## Açık riskler ve kör noktalar

1. **VoxCPM2’nin Türkçe çocuk-yönelimli kalitesi kamu benchmark’ıyla kesinleşmiş değil.** Resmi Türkçe destek ve fine-tune dokümanı güçlü sinyal; ama karar Neeko eval setiyle verilmeli.
2. **5–10 dakika LoRA adaptasyonu production tutarlılığı anlamına gelmez.** Bu yalnızca hızlı adaptation sinyali olarak kabul edilmeli. Uzun-form masal ve oturumlar arası sabitlik ayrıca ölçülmeli.
3. **eSpeak/Phonemizer gibi araçların lisans ve dağıtım modeli incelenmeli.** GPL/LGPL bileşenler backend servisinde kullanılabilir olsa da ürün içine gömme, binary dağıtım ve kapalı kaynak entegrasyon için hukuk/engineering review gerekir.
4. **Speaker embedding metrikleri Türkçe/çocuk-yönelimli konuşmada bias taşıyabilir.** ECAPA/WavLM/Resemblyzer skorları insan testi yerine geçmez.
5. **Watermark sökülebilir varsayılmalı.** IP koruması yalnızca teknik watermark’a dayandırılırsa kırılgan olur.
6. **Voice talent kişilik hakkı, sınırsız buyout ile tamamen ortadan kalkmaz.** Sözleşme güçlü olsa bile itibarı zedeleyen kullanım, yanıltıcı kullanım veya açık rıza kapsamını aşan kullanım risk üretir.

---

## Sonuç

Bu bloktan çıkan ana karar: NEEKO’nun ses altyapısı, model seçimi kadar **frontend ve governance ürünü** olmalıdır. Türkçe text frontend hataları çözülmeden hiçbir açık-ağırlıklı model ElevenLabs-grade algılanmayacaktır. Ses yeteneği kaydı ve speaker LoRA olmadan da Neeko’nun sesi şirket varlığına dönüşmeyecektir.

İlk üretim mimarisi şöyle olmalı:

```text
LLM response
  -> neeko-tts-frontend
  -> character manifest resolve
  -> VoxCPM2 base + Neeko LoRA
  -> streaming inference
  -> watermark/fingerprint
  -> eval/logging/cache
  -> device playback
```

Bu mimari hem bugünkü Neeko sesini stabilize eder hem de ileride yan karakter, DLC, figür ve masal evreni ölçeklenirken vendor lock-in’i azaltır.
