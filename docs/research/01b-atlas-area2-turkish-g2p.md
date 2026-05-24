# Alan 2 — Türkçe G2P ve Text Frontend (Atlas Araştırma Çıktısı)

**Hazırlayan:** Atlas (Claude Opus 4.7) · **Tarih:** 2026-05-19 · **Kapsam:** Açık kaynak Türkçe G2P kütüphaneleri, fonetik kural seti, text normalization (TN), code-switching, fonetik sözlük kaynakları.

> **Karar yönü:** Türkçe G2P + TN katmanı, açık kaynak TTS yığınımızın **en yüksek leverage** noktasıdır. Modeller niçin Türkçe'de zayıf? Çünkü en yaygın frontend (espeak-ng) Türkçe'de düzeyin altında, neural modeller phoneme yerine grapheme alıyor (MMS-TTS), ve ticari kapalı modeller (Polly, Azure) kendi sözlüklerini taşıyor. Bizim için bu = **dar bir niche'te bariyer kurma fırsatı**.

---

## TL;DR Genel (5 cümle)

1. **Türkçe için kamuya açık, üretim kalitesinde, modern bir G2P kütüphanesi yok.** Mevcut seçenekler ya kural-tabanlı ve eski (Altınok 2016), ya çok-dilli ortalama kalite (espeak-ng, epitran, CharsiuG2P), ya da TTS'e gömülü ve ayrı kullanılması zor (Rumeysakeskin'in CMUDict_tr).
2. **espeak-ng Turkish (tr) `tr_rules` dosyası 364 satır**, vurguyu son-vurgu varsayımıyla yerleştiriyor, ünlü uyumunu açıkça modellemiyor, place-name (Sezer) stress istisnalarını ele almıyor; foreign loanword epentezi ve circumflex (`â/î/û`) açıklarıyor. Üretim kalitesi için "kabul edilebilir taban" ama "Neeko grade" değil.
3. **Text normalization tarafı şaşırtıcı olarak daha iyi durumda:** `num2words` (Apache 2.0) Türkçe cardinal+ordinal destekliyor, `trnorm` (Apache 2.0, 2024) sayı/Roma rakamı/sembol/ordinal normalize ediyor, `Zemberek` 1057 kısaltma sözlüğü + spelling correction sunuyor — ama bunlar parça parça; **bütünleşik bir Türkçe TTS frontend kütüphanesi yok**.
4. **Code-switching (Türkçe-İngilizce karışım: "iPhone'umu açtım") için kamuya açık G2P pipeline yok** — sadece Yirmibeş et al.'in code-switching detection corpusu var; karışık G2P kendimiz kurmalıyız.
5. **Karar önerisi:** Birinci faz tabanı = `espeak-ng (tr)` + `num2words` + `trnorm` + `Zemberek abbreviations` + Altınok'un rule_based_g2p exception sözlüğü. İkinci faz = bu tabanı bizim çocuk-yönelimli vocabulary üzerinde curate edilmiş bir Türkçe fonetik sözlük + LID + neural homograph disambiguation katmanı ile **NEEKO Turkish Frontend** olarak kapatma.

---

## 1. Türkçe G2P Kütüphaneleri — Karşılaştırma

### 1.1 Bulunan kütüphaneler ve gerçek durumları

| Kütüphane | Türkçe kalite | Yaklaşım | Lisans | Son aktivite | Üretim için? |
| --- | --- | --- | --- | --- | --- |
| **espeak-ng (tr)** | Orta — taban kalite | Rule-based, 364 satır `tr_rules` + `tr_languages.c` | GPL-3.0 | Aktif (2025) | Taban olarak evet, tek başına hayır |
| **phonemizer** (bootphon) | espeak-ng aynası | Wrapper | GPL-3.0 | Aktif | espeak ile birlikte |
| **epitran** (dmort27) | Düşük-orta | CSV mapping + repair | MIT | Aktif (yavaş) | Hayır — vurgu/uyum yok |
| **CharsiuG2P** (lingjzhu) | Orta | ByT5 neural, 100 dil | MIT | Aktif | Test gerek |
| **Altınok rule_based_g2p** | Orta-yüksek (akademik) | Rule + morfoloji + SAMPA + heceleme | (belirsiz, paper repo) | 2016 (terk) | Doğrudan hayır, fikir kaynağı |
| **Phonetisaurus (tr modeli)** | Düşük-orta | WFST seq2seq | BSD-2 | Aktif | Hayır — küçük corpus |
| **CMUDict_tr** (Rumeysakeskin) | Bilinmiyor | Lookup ~1.5M kelime + heteronyms | (TTS repo) | 2022-2023 | İncelenmeli, lisans belirsiz |
| **MMS-TTS-tur** (Facebook) | N/A — phoneme yok | Karakter-level VITS | CC-BY-NC 4.0 | 2023 | G2P değil; karşılaştırma için |
| **Misaki (hexgrad)** | Yok | G2P motoru, kokoro için | Apache 2.0 | Aktif, TR yok | Henüz değil — feature request |
| **VNLP normalizer** (vngrs-ai) | Yok (G2P yapmıyor) | NLP toolkit | AGPL-3.0 | Aktif | TN kısmı evet |
| **MaryTTS Turkish** | Eski/terk | HMM/concatenative | LGPL | ~2014 | Hayır |

