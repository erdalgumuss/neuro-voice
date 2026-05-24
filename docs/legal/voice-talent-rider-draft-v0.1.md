# Voice Talent Rider Sözleşme Taslağı — v0.1

**Tarih:** 2026-05-19
**Statü:** **TASLAK** — Hukuk firması tarafından final inceleme + Türk hukuk uyumluluğu doğrulaması gerekir
**Amaç:** Voice talent ile imzalanacak sözleşmenin iskeleti — hukuk firmasına gönderilecek baz belge

---

## ⚠️ Önemli uyarı

Bu belge **hukukçu olmayan tarafından (Atlas + Erdal) hazırlanmış bir başlangıç taslağıdır**. Türk Borçlar Kanunu, 6698 sayılı KVKK, 5846 sayılı FSEK, TMK ve ilgili mevzuatın uygulaması konusunda **hukuk firması final incelemesi zorunludur**. Bu draft sadece "ne istiyoruz, hangi maddeler olmalı" çerçevesini hukuk firmasına aktarmak için yazılmıştır.

**Hukuk firmasına özel sorulacak konular:**
1. KVKK m.6 kapsamında ses biyometrik veri statüsü ve özel nitelikli veri işleme rejimi
2. FSEK m.80 (icracı sanatçı mali hakları) + m.80/A (manevi haklar — devredilemez) ayrımı
3. TMK m.24 + TBK m.49 (kişilik hakkı geri çekme) çerçevesinde "irrevocable buyout"un Türk hukuku altında sınırları
4. AI training data + synthetic voice generation için ayrı rider geçerli mi yoksa ana sözleşmeye entegre mi
5. Cross-border data transfer (yurt dışı GPU compute) için KVKK uyum mekanizmaları
6. NQAI henüz kurulu olmadığı için sözleşme tarafı: "Erdal Mert Karaaslan / NeuroQubit Tic." mi yoksa kurulacak şirket adına ön sözleşme mi

---

## 1. Taraflar

**İşveren / Hak Devralan:** Erdal Mert Karaaslan — NeuroQubit (NQAI kurulduğunda devredilecek)
T.C. Kimlik: [doldurulacak]
Adres: [doldurulacak]
E-posta: uwh3838@gmail.com
("İşveren" veya "Şirket" olarak anılacaktır)

**Voice Talent / Hak Devreden:** [Aday adı]
T.C. Kimlik: [doldurulacak]
Adres: [doldurulacak]
("Voice Talent" veya "Sanatçı" olarak anılacaktır)

İşveren ve Voice Talent toplu olarak "Taraflar" olarak anılacaktır.

---

## 2. Sözleşmenin konusu

Voice Talent, NEEKO karakterinin sesi olarak hizmet vermek ve İşveren'in yapay zeka tabanlı metin-konuşma (TTS) sisteminde kullanılmak üzere ses kayıtları üretmek ve aşağıdaki Madde 6 kapsamında haklarını devretmek üzere bu Sözleşme'yi imzalar.

Voice Talent'ın sesi, Voice Talent tarafından İşveren'in stüdyosunda yapılan kontrollü kayıtlarla üretilecek ve İşveren tarafından geliştirilen yapay zeka modeli (TTS LoRA fine-tune) eğitiminde kullanılacaktır. Eğitim sonucu üretilen "Synthetic Voice Model", NEEKO karakterinin sesi olarak ürün ekosistemi içinde kullanılacaktır.

---

## 3. Hizmet kapsamı

### 3.1 Casting / Audition

Voice Talent, sözleşme öncesinde 10-15 dakikalık casting kaydı yapmıştır ve İşveren tarafından final aday olarak seçilmiştir. Casting kaydı bu sözleşme kapsamında kullanım hakkına dahildir.

### 3.2 Ana stüdyo kaydı

- **Süre:** 3-4 gün, günde 1-1.5 saat seans
- **Yer:** İşveren tarafından sağlanan profesyonel stüdyo (İstanbul/Ankara)
- **Format:** 48 kHz / 24-bit WAV, sıkıştırılmamış
- **İçerik:** 5 mod (storytelling / lesson / play / sleep / Q&A), toplam 3-4 saat işlenmiş ham kayıt
- **Yönlendirme:** Voice director eşliğinde, NEEKO karakter spec'i (Ek 1) çerçevesinde
- **Materyal:** İşveren tarafından sağlanan prompt pack (Ek 2) okunacaktır

### 3.3 Ek kayıt SLA (sözleşme süresi boyunca)

