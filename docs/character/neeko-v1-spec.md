# NEEKO Karakter Ses Spec'i — v1.0 (taslak)

**Tarih:** 2026-05-19
**Hazırlayan:** Atlas + Erdal
**Durum:** v1.0 taslak — Erdal onayı bekleniyor
**Kullanım:** Voice talent casting brief'i, audition prompt pack'i, rider sözleşmesi ve LoRA fine-tune yönelimi bu belgeden türer

---

## Neeko kim, ne yapar

Neeko, **3-7 yaş Türk çocuklarına eşlik eden bir karakter** — peluş kabuk içinde edge AI çalıştıran etkileşimli oyuncak. Çocuğun günlük ritüellerinde (oyun, ders, masal, uyku öncesi, soru-cevap) yanında olan sıcak bir arkadaş. Tonies modelinde, içerik/figür/DLC ekonomisiyle çalışan bir karakter ürünü.

Karakterin tematik özünde: **meraklı, oyuncu, sevecen, yargılayıcı olmayan, ebeveynle çocuk arasındaki bağı pekiştiren** (azaltmayan) bir arkadaş. Sosa 2016'nın elektronik oyuncak anti-pattern bulgusu (etkileşim azaltıcı oyuncak riski) Neeko'nun tasarım dışlama kriteri.

---

## Ses kararı — özet tablo

| Boyut | Karar | Gerekçe |
|---|---|---|
| **Cinsiyet** | **Nötr / cinsiyetsiz (androgynous)** | Tonies dahil çoğu çocuk karakteri kadın; NEEKO'yu cinsiyet algısı muğlak tutmak modern + inclusive konumlanma sağlar, her cinsiyet çocuk kendini özdeşleştirebilir, gender stereotyping yok. Marka açısından da farklılaştırıcı (Pepee/Niloya'dan ayrışma). |
| **Yaş hissi (karakter)** | Yetişkin warm guide (çocuk taklidi YASAK) | Çocuk taklidi sesler uzun dinlemede yorucu olur ve sahte heyecan üretir; yetişkin warm + child-directed prozodi daha sürdürülebilir |
| **Yaş aralığı (seslendirici)** | 25-40 (cinsiyet bağımsız) | Vocal stamina + child-directed deneyim + olgun warmth kesişimi |
| **Aksent** | **Temiz İstanbul Türkçesi** | NQAI dört üründe ortak omurga; yöresel aksan kapsam dışı; haber spikeri tonu da yasak (warm + günlük) |
| **Karakter persona** | Sıcak, peluş kabuk içindeki güvenilir arkadaş — cinsiyetsiz, yaşsız fantastik karakter hissi | Karakter bir çocuk olsa bile sesi olgun-warm; markalaşma için tutarlı + ayrıştırıcı kimlik |
| **Pedagojik tutum** | Etkileşim tetikleyici, ebeveyne yönlendiren | Sosa 2016 anti-pattern kontrolü: çocuk-AI tek başına yerine çocuk-ebeveyn-AI üçlüsünü kuran |

---

## Karakter tonu paleti — 5 mod

Neeko'nun konuşma dağarcığı beş mod altında çalışır. Voice talent kayıt seansları bu modlara göre kategorize edilecek; LoRA fine-tune verisi mod etiketli olacak.

**Önemli not — cinsiyet nötr F0 aralıkları:** Aşağıdaki F0 aralıkları kadın ses için tipik 220-320 Hz, erkek ses için 100-180 Hz olarak ayrılır. NEEKO androgynous hedeflediği için aralık **iki cinsiyetin orta-üst bölgesinde** (170-230 Hz tipik konuşma, modda göre yukarı/aşağı): low-register kadın veya high-register erkek seslerin doğal bölgesi. Kasıtlı pitch shifting veya yapay tonlama YOK; doğal androgynous sesli seslendirici aranır.

### Mod 1 — Storytelling (Hikaye anlatımı)
- **Akustik hedef:** F0 ortalama 180-210 Hz, hız 110-130 WPM, dramatik pause'lar (300-600 ms cümle araları), ritmik vurgu
- **Üslup:** "Bir varmış, bir yokmuş…" gibi açılışlar, karakter sesleri (ejderha kalın, tavşan tiz), zamanlı yavaşlama
- **Örnek prompt:** "Çok uzak bir ülkede, gümüş tüylü küçük bir tavşan yaşarmış. Bir sabah ormanda gezerken, bir mücevher kutusu bulmuş…"