**Kaynaklar:**
- espeak-ng tr_rules: [github.com/espeak-ng/espeak-ng/blob/master/dictsource/tr_rules](https://github.com/espeak-ng/espeak-ng/blob/master/dictsource/tr_rules)
- Phonemizer: [github.com/bootphon/phonemizer](https://github.com/bootphon/phonemizer)
- Epitran tr-Latn: [github.com/dmort27/epitran](https://github.com/dmort27/epitran)
- CharsiuG2P: [github.com/lingjzhu/CharsiuG2P](https://github.com/lingjzhu/CharsiuG2P) (ByT5, PER 0.089/WER 0.261 toplamda)
- Altınok 2016: [arxiv.org/abs/1601.03783](https://arxiv.org/abs/1601.03783) — repo: [github.com/DuyguA/computational_linguistics/tree/master/rule_based_g2p](https://github.com/DuyguA/computational_linguistics/tree/master/rule_based_g2p)
- MMS-TTS-tur: [huggingface.co/facebook/mms-tts-tur](https://huggingface.co/facebook/mms-tts-tur)
- Misaki: [github.com/hexgrad/misaki](https://github.com/hexgrad/misaki) (TR yok, issue #325 StyleTTS2 üzerinde tartışma var)

### 1.2 espeak-ng Türkçe — yakın inceleme

`tr_rules` dosyası 364 satır; temel mantık şu:

- Vurgu: en sağdaki ünlü vurgulanır, "unstressed" işaretli ünlülere durulur. Bu **Türkçe'nin varsayılan son-hece vurgusu**na yakın ama yer isimleri (Sezer stress), alıntılar, pre-stressing suffix'ler için açıkça yanlış sonuçlar verir.
- Ünlü kategorileri: arka (`ı a o u`) vs. ön (`i e ö ü î â ô û`). Bu **ünlü uyumu kural setinin çıkarılması için yeterli ama uyum modeline gömülü değil** — yalnızca ses üretimi için kullanılıyor, suffix uyum hatalarını düzeltmiyor.
- `ğ` (yumuşak g): bir ön ünlüden önce glide `[j]`'ye dönüşüyor; vokal uzatma efekti kısmen ele alınıyor ama düzensiz.
- Resmen kapatılmış bilinen hatalar (Issue #152, 2016, durum: resolved): `gitti` gibi çift ünsüzde ilk ses sessizleşmesi, `x/q/w` harf adlandırması, `sertifika/dakika` gibi kelimelerde "kalın k". Bunlar **birkaç edge case**, ana mimari problemler değil.
- **Açık problemler:** circumflex'in (`kâr/kar`, `lâzım`) palatalizasyon + uzun ünlü ayrımı tutarsız; foreign loanword epenthesis (`Brüksel` → "Birüksel" konuşma gerçeği) yok; place names (`Ankara` `An-ka-ra` ilk hece vurgu) yanlış vurguda.

**Veri noktası:** Piper TTS Türkçe sesleri (dfki, fahrettin, fettah — medium quality) espeak-ng'i frontend olarak kullanıyor. Topluluk yorumları "anlaşılır ama mekanik" diyor. ([github.com/rhasspy/piper/blob/master/VOICES.md](https://github.com/rhasspy/piper/blob/master/VOICES.md))

### 1.3 Altınok 2016 rule-based G2P — neden referans

Duygu Altınok'un 2016 paperı (`arxiv:1601.03783`) Türkçe için kamuya açık en bütünsel rule-based G2P tasarımı. Mimari parçalar:

- `g2p.py` — ana converter
- `word_to_sampa.py` — IPA değil **SAMPA** çıktısı (çünkü ASR oryantasyonlu)
- `syllabifier.py` — heceleme + birincil vurgu işaretleme
- `heuristic_stemmer.py` — morfolojik analiz kullanarak ek-bağlı pronunciasyon değişimlerini yakalıyor
- `exceptionary_phonetics.py` — düzensiz telaffuzlar (kelime başlı listesi)
- `dicts/` — yabancı kelime + istisna sözlüğü, CMUdict'ten klonlanmış foreign words dictionary
- Çıktı: paralel telaffuzlar listesi + vurgu pozisyonları + hece bölmesi (`-` ile)

Tasarım fikri kıymetli — özellikle **morfolojik analizden gelen telaffuz değişimleri** (consonant softening `kitap+ı → kitabı`, `p→b`). Ama:

- Lisans repo'da net değil (paper academic, kod araştırma kalitesinde)
- Aktif değil (terk)
- Modern Python ortamına entegrasyon iş ister
- Output SAMPA, modern phoneme tabanlı modeller IPA istiyor (Misaki/Kokoro IPA kullanıyor)

**Bizim için değer:** Kod direkt kullanılmaz ama **rule manifest** olarak kullanılır — hangi kuralları implement etmeliyiz sorusuna açık cevap.

### 1.4 Rumeysakeskin CMUDict_tr — gizli cevher mi?

[github.com/Rumeysakeskin/Turkish-Text-to-Speech](https://github.com/Rumeysakeskin/Turkish-Text-to-Speech) projesinde belgelenen yaklaşım:

- **~1.5M kelime Türkçe fonetik lexicon** (CMUDict formatında)
- **Heteronyms_tr** dosyası — çoklu telaffuz yönetimi
- **36 Türkçe fonem** seti (ünlüler + uzunluk işaretli `:` varyantları + ünsüzler)

Eğer lisans uygunsa bu **kritik kaynak**. 1.5M kelime ≈ Türkçe morfolojik üretkenlik göz önüne alındığında comprehensive lookup. Repo'nun MANIFEST/LICENSE kontrol edilmeli.

**Aksiyon:** Repo'yu klonla, lisansı kontrol et, fonem set spec'ini çıkar. Lisans temizse direkt fork + maintenance.

### Bize özel öneri (5 madde)

1. **Hibrit frontend tasarla, tek bir kütüphaneye bağımlı olma:** `espeak-ng` taban + override katmanı. Override = bizim Türkçe exception dictionary + place-name stress sözlüğü + circumflex/loanword tablomuz. Frontend modüler olsun ki bir komponenti değiştirmek diğerlerini bozmasın.
2. **Altınok'un rule manifest'ini implement et, kodunu kullanma:** Modern Python + tip işaretli + test kapsamı. Specs: morfolojik consonant softening, vowel harmony validation, syllabification, primary stress with Sezer exceptions. Lisans temiz olur.
3. **Rumeysakeskin CMUDict_tr'yi incele, lisans uyuyorsa baseline lookup olarak al:** 1.5M kelime > rule. Lookup-first, rule-fallback pattern; OOV kelimelerde rule devreye girer.
4. **Phoneme set'i IPA üzerinden standartlaştır, SAMPA değil:** Modern modeller (XTTS, Misaki, F5-TTS) IPA bekliyor. Altınok SAMPA çıktı veriyor — bizim katmanımız IPA olmalı. SAMPA↔IPA mapper testlerle yaz.
5. **Misaki'ye Turkish G2P PR'ı stratejik bir hamle:** Kokoro toplulukları büyüyor, bizim implementasyonumuz upstream'e girerse Neeko'nun teknik kredibilitesi sıçrar. PR'ı bizim production kalitesi geldikten sonra paylaş — open source contribution + brand.

---

## 2. Türkçe Fonetik Kuralları ve Açık Kaynak Ele Alış

### 2.1 Ünlü uyumu (vowel harmony)

Türkçe iki uyum prensibi taşır:

- **Büyük uyum (back/front):** Kelime kökünün ilk hecesindeki ünlü arka (`a ı o u`) ise tüm sonraki ünlüler arka; ön (`e i ö ü`) ise tüm sonraki ünlüler ön. ([Wikipedia: Turkish phonology](https://en.wikipedia.org/wiki/Turkish_phonology))
- **Küçük uyum (rounded/unrounded ve high/low):** Yüksek ünlüler (`ı i u ü`) içinde rounded-back, unrounded-back, rounded-front, unrounded-front dörtlüğü; alçak ünlüler (`a e o ö`) içinde benzer ayrım.

**Örnek:** `ev-im` (ev-im, ön + ön ✓), `kitap-ım` (kitap-ım, arka + arka ✓), `araba-yı` (arka kök → arka -ı ✓).

**Alıntı/istisna kelimeler:**
- `saat-i` (uyum bozar, çünkü `saat` Arapça alıntı)
- `kitap` → ek alınca `kitab-ı` (consonant softening + uyumlu suffix)
- `kalp-im` (uyum doğru ama ek-i kullanım: `kalbim` softening)

**Açık kaynak ele alışı:**
- **espeak-ng:** Ünlüleri arka/ön sınıflıyor ama uyum kuralını validation için kullanmıyor; ses üretiminde palatalization belirler.
- **epitran (tur-Latn-bab vs tur-Latn-red vs tur-Latn-nosuf):** Suffix'li mi suffix'siz mi varyantı sen seçiyorsun. Validation katmanı yok.
- **Altınok rule_based_g2p:** Morfolojik analiz + heuristic stemmer ile çözüyor; suffix uyumu doğrulanıyor.
- **VNLP morfolojik analyzer:** Uyum kural setini disambiguation için kullanıyor (TTS değil).

### 2.2 Ünsüz yumuşaması (consonant softening / "ketçap" kuralı)

Türkçe'de kelime sonundaki `p t k ç` (= "ketçap" kalıbı) ünsüzleri, ünlüyle başlayan ek alınca `b d ğ c`'ye yumuşar:

| Kelime | + ek | Yumuşamış |
| --- | --- | --- |
| kitap | -ı | kitabı (p→b) |
| ağaç | -a | ağaca (ç→c) |
| dört | -ü | dördü (t→d) |
| renk | -i | rengi (k→g) ya da kapsamda `ğ` |
| balık | -ı | balığı (k→ğ) |

İstisnalar: `at-a` (at, tek heceli, yumuşamaz), `top-u` (top yumuşamaz). **Kural tek-heceli native köklerde uygulanmaz.**

**Açık kaynak ele alışı:**
- **espeak-ng:** Bu transformasyon yazılı metin üzerinde yapılmaz; çünkü espeak grapheme-level çalışıyor. Eğer cümle yazılı olarak `kitabı` geliyorsa doğru, ama `kitap+ı` morfolojik gelmişse espeak bunu birleştiremez. **TTS context'inde her zaman yazılı form gelir, dolayısıyla bu kural normalize edilmiş yazıyla zaten doğru çalışır.**
- **Pre-tokenization aşamasında problem:** Eğer text "kitap-ı" gibi tire-ayrımlı veya morfemli geliyorsa, bizim normalize katmanımız `kitap+ı → kitabı` yapmalı.
- **Bizim için pratik:** TTS text input zaten yazılı Türkçe; bu transformasyonun TTS frontend'de yapılması gerekmiyor (kullanıcı `kitabı` yazıyor zaten). **Ama LLM çıktısı bazı durumda morfem-ayrımlı gelebilir** — buna karşı sanity check faydalı.

### 2.3 Vurgu kuralları (stress)

Türkçe varsayılan vurgu **son hece** üzerinde. İstisnalar:

- **Yer isimleri (Sezer stress):** Penultimate veya antepenultimate. `An.ka.ra` (1. hece), `İs.tan.bul` (2. hece), `Trab.zon` (1. hece). Akademik bulgu: 206 düzensiz vurgulu yer adının yalnızca 51'i Sezer kuralına uyuyor — kalanlar tek tek istisna. ([roa.rutgers.edu/files/39-1294/39-1294-INKELAS-0-0.PDF](https://roa.rutgers.edu/files/39-1294/39-1294-INKELAS-0-0.PDF))
- **Pre-stressing suffix'ler:** `-iyor`, `-ce`, `-le` gibi belirli ekler kendinden önceki heceye vurgu çeker. `geliyorum` → `ge.LI.yo.rum` (`-iyor` ekinden önce).
- **Alıntı kelimeler:** `lokanta`, `kavanoz`, `domates` ilk veya orta hecede.
- **Bileşik kelimeler:** İlk bileşene vurgu çeker, `BİL.ge.han`.

**Açık kaynak ele alışı:**
- **espeak-ng:** "right-most vowel receives stress, stops before unstressed marker"; yer isimlerini ele almaz; Sezer kuralı yok; pre-stressing suffix'ler için bazı `_S1..._S8` marker'ları var ama eksiksiz değil.
- **Altınok rule_based_g2p:** Pre-stressing suffix kapsamı geniş; place name dictionary referansı var ama düzensiz isim listesi sınırlı.
- **Bizim için kritik:** Çocuk içeriğinde yer isimleri sık geçer (`Ankara'da yaşıyorum`, `İzmir'in havası güzel`). Sezer stress + curated exception dictionary olmadan robot gibi konuşur.

### 2.4 Uzun ünlü ve circumflex (`â î û`)

Türkçe'de uzun ünlü işareti `â/î/û` Arapça/Farsça alıntılarda iki amaca hizmet eder:

- **Palatalizasyon işareti** (`â û` öncesindeki `k g l` ünsüzünü palatalize eder): `kâr /caɾ/` (kâr) vs `kar /kaɾ/` (snow). `lâle /laːlɛ/` palatalize `l`.
- **Uzun ünlü** (`î` her zaman uzun): `idarî /idaːɾi/`.

Modern Türkçe yazımında circumflex sıkça düşürülüyor; bu **homograf problemi** üretiyor (`kar/kâr`, `hala/hâlâ`). Akademik tartışma: TDK kuralları gereği circumflex yazılmalı ama günlük kullanımda yazılmıyor.

**Açık kaynak ele alışı:**
- **espeak-ng:** `â î û` ön ünlü olarak sınıflıyor (palatalization kazandırıyor) ama uzun ünlü durasyonu için ekstra logic yok.
- **Altınok:** Uzun ünlü flag'ini SAMPA çıktısında `:` ile işaretliyor; circumflex'siz formları homograph olarak tanımlıyor ama disambiguation otomatik değil.
- **MMS-TTS-tur (karakter-level):** Eğitim datasında circumflex varsa öğreniyor; yoksa default short okunuyor.

**Bizim için pratik:** Çocuk içeriğinde `kâr` gibi kelimeler nadir; ama `Lale`, `Halime`, `Adile` isimleri palatalization gerektiriyor. **Cunning exception dictionary** (circumflex'siz yazılan ama palatalize okunan isimler/kelimeler) gerekli.

### 2.5 Yabancı kelime epentezi (vowel epenthesis)

Türkçe başlangıçta consonant cluster izin vermez. Batı alıntıları yüksek ünlü eklenerek çözülür:

| Yazım | Konuşma gerçeği |
| --- | --- |
| Brüksel | Birüksel |
| spor | sıpor (geleneksel) |
| stratejik | sıtratejik |
| Kremlin | Kıremlin |

Modern eğitimli konuşmada epenthesis azaldı; çocuk konuşmasında daha sık. **Bizim çocuk-yönelimli use case'imiz için bu sub-domain önemli** — çocuğun doğal konuşmasında epenthesis var. Karakter Neeko'nun **çocuğun konuşma biçimini taklit etmesi gerekmiyor** (Neeko yetişkin gibi konuşan dost karakter); o yüzden epenthesis-free okumayı tercih ederiz, **ama çocuk girdi text'inde epenthesis-yazılmış kelimeler gelebilir** (ses-yazıştırma TTS değil ASR sorunu).

**Açık kaynak ele alışı:**
- espeak ve epitran epenthesis modellemez; cluster'ı bozulmuş okuyabilir.
- Altınok foreign words dictionary'sinde epenthesis kuralları var (CMUdict klonu).

### Bize özel öneri (5 madde)

1. **"Türkçe Phonetic Rule Manifest" yaz:** Tüm kuralları (uyum, softening, stress, circumflex, epenthesis) tek doküman, her kural için 5+ örnek. Bu manifest hem implementasyon spec, hem QA test seti, hem voice talent yönlendirmesi olur.
2. **Yer isimleri için **Türkiye Stress Dictionary** kur:** İl + ilçe + popüler yer ismi listesi + her birinin vurgu pozisyonu. TÜİK il listesi başlangıç (~970 ilçe), curated by-hand. Bunsuz "Ankara" robot okur.
3. **Pre-stressing suffix tablosu** + suffix tag'leme: morfolojik analyzer (Zemberek veya VNLP) ile ek-tipini tanı, stress'i ekten önceki heceye çek. Zemberek'in morphological analyzer'ı uygun ([github.com/ahmetaa/zemberek-nlp](https://github.com/ahmetaa/zemberek-nlp)).
4. **Circumflex restoration model:** Yazılı text'te düşmüş circumflex'i geri koy (`kar` ambig → context bakarak `kar` veya `kâr`). Küçük BERT fine-tune; Boğaziçi/dbmdz Turkish BERT taban. Çocuk content'inde nadir gelir ama isim okumada (`Lale`, `Adile`) kritik.
5. **Epenthesis policy karar:** Neeko **yetişkin-doğal Türkçe** konuşur (epenthesis yok); voice talent yönergesinde "Brüksel" gibi kelimeleri cluster ile okusun. Voice talent kontratına bu spec girer.

---

## 3. Türkçe Text Normalization — Açık Kaynak Matrisi

### 3.1 Hangi kütüphane neyi yapıyor?

| Kural | espeak-ng | num2words | trnorm | Zemberek | VNLP | NeMo-text-processing |
| --- | --- | --- | --- | --- | --- | --- |
| Cardinal sayı (`1234` → "bin iki yüz otuz dört") | ⚠️ kısıtlı | ✅ | ✅ | ❌ | ✅ | ❌ (TR yok) |
| Ordinal (`3.` → "üçüncü") | ❌ | ✅ (PR #468) | ✅ | ❌ | ⚠️ | ❌ |
| Roma rakamı (`II. Mahmut` → "ikinci Mahmut") | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Tarih (`1/1/2026` → "bir Ocak iki bin yirmi altı") | ❌ | ⚠️ year only | ⚠️ kısıtlı | ❌ | ❌ | ❌ |
| Saat (`14:30` → "on dört otuz") | ⚠️ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| Telefon (`+90 532 123 4567`) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Para (`100 ₺` → "yüz lira") | ❌ | ✅ (currency) | ✅ (sembol) | ❌ | ❌ | ❌ |
| Yüzde (`%50` → "yüzde elli") | ⚠️ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Kısaltma (`Dr.`, `TBMM`) | ❌ | ❌ | ❌ | ✅ (1057 entry) | ⚠️ | ❌ |
| Sembol (`€ $ ° m²`) | ⚠️ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Spelling correction | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| Deasciification (`siz → şiz`) | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |

**Kaynaklar:**
- num2words: [github.com/savoirfairelinux/num2words](https://github.com/savoirfairelinux/num2words) (Apache 2.0, v0.5.14, Aralık 2024). Turkish lang_TR.py PR #468 ile ordinal eklendi.
- trnorm: [github.com/ysdede/trnorm](https://github.com/ysdede/trnorm) (Apache 2.0, 2024)
- Zemberek normalization: [github.com/ahmetaa/zemberek-nlp/tree/master/normalization](https://github.com/ahmetaa/zemberek-nlp/tree/master/normalization) (Apache 2.0)
- Zemberek abbreviations: [github.com/ahmetaa/zemberek-nlp/blob/master/tokenization/src/main/resources/tokenization/abbreviations-long.txt](https://github.com/ahmetaa/zemberek-nlp/blob/master/tokenization/src/main/resources/tokenization/abbreviations-long.txt) — 1057 entry
- VNLP normalizer: [github.com/vngrs-ai/vnlp](https://github.com/vngrs-ai/vnlp) (AGPL-3.0, 2024)
- NeMo TN: [github.com/NVIDIA/NeMo-text-processing](https://github.com/NVIDIA/NeMo-text-processing) — Turkish yok; ar/de/en/es/fr/hi/hu/hy/it/ja/ko/pt/ru/rw/sv/vi/zh var.

### 3.2 Önemli not: NeMo Türkçe yok

NVIDIA NeMo'nun WFST-based text normalization repo'sunda 17 dil var, Turkish (`tr`) **yok**. Bu büyük bir boşluk — WFST yaklaşımı production-grade endüstri standardı (Google Sparrowhawk + NeMo). Türkçe için **NeMo-style WFST TN yazmak büyük açık iş kalemi**. Açık kaynak olarak yazılırsa upstream'e PR + community kredibilite.

### 3.3 Tarih ve saat — manual implement gerek

`1/1/2026`, `1 Ocak 2026`, `1.1.26` gibi format çeşitliliğini ele alan kamuya açık Türkçe library yok. trnorm cardinal+ordinal yapıyor ama tarih kompozisyonu yok. Saat için aynı durum (`14:30`, `2 buçuk`, `saat iki`).

**Tipik Türkçe okuma kuralları:**
- `1/1/2026` → "bir Ocak iki bin yirmi altı"
- `01.01.2026` → aynı
- `1 Ocak 2026` → aynı (direkt okunur)
- `14:30` formal → "on dört otuz" / konuşma → "iki buçuk"
- `09:45` formal → "dokuz kırk beş" / konuşma → "ona on beş kala"

Neeko çocuk-yönelimli olduğu için **konuşma diline yakın** okuma tercih edilir. "Saat dört" çocuğun "16:00"'ı anlaması için "saat dört" doğru; "on altı sıfır sıfır" yanlış register.

### 3.4 Telefon, IBAN, kart numarası

Hiçbir açık kaynak Türkçe TN library +90 telefon, IBAN, kart numarası okuma normalizasyonu yapmıyor. Çocuk content'inde nadir geçer ama Neeko'ya "telefon numaranı oku" gibi soru gelirse fallback gerek. Düşük öncelik.

### 3.5 Neural / BERT-based normalization

- **Sinan Göker (Hacettepe MSc 2018):** "Neural Text Normalization for Turkish Social Media" tezi ([burcu-can.github.io/SinanGoker-MscThesis.pdf](https://burcu-can.github.io/SinanGoker-MscThesis.pdf)) — contextual normalization + seq2seq. Sosyal medya odaklı (informal → formal), TTS frontend için doğrudan kullanım değil ama mimari fikir.
- **BERTurk (`dbmdz/bert-base-turkish-cased`):** 35GB corpus, 4.4B token. Token classification head ile TN-as-tagging mümkün — her token'a "keep/expand/replace" etiketi. Bu Google'ın production TN yaklaşımına yakın (Sparrowhawk).
- **TabiBERT (2025, arxiv:2512.23065):** ModernBERT mimarisinde Türkçe foundation model. Yeni; TN fine-tune için potansiyel taban.

**Neural TN'in pratik konumu:** Symbolic normalization (sayı, tarih, kısaltma) WFST/rule ile çözmek hala daha güvenilir. Neural sadece **bağlam-bağımlı disambiguation** için (homograph, kısaltma genişletme `Av.` = avukat mı, Av soyadı mı). Bizim için bu ileri faz.

### Bize özel öneri (5 madde)

1. **Faz-1 minimum TN stack:** `num2words` (sayı) + `trnorm` (ordinal/Roma/sembol) + `Zemberek` abbreviations dictionary + bizim curated `tarih_saat_okuma.py`. Bu kombinasyon Türkçe TTS frontend'in %85'ini kapsar.
2. **NeMo-style Türkçe TN, açık kaynak kazanç:** Pynini WFST ile Türkçe TN gramerleri yaz, NVIDIA NeMo-text-processing'e PR aç. 3-6 ay iş; ama "NEEKO Türkçe TN kütüphanesini açık kaynak yaptı" cümlesi marka değeri yüksek + community contributor pool çeker.
3. **Tarih/saat için çocuk-uygun register seç:** Neeko "saat iki buçuk" okur ("on dört otuz" değil). Bu çocuk content'inin tonu için hayati. Voice talent yönergesinde + TN library'de aynı kural.
4. **Kısaltma dictionary'sini Zemberek'ten al + curate et:** 1057 entry Zemberek başlangıç; çocuk content'ine uyarla (gereksiz olanları sil, eksik olanları ekle — `OYAK`, `MEB`, `MİLLİ EĞİTİM` vb. çocuk content'inde geçenler).
5. **Homograph disambiguation için neural katman (sonra):** BERTurk fine-tune + 1000-2000 curated örnek. `yüz` (yüz adet vs surat), `kara` (kara renk vs kara toprak), `ben` (ben zamiri vs ben işaret). Faz-2 iş, MVP'de değil.

---

## 4. Code-Switching (Türkçe-İngilizce karışım)

### 4.1 Realite

Türk konuşmasında İngilizce kelimeler çok yaygın: `WhatsApp'tan yazdım`, `iPhone'umu açtım`, `OK tamam`, `meeting'e geç kaldım`, `chat'leşelim`. **Çocuk content'inde** bile yaygın (`tablet`, `bilgisayar`, `oyun`).

### 4.2 Açık kaynak çözümler

| Kaynak | Ne | Bizim için |
| --- | --- | --- |
| [zeynepyirmibes/code-switching-tr-en](https://github.com/zeynepyirmibes/code-switching-tr-en) | Türkçe-İngilizce code-switching detection corpus | LID training data |
| Code-switching ASR/TTS literature | Genel patternler, Türkçe spesifik az | İlham, doğrudan kod yok |
| Phonological Adaptations of English Loanwords in Turkish ([digitalcommons.liberty.edu](https://digitalcommons.liberty.edu/cgi/viewcontent.cgi?article=1001&context=eml_undergrad_schol)) | Akademik analiz | Rule manifest için |

**Üretim hazır Türkçe-İngilizce karışık G2P pipeline kamuya açık değil.** Kendimiz kurmalıyız.

### 4.3 Mimari önerisi

```
Input text → Language ID per token → 
  ├─ TR token → tr-G2P (espeak-ng + override)
  ├─ EN token → en-G2P (espeak-ng en veya CMUdict)
  └─ Türkifiye olmuş EN (örn. "WhatsApp'tan") → tr-G2P + apostrof handling
→ Birleştirilmiş phoneme stream → TTS model
```

**Kritik karar noktası:** "WhatsApp" gibi token'lar gerçekte nasıl okunur?
- Tam İngilizce telaffuz: `/wɒtsæp/` — yapay, çoğu Türk böyle söylemez
- Türkçe accent İngilizce: `/wotsep/` veya `/vatsap/` — gerçeğe yakın
- Tam Türkçeleştirilmiş: `/vatsap/` — en doğal

Neeko'nun marka tonu açısından bu **karakter karar**: çocuğa konuşan Türk Neeko İngilizce kelimeleri Türk aksanı ile (orta yol) söylemeli. `iPhone` → "ayfon", `tablet` → "tablet" (zaten Türkçeleşmiş).

### 4.4 Apostrof ile İngilizce kök + Türkçe ek (`WhatsApp'tan`, `Google'a`)

Bu Türkçe'nin özel patterni: İngilizce kök + apostrof + Türkçe ek. Türkçe ek **kökün son ünlüsüne göre uyum sağlar**. `Google'a` → /guːglɑ/, `WhatsApp'tan` → consonant softening + suffix uyumu.

**Açık kaynakta bu doğrudan ele alınmaz.** Bizim curated handling gerekli:
1. Apostrof tokenizer (`WhatsApp'tan` → `WhatsApp` + `'tan`)
2. Foreign root pronunciation dictionary (`Google → /guːgl/`, `WhatsApp → /vatsap/`)
3. Türkçe suffix attachment with vowel harmony (foreign kökün son ünlüsüne göre)

### Bize özel öneri (3 madde)

1. **LID-first, language-specific G2P pipeline kur:** Token-level LID + her token'a uygun G2P. Curated foreign-Turkish dictionary (300-500 yaygın İngilizce kelime + Türkçe okunuş + suffix uyum kuralı) MVP için yeterli.
2. **Marka tonu kararı: orta-yol aksan:** Neeko İngilizce kelimeleri "doğal Türkçe konuşan birinin" tonuyla okur. Voice talent kontratında + G2P override sözlüğünde tutarlı.
3. **Apostrof + Türkçe ek için özel handler:** Regex `(\w+)'(\w+)` tokenization + foreign root lookup + Türkçe suffix vowel harmony post-process. Çocuk content'inde sık geçmez ama "Neeko'm" gibi karakter kendisini referans verirken kullanılır.

---

## 5. Türkçe Fonetik Sözlük ve Veri Kaynakları

### 5.1 Açık fonetik kaynaklar

| Kaynak | Boyut | Lisans | Notlar |
| --- | --- | --- | --- |
| [Rumeysakeskin/Turkish-Text-to-Speech CMUDict_tr](https://github.com/Rumeysakeskin/Turkish-Text-to-Speech) | ~1.5M kelime | Belirsiz, kontrol et | 36 fonem set; heteronyms ayrı dosya |
| [DuyguA/computational_linguistics/turkish-phonetic-lexicon](https://github.com/DuyguA/computational_linguistics/blob/master/turkish-phonetic-lexicon/turkish.lexicon) | 56.8 MB (binary indirilir) | Araştırma | SAMPA, Altınok 2016 |
| [Phonetisaurus tr modelleri](http://speechtechnology.web.illinois.edu/data/g2ps/) | Küçük | BSD-2 | Eğitim seti küçük, WFST |
| METUbet (METU Phonetic Alphabet) | Sözlük tanımı | Araştırma | TurkicASR corpus'unda kullanıldı |
| TDK fonetik sözlüğü | Kapalı | Telif | Erişim kapalı, scraping etik gri |

### 5.2 Speech corpora (G2P alignment için)

| Corpus | Boyut | Lisans | TR phoneme alignment? |
| --- | --- | --- | --- |
| Mozilla Common Voice Turkish | ~80 saat | CC0 | Yok, biz align ederiz |
| [issai/Turkish_Speech_Corpus](https://huggingface.co/datasets/issai/Turkish_Speech_Corpus) | 218 saat | CC-BY 4.0 | METUbet phonetic representation kullanıldı |
| ITU Turkish Broadcast News | 130 saat | Akademik | Kapalı dağıtım, ITU'dan istek |
| Boğaziçi BoUN UD treebank | 9.7K cümle | CC-BY-SA | Sözel, alignment yok |

### 5.3 Türkçe fonem inventarı (SAMPA + IPA)

Standart Türkçe SAMPA (8 ünlü + 26 ünsüz):

**Ünlüler:** `a e 1 i o 2 u y` (+ uzun varyantları `:` ile) — IPA: `/a e ɯ i o œ u y/` + `:` uzunluk
**Ünsüzler:** `p t tS k c b d dZ g gj f s S v w z Z m n N l 5 r j h G` — palatalizasyon, soft-g (`G` ↔ `ğ`)

(Kaynak: [help.voicemaker.in/turkish-phonemes](https://help.voicemaker.in/turkish-phonemes/), [Amazon Polly Turkish](https://docs.aws.amazon.com/polly/latest/dg/ph-table-turkish.html), Wikipedia Turkish phonology)

### Bize özel öneri (3 madde)

1. **Phoneme set: standart IPA (37-40 sembol), Latin Türkçe karakter desteği**: Tüm modelleri (espeak override, Misaki PR, fine-tune training) tek phoneme alphabet üzerinden besle. SAMPA ile mapper aracı yaz (Altınok kodu okurken çevirmen).
2. **`issai/Turkish_Speech_Corpus` (218 saat, CC-BY 4.0) + MFA align + curated lexicon kombosu = baseline G2P training data**. Eğer rule-based + lookup yetmezse, neural G2P fine-tune için bu corpus.
3. **`Rumeysakeskin CMUDict_tr` lisansını netleştir:** İlk hafta iş — repo sahibi ile iletişim, lisans dosyası, kullanım izni. Eğer Apache/MIT/CC-BY ise altın madeni; eğer "araştırma only" ise bizim curated kendi sözlüğümüzü kuracağız (~50K kelime başlangıç).

---

## 6. Karar Önerisi (Kararı Üreten Bölüm)

### NEEKO Turkish Frontend mimari önerisi (Faz-1, 0-6 ay)

```
text (Turkish + EN code-switch) 
  → tokenizer (apostrof + sentence boundary)
  → text normalizer
      ├─ num2words (sayı cardinal)
      ├─ trnorm (ordinal, Roma, sembol, %, ₺)
      ├─ tarih_saat_okuma.py (çocuk register)
      ├─ Zemberek abbreviations (curated subset)
      └─ NEEKO_exceptions.json (özel isim, marka, karakter)
  → LID per token
  → G2P
      ├─ Turkish token → espeak-ng + NEEKO_phoneme_overrides.json
      │     + place_name_stress.json + circumflex_restore.bert
      ├─ English token → CMUdict / espeak en
      └─ Foreign+Turkish suffix (`Google'a`) → curated foreign root + suffix uyum
  → phoneme stream (IPA)
  → TTS model (XTTS/F5/Misaki+Kokoro)
```

### Niçin bu seçim?

1. **espeak-ng taban olarak:** GPL-3.0 lisansı dağıtım için karmaşık ama process-level kullanım (subprocess çağrı) lisans yayılımı yaratmıyor. Resmi PyTorch TTS ekosistemi (XTTS, StyleTTS2, F5-TTS, Piper, Coqui) hep espeak destekli — uyumluluk avantajı.
2. **Override katmanı bizim diferansiyatörümüz:** Çocuk-yönelimli vocabulary üzerinde curate edilmiş; karakter Neeko'nun isim okuma, marka adı okuma, çocuk kitabı kelimeleri özel. Bu **bizim moat'umuz** — başkası bu sözlüğü 6 ayda kuramaz.
3. **num2words + trnorm + Zemberek = mevcut, Apache 2.0, üretim hazır:** Ekosistem dışı bağımlılık ekleyerek tekerleği yeniden icat etmiyoruz. Yapılması gereken kompozisyon ve gap-fill.
4. **NeMo TN Türkçe gap'i strateji:** Yazılması gereken hâlâ var, ama MVP'de değil. Faz-2 (6-12 ay): Pynini WFST grammar; sürüm sonrası açık kaynak PR.
5. **LID + code-switch:** Faz-1 minimum (regex + curated foreign root); Faz-2 ML LID (PyConll/glotto-id).

### Ne ile başlamayalım?

- **Sıfırdan neural G2P eğitme:** Veri var, kaynak var, ama 1.5M parametre G2P modelin marjinal kalite kazancı (rule-based baseline'ı geçmek için) zaman değil. İlerde XPhoneBERT türü phoneme-aware LM TTS kalitesini çekerse ek katman.
- **Misaki'ye direkt commit:** Önce bizim production sürümümüz olsun. Misaki PR'ı pazarlama hamlesi.
- **VNLP'ye bağımlılık:** AGPL-3.0; ticari ürün için risk. VNLP'nin TN logic'ini referans olarak oku, doğrudan import etme.

### İlk hafta concrete next steps

1. `Rumeysakeskin/Turkish-Text-to-Speech` repo'sunu klonla, MANIFEST/LICENSE oku. Lisans uyuyorsa CMUDict_tr'yi `data/phonemes/turkish-lexicon-v0.csv` olarak kaydet.
2. Altınok 2016 paperı'nın repo'sunu (`DuyguA/computational_linguistics/rule_based_g2p`) klonla, kuralları liste olarak çıkar (`docs/architecture/turkish-phonetic-rules-manifest.md`).
3. `num2words tr` + `trnorm` + `Zemberek abbreviations` ile bir prototype TN script (`src/g2p/tr_text_normalizer.py`) — 200 örnek üzerinde golden output (`data/test-sets/tn-test-200.json`).
4. espeak-ng (Turkish) ile baseline phoneme çıktısı al; 100 cümlelik child-oriented test set üzerinde (`data/test-sets/g2p-eval-100.json`) hangi kelimelerin yanlış okunduğunu listele. Bu **NEEKO_phoneme_overrides.json**'un ilk seed'i.
5. Place name stress için TÜİK il/ilçe listesi (~970 entry) + by-hand vurgu pozisyonu işaretleme. `data/phonemes/turkish-place-names-stress.json`.

---

## Kaynaklar (Tarihli Link Listesi)

### Akademik
- Altınok, D. (2016). "Towards Turkish ASR: Anatomy of a rule-based Turkish g2p." [arxiv.org/abs/1601.03783](https://arxiv.org/abs/1601.03783) (Ocak 2016)
- Inkelas, S. (1994). "Exceptional stress-attracting suffixes in Turkish." [roa.rutgers.edu/files/39-1294/39-1294-INKELAS-0-0.PDF](https://roa.rutgers.edu/files/39-1294/39-1294-INKELAS-0-0.PDF)
- Göker, S. (2018). "Neural Text Normalization for Turkish Social Media." Hacettepe MSc. [burcu-can.github.io/SinanGoker-MscThesis.pdf](https://burcu-can.github.io/SinanGoker-MscThesis.pdf)
- The Nguyen et al. (2023). "XPhoneBERT: A Pre-trained Multilingual Model for Phoneme Representations for Text-to-Speech." INTERSPEECH 2023. [arxiv.org/abs/2305.19709](https://arxiv.org/abs/2305.19709) (Mayıs 2023). Turkish (`tur`) destekli.
- TabiBERT (2025). "A Large-Scale ModernBERT Foundation Model for Turkish." [arxiv.org/abs/2512.23065](https://arxiv.org/abs/2512.23065)
- Wikipedia: [Turkish phonology](https://en.wikipedia.org/wiki/Turkish_phonology), [Help:IPA/Turkish](https://en.wikipedia.org/wiki/Help:IPA/Turkish)

### Açık kaynak kütüphaneler (G2P)
- espeak-ng: [github.com/espeak-ng/espeak-ng](https://github.com/espeak-ng/espeak-ng) (GPL-3.0, aktif 2025). Turkish `dictsource/tr_rules` (364 satır). Issue #152 Turkish pronunciation fixes resolved (2016).
- phonemizer: [github.com/bootphon/phonemizer](https://github.com/bootphon/phonemizer) (GPL-3.0, espeak wrapper)
- epitran: [github.com/dmort27/epitran](https://github.com/dmort27/epitran) (MIT, Turkish: `tur-Latn`, `tur-Latn-bab`, `tur-Latn-red`, `tur-Latn-nosuf`)
- CharsiuG2P: [github.com/lingjzhu/CharsiuG2P](https://github.com/lingjzhu/CharsiuG2P) (MIT, ByT5, 100 dil, PER 0.089 ortalama)
- Misaki: [github.com/hexgrad/misaki](https://github.com/hexgrad/misaki) (Apache 2.0; Turkish henüz yok, StyleTTS2 issue #325 üzerinde topluluk çalışması)
- Altınok rule_based_g2p: [github.com/DuyguA/computational_linguistics/tree/master/rule_based_g2p](https://github.com/DuyguA/computational_linguistics/tree/master/rule_based_g2p) (terkedilmiş, araştırma)
- Phonetisaurus: [github.com/AdolfVonKleist/Phonetisaurus](https://github.com/AdolfVonKleist/Phonetisaurus) (BSD-2, WFST G2P)

### Açık kaynak kütüphaneler (TN / NLP)
- num2words: [github.com/savoirfairelinux/num2words](https://github.com/savoirfairelinux/num2words) (LGPL-2.1, v0.5.14 Aralık 2024). Turkish lang_TR.py, PR #468 ordinal.
- trnorm: [github.com/ysdede/trnorm](https://github.com/ysdede/trnorm) (Apache 2.0, 2024). ASR benchmark için Türkçe normalize.
- Zemberek-NLP: [github.com/ahmetaa/zemberek-nlp](https://github.com/ahmetaa/zemberek-nlp) (Apache 2.0). Normalization README: [normalization/README.md](https://github.com/ahmetaa/zemberek-nlp/blob/master/normalization/README.md). Abbreviations: 1057 entry.
- VNLP: [github.com/vngrs-ai/vnlp](https://github.com/vngrs-ai/vnlp) (AGPL-3.0). Normalizer + morphological analyzer.
- NeMo-text-processing: [github.com/NVIDIA/NeMo-text-processing](https://github.com/NVIDIA/NeMo-text-processing) (Apache 2.0). Turkish desteklenmiyor — 17 dil var.
- BERTurk: [huggingface.co/dbmdz/bert-base-turkish-cased](https://huggingface.co/dbmdz/bert-base-turkish-cased) (MIT, 35GB corpus)

### Türkçe TTS modelleri ve datalar
- MMS-TTS-tur: [huggingface.co/facebook/mms-tts-tur](https://huggingface.co/facebook/mms-tts-tur) (CC-BY-NC 4.0, karakter-level VITS, phoneme yok)
- XTTS-v2: [huggingface.co/coqui/XTTS-v2](https://huggingface.co/coqui/XTTS-v2) (CPML, Turkish destekli, 17 dil)
- Piper Turkish voices: dfki, fahrettin, fettah — medium quality, espeak-ng frontend ([github.com/rhasspy/piper/blob/master/VOICES.md](https://github.com/rhasspy/piper/blob/master/VOICES.md))
- IS2AI TurkicTTS: [github.com/IS2AI/TurkicTTS](https://github.com/IS2AI/TurkicTTS) (Tacotron 2 + 10 Turkic dil + IPA-based converter)
- Rumeysakeskin Turkish-TTS: [github.com/Rumeysakeskin/Turkish-Text-to-Speech](https://github.com/Rumeysakeskin/Turkish-Text-to-Speech) (CMUDict_tr ~1.5M kelime)
- Turkish Speech Corpus ISSAI: [huggingface.co/datasets/issai/Turkish_Speech_Corpus](https://huggingface.co/datasets/issai/Turkish_Speech_Corpus) (218 saat, METUbet)

### Code-switching
- Yirmibeş, Z. "Turkish-English code-switching detection corpus": [github.com/zeynepyirmibes/code-switching-tr-en](https://github.com/zeynepyirmibes/code-switching-tr-en)
- "Phonological Adaptations of English Loanwords in Turkish": [digitalcommons.liberty.edu/cgi/viewcontent.cgi?article=1001&context=eml_undergrad_schol](https://digitalcommons.liberty.edu/cgi/viewcontent.cgi?article=1001&context=eml_undergrad_schol)

### Referans (ticari, sadece benchmark için)
- Amazon Polly Turkish IPA tablosu: [docs.aws.amazon.com/polly/latest/dg/ph-table-turkish.html](https://docs.aws.amazon.com/polly/latest/dg/ph-table-turkish.html)
- VoiceMaker Turkish IPA: [help.voicemaker.in/turkish-phonemes](https://help.voicemaker.in/turkish-phonemes/)

---

**Toplam taranan repo/paper sayısı:** ~30+ · **İncelenen README/code:** 12 · **Doğrudan kaynak link:** 50+