- Yılda **1-2 ek kayıt günü** (her biri 1-1.5 saat seans), İşveren talebi üzerine
- Maksimum **30 gün önceden bildirim**
- Sabit ek ücret (Madde 4.2)

---

## 4. Ücret + ödeme

### 4.1 Buyout ana ücret

**Net buyout:** [TL tutarı, 40.000-90.000 TL aralığında — premium aday için 100.000-150.000 TL'ye kadar].

**Ödeme planı:**
- %30 sözleşme imzasında
- %40 final stüdyo kaydı tamamlandığında
- %30 model eval geçtiğinde (Synthetic Voice Model release approval'dan sonra, maks. sözleşmeden 90 gün sonra)

Tüm ödemeler [banka hesabı] üzerinden, TL olarak yapılacaktır. KDV ve diğer vergiler İşveren tarafından karşılanır.

### 4.2 Ek kayıt günü ücreti

Her ek kayıt günü için **sabit ücret:** [örn. 5.000-8.000 TL net]. Bu ücret yılda 1-2 ek kayıt günü için geçerlidir; ekstra gün gerekirse karşılıklı anlaşmayla.

### 4.3 Diğer ücretler

- **Casting fee (final aday için)** ana buyout'a dahildir
- **Reddedilen adaylar için casting fee:** 3.000-5.000 TL net + ulaşım (sözleşme kapsamı dışı)

---

## 5. Sözleşme süresi

**Ana süre:** [5-10] yıl, sözleşme imzasından itibaren.

**Opsiyon:** İşveren tek taraflı olarak süreyi [5] yıl daha uzatabilir (uzatma bildirimini süre bitiminden 90 gün önce yapmak şartıyla); uzatma ücreti ana buyout'un %30'u kadar ek ödeme ile gerçekleşir.

**Sonra:** Sözleşme süresi bittiğinde Synthetic Voice Model üretimi durdurulur. Tarafların yazılı anlaşmasıyla yeni sözleşme yapılabilir.

---

## 6. Hak devri ve kullanım kapsamı (KRİTİK MADDELER)

### 6.1 Synthetic Voice + AI Training Rights — Açık rıza

Voice Talent açıkça beyan ve kabul eder ki:

(a) Bu sözleşme kapsamında üretilen ses kayıtları **yapay zeka modeli eğitiminde (TTS LoRA fine-tune) kullanılacaktır**;
(b) Eğitim sonucu üretilen **Synthetic Voice Model**, Voice Talent'ın sesine benzer ancak bağımsız bir sentetik üretim aracıdır;
(c) Synthetic Voice Model, bu sözleşmede tanımlanan kapsamlar dahilinde, **sayısız sentetik konuşma üretmek** için kullanılabilir;
(d) Bu kullanım için **açık rıza**sını verir.

Bu rıza, **KVKK m.6 kapsamında özel nitelikli kişisel veri işleme açık rızası** olarak ayrıca imzalanan KVKK Aydınlatma Metni (Ek 3) ile birlikte alınır.

### 6.2 Kullanım kapsamı

Synthetic Voice Model aşağıdaki ürün ve coğrafyalarda kullanılabilir:

**Ürün ailesi:**
- **NEEKO** (çocuk oyuncağı + companion app + NEEKO marketplace) — **dahil**
- **NIVA** (call-center / yetişkin AI agent) — **opsiyonel, ayrı ek ücret ile** (Madde 6.3)
- **NeuroCourse** (eğitim platformu eğitmen sesi) — **opsiyonel, ayrı ek ücret ile**
- **NARO** (Erdal'ın belirleyeceği ürün) — **opsiyonel, ayrı ek ücret ile**
- **NQAI dış lisans hattı** (Faz-3, eğer NQAI Türkçe TTS API olarak açılırsa) — **opsiyonel, ayrı ek ücret ile**

**Coğrafya:** Türkiye + uluslararası (ihracat, dijital dağıtım dahil).

**Dil:** Türkçe (birincil). Diğer diller (multilingual fine-tune ihtimali) için ek mutabakat gerekir.

**Platform:** Tüm dijital + fiziksel platformlar (oyuncak, mobil uygulama, web, ses asistanı, dijital içerik, reklam — NEEKO çatısı altında).

### 6.3 Ek ürün kullanımı

NIVA, NeuroCourse, NARO veya NQAI dış lisans için Synthetic Voice Model kullanımı, **karşılıklı yazılı mutabakat ve ek ücret** ile devreye girer. Ek ücret oranı ana buyout'un **%30-50'si** aralığındadır (her ürün için ayrı).

### 6.4 Veri ve model mülkiyeti

Aşağıdakilerin **tüm fikri ve sınai mülkiyet hakları kayıtsız şartsız İşveren'e aittir**:

- Stüdyo kayıt dosyaları (raw audio dataset)
- Bu kayıtların post-processing'i ve etiketlemeleri
- Bu kayıtlar üzerinde eğitilen tüm model ağırlıkları (base model + LoRA adapter)
- Synthetic Voice Model'in ürettiği tüm output'lar (generated audio)
- Voice fingerprint embedding'leri ve karakter referans setleri
- Bu sözleşme kapsamında ortaya çıkan tüm türev eserler

Voice Talent, **bu hakları geri alma talebinde bulunmayacağını** beyan eder; ancak Madde 8 (sunset clause) saklıdır.

### 6.5 FSEK m.80 — İcracı sanatçı mali hakları devri

Voice Talent, FSEK m.80 kapsamında bu kayıt performansı üzerindeki **tüm mali hakları**nı (tespit, çoğaltma, yayma, temsil, işleme, kiralama, ödünç verme, umuma iletim) **kayıtsız şartsız ve süresiz** olarak İşveren'e devreder.

**FSEK m.80/A — manevi haklar:** Türk hukukunda icracı sanatçının manevi hakları (sanatçı olarak adının belirtilmesi, performansın bütünlüğünün korunması) **devredilemez**. Taraflar bu hususta:
- Synthetic Voice Model çıktısında Voice Talent'ın adı belirtilmez (karakter NEEKO'dur, sanatçı arka plandadır)
- Voice Talent kendi resmi biyografisinde "NEEKO karakterinin sesi" referansını verebilir (gizlilik şartlarına uymak kaydıyla — Madde 11)
- Performansın bütünlüğü, NEEKO karakter spec'i çerçevesinde "uygun kullanım"la korunmuş sayılır

---

## 7. Münhasırlık

**Çocuk oyuncak + içerik kategorisinde (3-7 yaş hedef kitle):** Voice Talent, sözleşme süresince **rakip ürünlerde** (Tonies, LeapFrog, VTech veya benzeri etkileşimli çocuk oyuncak ürünleri; Pepee/Niloya/Çakıl Çocuk gibi büyük çocuk karakter franchise'larına yeni karakter olarak katılım) Synthetic Voice Model'e ait sesin veya benzerinin kullanılmasını taahhüt etmez ve **kendi sesini benzer kategorideki** rakip karakterler için yeni bir AI training data olarak vermez.

**Diğer kategoriler:** Yetişkin haber, kurumsal reklam, dublaj (yetişkin içerik), audiobook (yetişkin), korunmuş genel seslendirme — **non-exclusive**, Voice Talent serbestçe başka projelerde çalışabilir.

---

## 8. Sunset clause — Kişilik hakkı geri çekme senaryosu

Türk hukukunda kişilik hakkı (TMK m.24) bütünüyle ve süresiz olarak devredilemez. Bu çerçevede:

### 8.1 Geri çekme hakkı

Voice Talent, aşağıdaki **olağanüstü hallerden** birinde, kişilik hakkı kapsamında Synthetic Voice Model'in **yeni içerik üretiminin durdurulmasını** talep edebilir:

(a) Voice Talent'ın açık ve geri dönülmez biçimde itibarının zedelendiği bir kullanım (örn. siyasi propaganda, yetişkin içerik, dolandırıcılık) yapılmış olması — bu durumda **derhal** durdurma talep edilebilir
(b) İşveren'in sözleşme şartlarını ciddi şekilde ihlal etmesi — bu durumda Voice Talent yazılı bildirim sonrası durdurma talep edebilir
(c) Voice Talent'ın yaşamını/sağlığını ciddi biçimde etkileyen olağanüstü kişisel durum — bu durumda **12 aylık geçiş süreci** uygulanır

### 8.2 12 aylık geçiş süreci (olağan geri çekme)

Eğer Voice Talent (c) bendi kapsamında geri çekme talep ederse:

- **0-3 ay:** İşveren mevcut içerik üretimini sürdürür; yeni karakter geliştirme durdurulur
- **3-9 ay:** Yedek karakter sesi geçişi planlanır (alternatif voice talent + LoRA fine-tune); mevcut içerik kullanılmaya devam eder
- **9-12 ay:** Yedek karakter sesi production'a alınır; eski Synthetic Voice Model yeni içerik üretiminden çekilir
- **12+ ay sonrası:** Eski Synthetic Voice Model arşivlenir, kullanım sadece mevcut release edilmiş içerikle sınırlı

### 8.3 Tazminat formülü (geri çekme halinde)

Voice Talent kişilik hakkı geri çekme talebinde bulunursa:

- (a) ve (c) bentlerinde (Voice Talent'ın iyi niyetli iradesi): İşveren, Voice Talent'a ödenen buyout tutarının **%30-50'si** oranında tazminat ödemez; aksine Voice Talent İşveren'e **yedek karakter geçiş maliyetini** (yeni casting + yeni kayıt + yeni LoRA fine-tune + ürün geçiş maliyeti) tazminat olarak karşılayabilir. Tutar bilirkişi raporu ile belirlenir.
- (b) bendinde (İşveren'in ihlali): Voice Talent İşveren'den **tüm buyout artı %50 tazminat** talep edebilir; geri çekme derhal yürürlüğe girer.

### 8.4 Olağanüstü kullanım yasakları

İşveren, **hiçbir koşulda** Synthetic Voice Model'i aşağıdaki amaçlarla kullanmayacağını beyan ve taahhüt eder:

- Politik propaganda veya siyasi parti faaliyetleri
- Yetişkin / cinsel içerik
- Yanıltıcı / dolandırıcılık / sahte haber üretimi
- Üçüncü kişiyi taklit eden içerik
- Voice Talent'ın itibarını zedeleyebilecek herhangi bir kullanım

Bu maddenin ihlali halinde Voice Talent **derhal geri çekme** ve **buyout artı %100 tazminat** talep edebilir.

---

## 9. KVKK + veri güvenliği

### 9.1 Kişisel veri işleme

Voice Talent, KVKK m.5 ve m.6 kapsamında **özel nitelikli kişisel verisi (ses biyometrik veri)** İşveren tarafından **açık rıza ile** işlenmesine onay verir. Açık rıza KVKK Aydınlatma Metni (Ek 3) ile imzalanarak alınır.

### 9.2 Veri saklama

- Raw audio dataset: **şifreli storage**, retention 10 yıl
- Model ağırlıkları (LoRA adapter): şifreli, encrypted-at-rest
- Erişim kontrolü: least privilege, audit log
- Yurt dışı veri aktarımı (cloud GPU): KVKK uyum mekanizması (anonymization veya yeterli koruma sağlanan ülkeler) ile

### 9.3 Veri sahibi hakları

Voice Talent, KVKK m.11 kapsamında verilerinin işlenmesi, silinmesi, aktarılması hakkında bilgi alma + itiraz hakkına sahiptir. Bu haklar Aydınlatma Metni'nde detaylandırılmıştır.

### 9.4 Veri silme

Sözleşme süresi sonunda veya sunset clause sonunda, raw audio dataset Voice Talent'ın yazılı talebi üzerine **silinir veya geri verilir**. Model ağırlıkları (LoRA adapter) ve generated output'lar İşveren mülkiyetinde kalır (Madde 6.4).

---

## 10. Yasaklı kullanım (Voice Talent'ı koruyan madde)

Madde 8.4'e ek olarak, İşveren aşağıdaki kullanımları taahhüt eder ki yapmaz:

- Voice Talent'ın **gerçek kimliğini ifşa eden** kullanım (kamuya açıklamayla — Voice Talent'ın izni olmadan)
- **Religion, dini içerik, mezhep/inanç polemiği** üretiminde NEEKO karakterini kullanma
- Voice Talent'ın **fiziksel görüntüsü** ile birleştirilmiş deepfake video
- Voice Talent'a önceden bildirilmeyen **yeni ürün hatlarında** kullanım (Madde 6.3 mutabakatı olmadan)

---

## 11. Gizlilik (NDA)

Voice Talent, bu sözleşmenin imza tarihinden itibaren **5 yıl** boyunca aşağıdakileri **gizli** tutmayı taahhüt eder:

- Sözleşmenin mali şartları (buyout tutarı, ek kayıt ücretleri)
- NEEKO karakter spec'inin teknik detayları
- Casting + audition süreç detayları
- İşveren'in iş stratejisi ve diğer ürünler (NIVA, NeuroCourse, NARO)
- Synthetic Voice Model teknik detayları (model mimarisi, fine-tune parametreleri)

**İstisna:** Voice Talent kendi resmi biyografisinde **"NEEKO karakterinin sesi"** referansını verebilir (sadece kategorik bilgi, teknik/mali detay olmaksızın).

---

## 12. Sözleşmenin sona ermesi

Sözleşme aşağıdaki durumlarda sona erer:
- Sözleşme süresinin dolması (Madde 5)
- Sunset clause kapsamında geri çekme (Madde 8)
- Karşılıklı yazılı anlaşma
- Tarafların ölümü (mirasçılarına ek hak/yükümlülük geçer)

Sözleşme sona erdiğinde, **Synthetic Voice Model üretimi durur**, ancak Sözleşme süresince üretilmiş içerik (NEEKO ürün ekosisteminde yayınlanmış) **kullanılmaya devam eder**.

---

## 13. Uyuşmazlık çözümü

Bu sözleşmeden doğan uyuşmazlıklarda **Türk Hukuku** uygulanır.

**İlk başvuru:** Taraflar uyuşmazlığı yazılı bildirimle ortaya koyduktan sonra **60 gün** içinde dostane çözüm arar.

**Tahkim/mahkeme:** Dostane çözüm sağlanamazsa, **İstanbul Merkezi Tahkim Kurulu (ISTAC)** kuralları çerçevesinde tahkime başvurulur. Tahkim dili Türkçe, hakem sayısı tek hakemdir (taraflar anlaşamazsa ISTAC tayin eder).

---

## 14. Genel hükümler

- **Kısmi geçersizlik:** Bir maddenin geçersizliği, sözleşmenin diğer maddelerinin geçerliliğini etkilemez.
- **Tadilat:** Sözleşmede değişiklik ancak **iki tarafın yazılı mutabakatı** ile yapılabilir.
- **Tebligat:** Tarafların sözleşmede belirtilen e-posta + fiziksel adresleri tebligat için geçerlidir.
- **İmza:** Bu sözleşme [tarih] tarihinde, iki nüsha olarak, taraflar arasında imzalanmıştır. Her iki nüsha aynı hukuki değere sahiptir.

---

## Ekler

**Ek 1:** NEEKO Karakter Ses Spec'i v1.0 — `docs/character/neeko-v1-spec.md`
**Ek 2:** Audition + Final Kayıt Prompt Pack — `data/casting-prompts/v0.1.md` (audition için), final kayıt prompt pack ayrı belge olarak hazırlanacak
**Ek 3:** KVKK Aydınlatma Metni + Açık Rıza Beyanı — hukuk firması tarafından final form
**Ek 4:** Voice Talent NDA — hukuk firması tarafından final form

---

## İmzalar

**Voice Talent:**
İsim: ___________________
T.C. Kimlik: ___________________
Tarih: ___________________
İmza: ___________________

**İşveren (NeuroQubit / Erdal Mert Karaaslan):**
İsim: Erdal Mert Karaaslan
T.C. Kimlik: ___________________
Tarih: ___________________
İmza: ___________________

**Tanık 1:**
İsim: ___________________
İmza: ___________________

**Tanık 2:**
İsim: ___________________
İmza: ___________________

---

## Hukuk firması için kontrol listesi

Aşağıdaki başlıklarda hukuk firması validation yapacak:

- [ ] KVKK m.6 (özel nitelikli veri) + açık rıza formu Türk hukukuna uygunluk
- [ ] FSEK m.80 ve m.80/A devir/devredilemezlik ayrımı doğru ifade
- [ ] TMK m.24 + TBK m.49 kişilik hakkı geri çekme sınırları doğru
- [ ] Sunset clause + tazminat formülü orantılılık ilkesi açısından
- [ ] Münhasırlık + non-compete maddesi Türk hukuku açısından (anti-trust)
- [ ] Cross-border data transfer (KVKK m.9) açık rıza + güvenlik şartları
- [ ] NQAI henüz kurulu değil — sözleşme tarafı olarak Erdal Mert Karaaslan / NeuroQubit ile ileride NQAI'a devir maddesi
- [ ] Tahkim/mahkeme + dostane çözüm + bilirkişi raporu maddeleri
- [ ] İmza prosedürü + tanık + noter onayı gereksinimi
- [ ] KVKK Aydınlatma Metni ve NDA ek belgelerinin final formu
- [ ] Buyout tutarı serbest piyasa koşullarına uygunluk (gerçi bu mali, ama anti-trust açısından kontrol)

---

**Atlas notu:** Bu taslak hukuki kapsam için iskelet sunar. Voice Talent ile imzalamadan önce hukuk firması final inceleme ve revizyonu zorunlu. Erdal'ın "Hukuk firması outreach mail metni" görevinde bu draft eklenerek firmaya yollanacak.