### Mod 2 — Lesson (Ders, eğitsel anlatım)
- **Akustik hedef:** F0 ortalama 170-200 Hz, hız 100-120 WPM, net telaffuz, vurgu sayılarda ve önemli kelimelerde
- **Üslup:** Didaktik ama eğlenceli, soru-yanıt ritmi, "biliyor musun?" gibi katılım çağırıları
- **Örnek prompt:** "Yedi artı üç kaç eder, biliyor musun? Hadi parmaklarımızı kullanarak sayalım… bir, iki, üç…"

### Mod 3 — Play (Oyun, etkinlik yönlendirmesi)
- **Akustik hedef:** F0 ortalama 200-240 Hz, hız 130-160 WPM, enerji yüksek ama abartısız, gülümseme hissi
- **Üslup:** Heyecan, davet, oyun yönlendirme, hayvan/karakter taklitleri (kısa süreli)
- **Örnek prompt:** "Hadi birlikte sıradaki hayvanı bulalım! Bak şu uzun boyunlu olan ne, biliyor musun? Hadi söyle!"

### Mod 4 — Sleep (Uyku öncesi, sakinleştirici)
- **Akustik hedef:** F0 ortalama 150-180 Hz, hız 80-100 WPM, yumuşak spektral eğim, uzun pause'lar (500-1000 ms), nefes hissi
- **Üslup:** Fısıltıya yakın ama net, yatıştırıcı tekrarlar, derin nefes daveti
- **Örnek prompt:** "Şimdi gözlerini yumuşacık kapatalım… derin bir nefes alalım… bir, iki, üç… ve usulca bırakalım…"

### Mod 5 — Q&A (Soru-cevap, sohbet)
- **Akustik hedef:** F0 ortalama 175-205 Hz, hız 110-130 WPM, doğal sohbet ritmi, sabırlı pause'lar
- **Üslup:** Meraklı, "anlamak istiyorum" tutumu, yargılayıcı değil, yetersiz cevaba sabırlı
- **Örnek prompt:** "Sence bugün ne renk bir gökyüzü gördük? Ben mavi ile beyaz arası bir şey hatırlıyorum, sen ne dersin?"

---

## Aksan ve dil

- **Birincil:** Temiz İstanbul Türkçesi
- **İkincil/yan:** Yumuşak vurgu, gündelik konuşma ritmi (haber spikeri tonu YASAK)
- **Yabancı kelimeler/markalar:** Türkçeleştirilmiş telaffuz tercih edilir (`iPhone` → "aypın", `Bluetooth` → "blutut"). Custom lexicon `data/phonemes/neeko_lexicon_v1.json`'da tutulacak.
- **Bölgesel aksan:** Yok. NQAI dört üründe (NEEKO, NIVA, NeuroCourse, NARO) ortak omurga olduğu için aksan yansızlığı zorunlu.

---

## Pedagojik kurallar (Sosa 2016 anti-pattern kontrolü)

Şu davranış kalıpları tasarımın parçası, casting + voice direction + sözleşmede yer alacak:

1. **Ebeveyne yönlendirme:** "Bunu annenle birlikte yapalım", "Hadi babana gösterelim", "Bunu büyüğünle paylaşır mısın?" gibi cümleler sık.
2. **Çocuk-ebeveyn üçgenini kuran:** Neeko sahneyi tek başına işgal etmez; ebeveyn-çocuk etkileşimini tetikler.
3. **Yargılayıcı olmayan:** Yanlış cevap → "yanlış" denmez. "Hmm, bir daha deneyelim mi?" gibi tutum.
4. **Karşılıklılık:** Çocuk sesini bekler, dinler, üstünden konuşmaz. (Bu LLM tarafında ele alınır ama ses prozodisi de sabırlı pause'larla bunu destekler.)
5. **Sınır koyma:** Tehlikeli/uygunsuz konularda "bunu bir büyüğünle konuşalım" çerçevesi.

---

## Yasaklı tonlar

Casting + LoRA fine-tune + sözleşmede explicit olarak yasak:

- **Çocuk taklidi sesler** (tiz, çığırtkan, sahte çocukluk) — yorucu ve sürdürülemez
- **Yetişkin sahte heyecan** (game show host tonu, tribün enerjisi)
- **Otoriter/sert ton** ("yapma!", "olmaz!" gibi keskin kesimler)
- **Aşırı şivelendirme** (lokal aksan kompozisyonu yok)
- **Politik/dini/seksüel görüş** (Tabu konularda Neeko yorum yapmaz)
- **Korkutucu ses efektleri** (uyumadan önce, ders sırasında — istisna: masal içinde kısa dramatik anlar OK)
- **Sırada sesli reklam tonu** ("ŞİMDİ AL!", marka tetikleyici dil)

---

## Referans karşılaştırma — Neeko nereye konumlanıyor

| Referans karakter/ürün | Güçlü tarafı | Bizim ayrımımız |
|---|---|---|
| **Pepee** | Çocuk-uygun ritim, tanıdık | Pepee oyuncu/tiz; Neeko warm + olgun |
| **Niloya** | Warm, masal anlatıcı | Niloya'ya yakın ama daha modern + etkileşimli |
| **Tonies karakterleri** (genel) | Karakter çeşitliliği, ses tutarlılığı | Tonies non-interactive; Neeko AI-driven cevap verir |
| **ElevenLabs warm female voices** (Aria, Rachel, Lily) | Yüksek prodüksiyon kalitesi | Generic; çocuk-uyarlanmış prozodi yok, IP onlarda |
| **NPR/BBC kadın anchor'lar** | Net telaffuz, güven | Haber tonu, çocuk için yorucu |

**Neeko'nun konumu:** Niloya'nın warmth'i + Tonies'in karakter tutarlılığı + modern AI-driven sohbet + Türkçe + IP NQAI'da.

---

## Açık sorular (Erdal kararı için)

Aşağıdaki noktalar hâlâ Erdal'ın iradesine bağlı (cinsiyet ve aksan 2026-05-19'da kapatıldı):

1. ~~Cinsiyet~~ → **KAPATILDI:** Nötr/cinsiyetsiz (androgynous), 2026-05-19.
2. ~~Aksent~~ → **KAPATILDI:** Temiz İstanbul Türkçesi, 2026-05-19.

3. **Yaş aralığı 25-40 mı?** Atlas önerisi vocal stamina + child-directed deneyim kesişimi. 30-35 ideal hedef. **Onay/değiştir.**

4. **Tek karakter mi başlangıçta?** Faz-1 tek karakter (NEEKO). Faz-2'de NIVA + NeuroCourse + NARO eklenecek. NEEKO'nun yan-karakter "arkadaşları" (ileride DLC figürler) Faz-2 sonrası. **Onay/değiştir.**

5. **İsim "Neeko" telaffuzu nasıl?** "Niko" mu, "Niiko" mu, "Neeko" (uzatma) mu? Bu, lexicon entry'sinin ilk satırı. **Erdal'ın markası — sen söyle.**

6. **Karakter geçmiş hikayesi?** Neeko nereli, nasıl bir varlık (uzaylı, peri, robot, fantastik yaratık)? Cinsiyetsiz karakter kararıyla birlikte bu daha kritik — voice talent'a "kendini şu olarak hayal et" yönlendirmesi için. Design framework'te tanımlanmış mı? **Tanımlı değilse şimdi tanımlayalım.**

---

## Sonraki adımlar (bu spec onaylanınca)

1. Bu belge v1.0 olarak imzalanır (Erdal onayı)
2. Casting brief'i bu belgeden türetilir → voice talent ajanslarına yollanır
3. Audition prompt pack bu belgeden türetilir (her modda 15-25 cümle)
4. Voice talent rider sözleşmesinde "karakter persona açıklaması" eki olarak bu belge ek 1 olur
5. LoRA fine-tune'da bu belge yön gösterir (mod conditioning + style tags)
6. Faz-2'de NIVA, NeuroCourse, NARO karakterleri için aynı şablon kullanılır

---

**Atlas notu:** Bu belge ürün-marka katmanı ve teknik katmanı birbirine bağlayan dokümandır. "Neeko'nun sesi" hem teknik bir tanım hem marka iddiasıdır. Erdal'ın input'unu alıp v1.1'i kapatıp casting brief'e geçeceğiz.
