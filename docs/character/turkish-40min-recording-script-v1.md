# Turkish 40-Minute Recording Script v1

Amaç: VoxCPM2 LoRA / voice-clone denemesi için tek konuşmacılı, temiz, Türkçe ağırlıklı, fonetik kapsaması yüksek bir kayıt üretmek.

Bu metin tamamen özgün yazılmıştır. Dış kaynaklardan metin kopyalanmamıştır.

## Kayıt Protokolü

- Parça numaralarını okumayın. Sadece metni okuyun.
- Her satırdan sonra 1-2 saniye sessiz kalın. Bu, sonradan otomatik kesmeyi kolaylaştırır.
- Hata yaparsanız durun, 2 saniye bekleyin, aynı satırı baştan okuyun. "Tekrar" demenize gerek yok.
- Aynı mikrofon, aynı oda, aynı mesafe, aynı kazanç ile kaydedin.
- WAV tercih edin: 48 kHz / 24-bit veya en az 48 kHz / 16-bit. Mono yeterli.
- Ağır noise reduction, müzik, reverb, telefon filtresi, compressor veya agresif normalize uygulamayın.
- Ses düzeyi dengeli olsun: patlama, clipping, fısıltı ve bağırma olmasın.
- Ton: sıcak, net, konuşur gibi. Çocukla konuşurken karikatürleşmeyin; yetişkin cümlelerinde kurumsal netlik koruyun.
- 40 dakikayı tek seferde bitirmek şart değil. 4 blok x 10 dakika daha sağlıklı olur. Moladan sonra mikrofon mesafesini değiştirmeyin.

## Araştırma Notları

- VoxCPM2 fine-tuning dokümanı tek konuşmacı klonlama için LoRA'yı hızlı ve düşük VRAM'li yol olarak konumluyor; daha geniş stil/domain adaptasyonu için daha fazla klip ve daha yüksek rank öneriyor. Ayrıca örneklerin yüzde 30-50 kadarında `ref_audio` kullanmak, referanslı ve referanssız yeteneğin birlikte korunmasına yardım ediyor.
- VoxCPM2 tarafında AudioVAE encoder 16 kHz çalışırken decoder 48 kHz çıktı verir; bu yüzden ham kaydı yüksek kalitede almak iyi, eğitim pipeline'ının kendi resample yoluna güvenmek daha güvenli.
- TTS datasında kalite nicelikten önce gelir: tek oda, tek konuşmacı, düşük gürültü, tutarlı enerji. Hışırtı, ağız sesi, yankı ve ton salınımı model tarafından öğrenilebilir.
- TTS kayıt metni fonem kapsamasına ve fonem dağılımına dikkat etmelidir. Türkçe için özellikle `ç, ğ, ı, i, ö, ş, ü`, `r`, `h`, `j`, yumuşak g, sayı okuma, kısaltma, soru tonlaması, listeleme ve uzun/kısa cümle dengesi önemlidir.
- 40 dakika bu deneme için mantıklı bir üst pilot. İlk fine-tune sonrası hatalar neredeyse kesin olarak veri temizliği, segment uzunluğu, transcript uyumu, referans sesi ve prozodi tutarlılığı üzerinden okunmalı.

## Okuma Metni

### Blok 1 — Nötr, Sıcak, Günlük Anlatım

TR40-001: Bugün sesimi olabildiğince doğal, açık ve sakin tutarak okuyorum; acele etmiyorum, kelimeleri yutmuyorum, ama tiyatro sahnesindeymiş gibi de davranmıyorum.

TR40-002: Sabahın ilk ışığı perdeye vurduğunda oda birden aydınlanmadı; önce küçük bir çizgi belirdi, sonra o çizgi yavaşça bütün duvarı dolaştı.

TR40-003: Bir fincan çayın buharı, masanın üzerinde duran kâğıtların kenarını hafifçe titretiyordu; dışarıda ise rüzgâr sessizce yön değiştiriyordu.

TR40-004: Bazı günler çok büyük kararlar almayız; sadece sandalyemizi düzeltir, derin bir nefes alır ve kaldığımız yerden devam ederiz.

TR40-005: Bu cümlede özellikle sakin kalıyorum; sesim ne fazla parlak, ne fazla koyu, ne de gereksiz biçimde dramatik olmalı.

TR40-006: Kapının yanındaki küçük rafın üzerinde üç anahtar, bir gözlük kabı, iki bozuk para ve yarım kalmış bir alışveriş listesi vardı.

TR40-007: Saat dokuzu yirmi geçiyordu; toplantıya daha on dakika vardı, bu yüzden notlarımı yeniden okumak için yeterince zamanım olduğunu düşündüm.

TR40-008: İnsan bazen doğru cümleyi ararken çok konuşur; bazen de bir kelimeyi doğru yerde söylemek, uzun bir açıklamadan daha güçlü olur.

TR40-009: Yağmurun sesi camda ince bir ritim kurdu; ne hızlıydı, ne yavaştı, sanki biri uzaktan usulca parmaklarıyla tempo tutuyordu.

TR40-010: Masanın üstündeki ajandaya baktım; pazartesi günü için üç görüşme, salı günü için kısa bir rapor ve cuma günü için küçük bir hatırlatma yazıyordu.

TR40-011: Bu kayıt boyunca sesimin rengini korumaya çalışıyorum; cümle değişse bile konuşmacı aynı kişi olarak duyulmalı.

TR40-012: Yumuşak ama belirgin konuşmak, özellikle uzun metinlerde önemlidir; kelime sonlarını düşürürsem model de sonları belirsiz üretmeyi öğrenebilir.

TR40-013: Eski bir defterin arasında kurumuş bir yaprak buldum; rengi solmuştu ama damarları hâlâ ince ince seçiliyordu.

TR40-014: Bir işi iyi yapmak için önce onu küçük parçalara ayırmak gerekir; sonra her parçayı ayrı ayrı, sabırla ve dikkatle tamamlarız.

TR40-015: Şimdi kısa bir soru okuyorum: Bugün kendine biraz zaman ayırdın mı, yoksa bütün günü başkalarının işleriyle mi geçirdin?

TR40-016: Cevabı hemen vermek zorunda değilsin; bazen iyi bir soru, cevaplanmadan önce zihinde biraz dolaşmak ister.

TR40-017: Sokakta bir çocuk kahkaha attı, ardından biri bisiklet zilini çaldı; şehir bir anlığına daha canlı ve daha yakın hissettirdi.

TR40-018: Dikkatli bir dinleyici, sesin içinde sadece kelimeleri değil, nefesi, beklemeyi, tereddüdü ve güven duygusunu da duyar.

TR40-019: Bu satırda hızımı biraz daha dengeliyorum; anlatım akıcı olmalı, fakat takip etmeyi zorlaştıracak kadar hızlı olmamalı.

TR40-020: Günün sonunda geriye kalan şey çoğu zaman büyük başarılar değil, iyi niyetle kurulmuş küçük ve temiz cümlelerdir.

### Blok 2 — Çocuğa Yönelik Açık ve Güven Veren Ton

TR40-021: Merhaba, bugün birlikte küçük bir keşif yapacağız; önce etrafımıza bakacağız, sonra duyduğumuz sesleri tek tek ayırt etmeyi deneyeceğiz.

TR40-022: Eğer bir şeyi hemen anlamazsan hiç sorun değil; merak etmek zaten öğrenmenin ilk adımıdır, acele etmeden tekrar deneyebiliriz.

TR40-023: Bak, şu küçük taşın rengi gri gibi görünüyor ama ışığa yaklaştırınca içinde maviye benzeyen ince çizgiler de var.

TR40-024: Şimdi üçe kadar sayalım: bir, iki, üç. Harika; şimdi aynı saymayı daha yavaş ve daha dikkatli yapalım.

TR40-025: Bazen cesur olmak, yüksek sesle bağırmak değildir; bazen cesur olmak, korktuğunu söyleyip yine de küçük bir adım atmaktır.

TR40-026: Eğer bulutlara dikkatle bakarsan, bazıları pamuk gibi dağılır, bazıları da kocaman bir gemi gibi gökyüzünde ilerler.

TR40-027: Bir sorum var: Sence karıncalar yollarını nasıl buluyor? Belki kokuyla, belki izlerle, belki de birlikte çalışmayı çok iyi bildikleri için.

TR40-028: Şimdi gözlerini kırp ve odadaki üç yuvarlak şeyi bulmaya çalış; acelemiz yok, ben burada bekliyorum.

TR40-029: Yanlış cevap verdiğinde üzülmeni istemem; yanlış cevaplar bazen doğru kapının nerede olduğunu gösteren küçük işaretler gibidir.

TR40-030: Bugünkü görevimiz çok basit: gördüğümüz şeyi söylemek, duyduğumuz şeyi ayırmak ve hissettiğimiz şeyi nazikçe anlatmak.

TR40-031: Küçük bir kuş düşün; kanatları minicik ama gökyüzü çok büyük. Yine de her sabah uçmayı yeniden deniyor.

TR40-032: Bu cümleyi daha yumuşak okuyorum: Korkman sorun değil, yanında kalacağım, birlikte nefes alacağız ve sonra ne yapacağımıza karar vereceğiz.

TR40-033: Şimdi parmaklarımızla ritim tutalım: tık, tık, tık. Sonra biraz bekleyelim ve aynı ritmi içimizden sayalım.

TR40-034: Bir oyuncak kırıldığında dünyanın sonu gelmez; önce ne olduğunu anlarız, sonra onarabilir miyiz diye bakarız.

TR40-035: Harfleri tek tek söyleyelim: a, e, ı, i, o, ö, u, ü. Şimdi bu sesleri karıştırmadan, sakin sakin tekrar edelim.

TR40-036: Ç harfiyle çiçek, çanta, çorap ve çınar diyebiliriz; ş harfiyle şeker, şemsiye, şarkı ve şaşkın diyebiliriz.

TR40-037: Bazen en güzel oyun, çok pahalı oyuncaklarla değil, iyi seçilmiş iki kutu, biraz hayal gücü ve bolca sabırla kurulur.

TR40-038: Şimdi bir hikâyenin kapısını aralıyoruz; içeride acele eden kimse yok, sadece dinleyen kulaklar ve merak eden gözler var.

TR40-039: Sen konuşurken ben dikkatle dinleyeceğim; çünkü birini gerçekten dinlemek, ona verilen en güzel hediyelerden biridir.

TR40-040: Hazırsan başlıyoruz: önce derin bir nefes al, omuzlarını gevşet ve içinden sessizce şunu söyle, ben deneyebilirim.

### Blok 3 — Masal ve Hikâye Anlatımı

TR40-041: Uzak bir köyün kenarında, rüzgârı dinleyen küçük bir değirmen vardı; kimse onun konuştuğunu duymazdı ama herkes çalıştığını bilirdi.

TR40-042: Değirmenin yanında yaşlı bir zeytin ağacı büyürdü; gövdesi eğriydi, dalları karışıktı, ama gölgesi yaz günlerinde serin ve güvenliydi.

TR40-043: Bir akşamüstü, köyün en meraklı çocuğu ağacın altına oturdu ve fısıltıyla sordu: Sence yıldızlar gündüz nereye saklanıyor?

TR40-044: Zeytin ağacı cevap vermedi; sadece yapraklarını hışırdattı. Çocuk bunun bir cevap olmadığını sandı, ama aslında ilk ipucu buydu.

TR40-045: Çünkü bazı cevaplar kelimeyle gelmez; yaprağın titreyişinde, suyun akışında ya da çok uzaktan gelen bir kuş sesinde saklanır.

TR40-046: Çocuk ertesi sabah erkenden kalktı, ayakkabılarını giydi ve dere boyunca yürümeye başladı; amacı yıldızların izini bulmaktı.

TR40-047: Dere kenarında parlak bir taş gördü; taşın üzerinde incecik bir çizgi vardı, sanki gece göğünden düşmüş küçük bir yol haritasıydı.

TR40-048: Taşı cebine koymadı; çünkü bazı güzel şeyler sahip olmak için değil, fark etmek için karşımıza çıkar.

TR40-049: Yolun sonunda küçük bir tepe vardı. Tepeye çıkınca köy daha küçük, gökyüzü daha büyük, kendi sorusu ise daha derin görünüyordu.

TR40-050: O sırada yaşlı bir çoban yanına geldi ve gülümseyerek dedi ki: Yıldızlar gündüz saklanmaz, sadece onları görecek kadar karanlık olmaz.

TR40-051: Çocuk bu cevabı hemen sevmedi; çünkü gizemli bir mağara, altın bir anahtar ya da konuşan bir ay bekliyordu.

TR40-052: Ama akşam olunca anladı ki bazı gerçekler de masal kadar güzeldir; yeter ki onları duyacak kadar sessiz kalalım.

TR40-053: Gece çöktüğünde ilk yıldız belirdi. Çocuk zeytin ağacına döndü ve bu kez hiç soru sormadan yanında oturdu.

TR40-054: Yapraklar yine hışırdadı. Bu defa çocuk o sesi anladı: Aradığın şey bazen kaybolmamıştır, sadece görünmek için doğru zamanı bekliyordur.

TR40-055: Masal burada bitmedi; çünkü ertesi gün çocuk başka bir soru buldu. Güneş batarken renkler neden birbirine karışıyordu?

TR40-056: Her yeni soru, onu köyün başka bir köşesine götürdü; fırının önüne, okulun bahçesine, çeşmenin yanına ve dar taş sokaklara.

TR40-057: Fırıncı ona hamurun sabrı sevdiğini söyledi. Öğretmen, harflerin birleşince yollar açtığını anlattı. Çeşme ise sadece aktı, ama çok şey öğretti.

TR40-058: Çocuk büyüdüğünde hâlâ soru soruyordu; yalnız artık cevapları hemen yakalamaya çalışmıyor, önce etrafındaki sessizliği dinliyordu.

TR40-059: Köydekiler onun bilge olduğunu düşündü. O ise kendini sadece iyi bir dinleyici saydı; çünkü dünyayı anlamanın ilk yolu dinlemekti.

TR40-060: İşte o yüzden her gece, ilk yıldız göründüğünde, küçükken öğrendiği cümleyi hatırlardı: Görünmeyen şey, her zaman yok demek değildir.

### Blok 4 — Soru, Cevap, Diyalog ve Etkileşim

TR40-061: Bana bir şey sorabilirsin; cevabı bilmiyorsam bunu açıkça söylerim, sonra birlikte nereden araştırabileceğimizi düşünürüz.

TR40-062: Diyelim ki bugün moralin biraz düşük. Önce bunu düzeltmeye çalışmam; önce yanında durur, ne hissettiğini anlamaya çalışırım.

TR40-063: Şimdi kısa bir diyalog okuyorum. Birinci kişi soruyor: Hazır mısın? İkinci kişi cevap veriyor: Hazır sayılırım, ama biraz heyecanlıyım.

TR40-064: Birinci kişi şöyle diyor: Heyecan bazen iyi bir işarettir. İkinci kişi gülümsüyor ve diyor ki: O zaman yavaş başlayalım.

TR40-065: Eğer bir yönerge veriyorsam açık olmalıyım: Önce kırmızı düğmeye bas, sonra ekrandaki küçük daire yeşile dönene kadar bekle.

TR40-066: Eğer güven vermem gerekiyorsa aceleci olmamalıyım: Şu anda sorun çözülmedi, ama ne olduğunu anladık ve ilk adımı birlikte atıyoruz.

TR40-067: Sence bir robotun nazik olması mümkün müdür? Ben bunun yalnızca kelimelerle değil, zamanlama ve dikkatle de ilgili olduğunu düşünüyorum.

TR40-068: Bazen kullanıcı sadece cevap istemez; sesin kararlı, sakin ve yargılamayan bir yerde durmasını ister.

TR40-069: Şimdi seçenekleri okuyorum: Birinci seçenek kısa ve hızlıdır; ikinci seçenek daha güvenlidir; üçüncü seçenek ise daha çok zaman ister.

TR40-070: Hangisini seçersen seç, kararını değiştirme hakkın var. Biz önce küçük bir deneme yapar, sonra sonucu beraber değerlendiririz.

TR40-071: Evet, seni duyuyorum. Hayır, bu garip bir soru değil. Belki de tam bu soru, konunun en önemli yerine dokunuyor.

TR40-072: Şimdi daha resmi konuşuyorum: Talebinizi aldım, gerekli kontrolleri başlatıyorum ve işlem tamamlandığında sizi kısa bir özetle bilgilendireceğim.

TR40-073: Şimdi daha sıcak konuşuyorum: Merak etme, biraz karışık görünse de birlikte toparlarız; önce en kolay parçadan başlayalım.

TR40-074: Bir kelimeyi anlamadıysan onu tekrar edebilirim. Bir adımı kaçırdıysan başa dönebiliriz. Burada hız değil, açıklık önemli.

TR40-075: Lütfen şu bilgileri kontrol edin: adınız, soyadınız, işlem numaranız ve size ulaşabileceğimiz güncel telefon numaranız.

TR40-076: İşlem numarası şöyle okunabilir: A yetmiş iki, K dokuz yüz on, tire, üç sıfır beş. Şimdi bunu bir kez daha yavaş okuyorum.

TR40-077: Saat on dört otuzda başlayan görüşme, yaklaşık yirmi beş dakika sürecek. Eğer daha erken bitirirsek kalan zamanı sorularınıza ayıracağız.

TR40-078: Bana göre iyi bir asistan, çok bilmiş görünmez; doğru anda susmayı, doğru anda sormayı ve gerektiğinde özür dilemeyi bilir.

TR40-079: Kısa bir özür cümlesi okuyorum: Bu deneyim beklediğiniz gibi olmadı, bunun için üzgünüm; şimdi durumu düzeltmek için ne yapabileceğimize bakalım.

TR40-080: Kısa bir kapanış cümlesi okuyorum: Bugünlük bu kadar; istersen bir sonraki adımda kaldığımız yerden devam edebiliriz.

### Blok 5 — Sakinleştirici, Yavaş, Gece Modu

TR40-081: Şimdi daha yavaş ve sakin konuşuyorum; sesim hâlâ anlaşılır kalmalı, ama cümlelerin kenarları biraz daha yumuşak duyulmalı.

TR40-082: Gece olduğunda bütün soruları çözmek zorunda değiliz; bazı düşünceler sabaha kadar bekleyebilir, bazı cevaplar dinlenince daha net görünür.

TR40-083: Önce omuzlarını gevşet. Sonra çeneni sıkmadığını fark et. Şimdi burnundan sakin bir nefes al ve yavaşça bırak.

TR40-084: Bir odayı karanlık yapan şey ışığın yokluğu değildir sadece; bazen fazla düşünce de insanın içinde gölge gibi dolaşır.

TR40-085: O gölgeleri kovalamaya çalışma. Onlara isim ver, yerlerini fark et ve sonra dikkatini yeniden nefesine getir.

TR40-086: Uzakta çok hafif bir yağmur sesi varmış gibi düşün; damlalar acele etmiyor, toprak da onlardan hızlı olmalarını istemiyor.

TR40-087: Eğer bugün zor geçtiyse, bu senin zayıf olduğun anlamına gelmez. Zor günler güçlü insanları da yorabilir.

TR40-088: Şimdi çok kısa bir cümle okuyorum: Buradayım, duyuyorum, acele etmiyorum.

TR40-089: Bir düşünce geldiğinde ona takılıp gitmek yerine, onu pencereden geçen bir bulut gibi izleyebilirsin.

TR40-090: Bulutlar gelir, şekil değiştirir ve sonunda uzaklaşır. Senin görevin gökyüzünü itmek değil, gökyüzü olduğunu hatırlamaktır.

TR40-091: Çocuklara anlatır gibi ama bebekle konuşur gibi değil: Uyku bir düğme değildir; uyku, güvenli bir yere yavaş yavaş yaklaşmaktır.

TR40-092: Şimdi sayıları yumuşak okuyorum: on, dokuz, sekiz, yedi, altı, beş, dört, üç, iki, bir.

TR40-093: Her sayı biraz daha sakinleşmek için küçük bir işaret olsun. Hiçbir şeyi zorlamadan, sadece ritmi takip edelim.

TR40-094: Bedenin yatağa biraz daha ağırlaşıyorsa, bu iyi bir şey. Zihin hâlâ konuşuyorsa, ona nazikçe "sonra" diyebilirsin.

TR40-095: Karanlığın içinde bile güvenli şeyler vardır: tanıdık bir yastık, düzenli bir nefes, uzaktan gelen bir ev sesi.

TR40-096: Şimdi şu cümleyi dingin okuyorum: Bugün bitiyor, ama sen bitmiyorsun; yarın için kendine küçük bir yer bırakabilirsin.

TR40-097: Eğer uyuyamazsan da sorun değil. Sadece dinlenmek bile bedenin için iyidir; gözlerini kapatmak küçük bir başlangıçtır.

TR40-098: Gece bazen düşünceleri büyütür. Sabah aynı düşünce daha küçük, daha yönetilebilir ve daha anlaşılır görünebilir.

TR40-099: O yüzden şimdi karar vermiyoruz. Şimdi sadece dinleniyoruz. Geri kalan her şey, sırası geldiğinde yeniden ele alınabilir.

TR40-100: İyi geceler demek, günü tamamen unutmak değildir; günü nazikçe kapatıp kendine biraz sessizlik vermektir.

### Blok 6 — Kurumsal, Net, Call-Center / NIVA Tonu

TR40-101: Merhaba, NQAI destek hattına hoş geldiniz. Size daha hızlı yardımcı olabilmem için işlem türünüzü kısaca belirtmenizi rica ederim.

TR40-102: Güvenlik nedeniyle bazı bilgileri doğrulamamız gerekiyor. Lütfen doğum tarihinizi gün, ay ve yıl olarak açıkça söyleyin.

TR40-103: Sistem kayıtlarınızı kontrol ediyorum. Bu işlem genellikle on beş saniye kadar sürer; lütfen hattı kapatmadan bekleyin.

TR40-104: Talebiniz başarıyla alındı. Başvuru numaranız NQ iki yüz kırk sekiz, tire, yedi bin on üç olarak oluşturuldu.

TR40-105: Anladığım kadarıyla sorun, son ödemenin hesabınızda görünmemesiyle ilgili. Önce ödeme tarihini, sonra dekont bilgisini kontrol edeceğim.

TR40-106: Şu anda hesabınızda aktif görünen iki abonelik var: temel paket ve ek depolama paketi. İsterseniz her ikisini de ayrı ayrı inceleyebiliriz.

TR40-107: Bu noktada size üç seçenek sunabilirim: işlemi iptal etmek, planı değiştirmek veya mevcut planı aynı koşullarla yenilemek.

TR40-108: Lütfen IBAN bilgisini yazılı olarak paylaşmayın; güvenliğiniz için bu bilgiyi yalnızca doğrulanmış ödeme ekranı üzerinden güncelleyin.

TR40-109: Kayıtlarımızda e-posta adresiniz erdal nokta destek, et işareti, örnek nokta com biçiminde görünüyor. Bunu onaylıyor musunuz?

TR40-110: İşlem sonucunda herhangi bir ücret alınmayacaktır. Eğer ek ücret doğarsa, onayınız olmadan devam edilmeyecektir.

TR40-111: Yaşadığınız gecikme için üzgünüm. Şimdi dosyanızı öncelikli inceleme sırasına alıyorum ve size tahmini dönüş süresini söyleyeceğim.

TR40-112: Tahmini çözüm süresi yirmi dört saattir. Acil durumlarda bu süre kısalabilir, fakat kesin sonuç için teknik ekibin onayı gerekir.

TR40-113: Şu anda bağlantınızda kısa bir kesinti görünüyor. Modeminizi kapatıp on saniye bekledikten sonra yeniden açmanızı öneririm.

TR40-114: Eğer aynı hata tekrar ederse, lütfen ekranda gördüğünüz hata kodunu aynen okuyun. Kodun harf ve rakam sırası önemlidir.

TR40-115: Hata kodu E sıfır yedi, eğik çizgi, C doksan iki ise, sorun genellikle yetkilendirme süresinin dolmasından kaynaklanır.

TR40-116: Bu işlem kişisel verilerinizin korunması kapsamında kayıt altına alınır. Detaylı aydınlatma metnine hesabınızın güvenlik bölümünden ulaşabilirsiniz.

TR40-117: Görüşmeyi sonlandırmadan önce başka bir konuda yardıma ihtiyacınız olup olmadığını sormak isterim.

TR40-118: Memnuniyetinizi ölçmek için kısa bir değerlendirme gönderebiliriz. Katılmak istemezseniz herhangi bir işlem yapmanız gerekmez.

TR40-119: Yardımcı olabildiysem ne mutlu. İşlem özetiniz birkaç dakika içinde kayıtlı e-posta adresinize iletilecektir.

TR40-120: Bizi tercih ettiğiniz için teşekkür ederiz. Güvenli ve iyi bir gün geçirmenizi dilerim.

### Blok 7 — Eğitim, Açıklama, NeuroCourse Tonu

TR40-121: Şimdi bir konuyu adım adım anlatacağım. Önce ana fikri kuracağız, sonra örnekleri inceleyeceğiz, en sonunda kısa bir tekrar yapacağız.

TR40-122: Bir sistemi anlamanın en kolay yolu, onu girdiler, işlemler ve çıktılar olarak ayırmaktır. Bu üçlü çoğu teknik konuda işe yarar.

TR40-123: Girdi, sisteme verdiğimiz bilgidir. İşlem, bu bilginin dönüştürülme biçimidir. Çıktı ise sistemin bize geri verdiği sonuçtur.

TR40-124: Örneğin bir metin okuma modelinde girdi yazıdır; işlem, dil ve ses bilgisinin modele aktarılmasıdır; çıktı ise duyduğumuz konuşmadır.

TR40-125: Burada kritik nokta şudur: Model yalnızca kelimeleri değil, kelimelerin nasıl söylendiğine dair örüntüleri de öğrenir.

TR40-126: Bu yüzden eğitim verisi temiz değilse, model de temiz konuşmaz. Gürültülü veri, belirsiz transcript ve tutarsız ton doğrudan kaliteyi düşürür.

TR40-127: Şimdi önemli bir ayrımı netleştirelim: Daha fazla veri her zaman daha iyi sonuç demek değildir. Daha iyi veri çoğu zaman daha değerlidir.

TR40-128: Eğer kırk dakikalık kayıt aynı odada, aynı mikrofonla ve aynı enerjiyle alınmışsa, dağınık üç saatlik kayıttan daha öğretici olabilir.

TR40-129: Transcript uyumu da çok önemlidir. Yazıda olmayan bir kelimeyi okursam veya okuduğum kelime yazıda yoksa, model yanlış eşleşme öğrenir.

TR40-130: İnce ayar aşamasında hedefimiz, base modelin Türkçe bilgisini bozmak değil, belirli bir konuşmacının ses rengini ve ritmini yakalamaktır.

TR40-131: LoRA burada pratik bir yöntemdir; bütün modeli baştan eğitmek yerine, küçük ama etkili ek ağırlıklarla konuşmacı karakterini yakalamaya çalışır.

TR40-132: Fakat LoRA mucize değildir. Eğer kayıt kötü, kesimler yanlış veya metinler tekdüze ise, sonuç da kararsız ve yapay duyulabilir.

TR40-133: Şimdi kısa bir teknik liste okuyorum: örnekleme oranı, sinyal-gürültü oranı, segment uzunluğu, transcript doğruluğu ve referans ses tutarlılığı.

TR40-134: Bu listedeki her madde kaliteye ayrı ayrı dokunur. Bir tanesi ciddi bozulursa, diğerlerinin iyi olması sonucu tamamen kurtarmayabilir.

TR40-135: Model değerlendirmesinde üç şeye bakacağız: telaffuz doğru mu, ses bana benziyor mu, uzun cümlede karakter kayıyor mu?

TR40-136: Ayrıca hız da önemli: Gerçek zaman faktörü bire yakınsa kullanılabilir; bire çok uzaksa kullanıcı sesin gelmesini fazla bekler.

TR40-137: Şimdi öğrendiklerimizi özetleyelim: Temiz kayıt al, iyi böl, transcript'i düzelt, küçük eğitim yap, sonucu dinle ve hataya göre veriyi iyileştir.

TR40-138: Eğer sonuç çok düzse, daha fazla doğal ifade gerekir. Eğer sonuç fazla abartılıysa, daha dengeli ve nötr kayıtlar eklemek gerekir.

TR40-139: Eğer ses bazen kalınlaşıp bazen inceliyorsa, muhtemelen referans sesi, cfg değeri, eğitim adımı veya kayıt içi ton tutarlılığı kontrol edilmelidir.

TR40-140: İyi bir eğitim süreci tek seferlik değildir; küçük denemeler, dikkatli dinleme ve ölçülü düzeltmelerle olgunlaşır.

### Blok 8 — Türkçe Zorlayıcı Fonetik, Sayı, Kısaltma ve Telaffuz

TR40-141: Şimdi Türkçe'nin zor köşelerine geliyoruz: çığ, çiğ, çağ, sağ, sığ, yoğun, düğüm, eğri, öğle ve yağmur kelimelerini net okuyorum.

TR40-142: Yumuşak g bazen uzatır, bazen iki ünlüyü birbirine bağlar; değil, eğlence, soğuk, ağaç, boğaz ve göğüs derken bunu duyabilirsiniz.

TR40-143: Şu kelimelerde j sesini özellikle temiz söylüyorum: jilet, jandarma, jüri, jeoloji, ajanda, enerji, garaj ve proje.

TR40-144: Şimdi r sesleri geliyor: karar, tekrar, zarar, yarar, kırılır, sürer, arar, durur, görür ve bilir.

TR40-145: S ve ş ayrımına dikkat ediyorum: sis, şiş, ses, şeş, sarı, şarkı, sakız, şaşkın, sınıf ve şırınga.

TR40-146: Z ve s ayrımı da önemlidir: zar, sar, yaz, yas, kaz, kas, hızlı, hırslı, zeytin ve sepet.

TR40-147: Ç ve c ayrımı: cam, çam, can, çan, cılız, çilek, gece, geçe, acı ve açı.

TR40-148: K ve g ayrımı: kara, gara demiyorum; kedi, gedi demiyorum; kayık, geyik, kaynak, gerçek ve gökyüzü diyorum.

TR40-149: İnce ve kalın ünlüleri karıştırmadan okuyorum: kız, giz, kul, gül, kol, göl, sır, sir, tık, tik.

TR40-150: Şimdi sayılar: sıfır, bir, iki, üç, dört, beş, altı, yedi, sekiz, dokuz, on, on bir, yirmi iki, otuz üç.

TR40-151: Daha uzun sayılar: yüz beş, bin iki yüz kırk, on sekiz bin yedi yüz altmış üç, iki milyon beş yüz bin doksan.

TR40-152: Tarih okuyorum: yirmi beş Mayıs iki bin yirmi altı, Pazartesi günü saat on altı kırk beşte kayıt alındı.

TR40-153: Para okuyorum: üç yüz kırk dokuz lira doksan kuruş, bin iki yüz dolar, yüzde on sekiz KDV ve yüzde iki virgül beş artış.

TR40-154: Kısaltmalar: API, GPU, CPU, TTS, KVKK, TBMM, URL, QR kod, SMS, e-posta ve PDF dosyası.

TR40-155: İngilizce kökenli ama Türkçe cümlede geçen kelimeler: streaming, latency, pipeline, dashboard, benchmark, endpoint, token ve cache.

TR40-156: Şimdi onları Türkçe ritme yerleştiriyorum: Streaming endpoint yavaşsa kullanıcı ilk sesi geç duyar ve deneyim doğal olmaktan çıkar.

TR40-157: Karışık özel adlar okuyorum: NEEKO, NIVA, NeuroCourse, NARO, Erdal, Şefika, İstanbul, Ankara, İzmir, Diyarbakır ve Eskişehir.

TR40-158: İlçe ve yer adları: Kadıköy, Üsküdar, Çankaya, Keçiören, Bornova, Çeşme, Karşıyaka, Göreme, Safranbolu ve Şanlıurfa.

TR40-159: Uzun bir cümlede nefesimi koruyorum: Eğer model bu satırı doğal bölebilirse, hem noktalama bilgisini hem de konuşma ritmini daha iyi yakalayacaktır.

TR40-160: Kapanış için sakin bir cümle okuyorum: Sesim burada aynı kişi olarak kalmalı; kelimeler değişse de karakter, nefes ve güven duygusu dağılmamalı.

### Blok 9 — Karma Kalite Bloğu: Uzun Cümle, Kısa Cümle, Duygu Dengesi

TR40-161: Şimdi ikinci kapanışa geçmiyorum; bunun yerine modelin uzun süre aynı konuşmacıda kalıp kalmadığını anlamak için biraz daha geniş bir metin okuyorum.

TR40-162: Bazı sistemler kısa cümlede iyi görünür, ama metin uzadığında nefes, ton ve karakter kimliği yavaşça başka bir yere kayar.

TR40-163: Bu yüzden burada hem kısa hem uzun cümleler var. Kısa cümle. Bir soru. Küçük bir durak. Sonra yeniden akıcı bir anlatım.

TR40-164: Kütüphanenin sessiz katında eski kitapların kokusu vardı; kimse yüksek sesle konuşmuyordu, ama sayfa çevirme sesleri küçük bir sohbet gibi duyuluyordu.

TR40-165: Çocuk rafın önünde durdu ve kapağında mavi balina resmi olan kitabı seçti; çünkü balinaların şarkı söylediğini yeni öğrenmişti.

TR40-166: Ona göre bu bilgi hem tuhaf hem de güzeldi. Kocaman bir canlı, denizin karanlık yerlerinde kendi sesini yollaştırabiliyordu.

TR40-167: Bir yetişkin gibi açıklıyorum: Ses dalgaları ortam içinde yayılır; hava, su ve katı maddeler bu yayılımı farklı biçimlerde taşır.

TR40-168: Bir çocukla konuşur gibi açıklıyorum: Ses bazen görünmez bir top gibi zıplar; kulağına gelince onu duyduğunu fark edersin.

TR40-169: Şimdi aynı bilgiyi daha kurumsal söylüyorum: Konuyla ilgili teknik dokümanı inceleyip size anlaşılır bir özet hazırlayacağım.

TR40-170: Aynı konuşmacı, üç farklı bağlam. Hedefimiz bu: ton değişebilir, ama ses kimliği dağılmamalı.

TR40-171: Şimdi biraz daha hızlı ama hâlâ anlaşılır okuyorum; hızlı konuşmak, kelimeleri yutmak anlamına gelmemeli.

TR40-172: Şimdi biraz daha yavaş okuyorum; yavaş konuşmak da cümleyi ağırlaştırmak ya da gereksiz dramatik hale getirmek demek değildir.

TR40-173: Ara sıra nefes alıyorum, fakat nefesi cümlenin ortasında uygunsuz bir yere koymamaya çalışıyorum.

TR40-174: Bu satırda virgüller var, kısa duraklar var, ama anlam kopmuyor; nokta geldiğinde ise cümle gerçekten tamamlanıyor.

TR40-175: Şimdi bir liste okuyorum: temiz kayıt, doğru metin, düzenli kesim, dengeli eğitim, sabırlı dinleme ve küçük iyileştirmeler.

TR40-176: Şimdi bir karşılaştırma okuyorum: Kötü veri hızlı sonuç verir gibi görünür, iyi veri ise bazen yavaş başlar ama daha güvenilir ilerler.

TR40-177: Şimdi sıcak bir cümle: İyi ki denedin; sonucun kusursuz olması gerekmiyor, önemli olan ne öğrendiğimizi fark etmek.

TR40-178: Şimdi ciddi bir cümle: Bu kayıt, yalnızca izin verilen geliştirme ve model değerlendirme süreçleri için kullanılmalıdır.

TR40-179: Şimdi şaşırmış ama abartısız bir ton: Gerçekten mi, bunu daha önce fark etmemiştim; o zaman birlikte tekrar bakalım.

TR40-180: Şimdi sevinçli ama kontrollü bir ton: Harika, ilk deneme beklediğimizden daha temiz çıktı; yine de acele karar vermeyelim.

TR40-181: Şimdi üzgün ama sakin bir ton: Bu sonuç istediğimiz gibi olmadı, fakat sorunun nereden geldiğini bulabiliriz.

TR40-182: Şimdi güven veren bir ton: Kayıt bozulduysa yeniden alırız; önemli olan hatayı saklamak değil, temiz bir veri seti kurmak.

TR40-183: Türkçe'de bazı kelimeler yazıldığı gibi okunur sanılır, ama ritim yine de cümlenin anlamına göre değişir.

TR40-184: Mesela "olur" kelimesi bazen onaydır, bazen isteksiz kabul, bazen de sadece konuşmayı kapatan kısa bir işarettir.

TR40-185: "Peki" kelimesi de böyledir; sıcak söylenirse kabul, soğuk söylenirse mesafe, merakla söylenirse yeni bir soru gibi duyulur.

TR40-186: Şimdi "tamam" kelimesini doğal bağlamda kullanıyorum: Tamam, önce bunu kaydedelim, sonra çıktıyı dinleyip karar verelim.

TR40-187: Şimdi "hayır" kelimesini sertleştirmeden söylüyorum: Hayır, bu kısmı atlamayalım; çünkü kaliteyi asıl belirleyen detay burada olabilir.

TR40-188: Şimdi "evet" kelimesini abartmadan söylüyorum: Evet, model çalışıyor; ama çalışması, hazır olduğu anlamına gelmeyebilir.

TR40-189: Bir dosya adı okuyorum: neeko tire proto tire v sıfır, alt çizgi, test sıfır üç nokta wav.

TR40-190: Bir ölçüm cümlesi okuyorum: İlk ses iki yüz kırk milisaniyede geldi, toplam üretim ise üç virgül sekiz saniye sürdü.

TR40-191: Bir kalite değerlendirmesi okuyorum: Telaffuz doğru, benzerlik orta, duygu dengesi iyi, fakat uzun cümlede sonlara doğru hafif kayma var.

TR40-192: Bir karar cümlesi okuyorum: Bu sonucu doğrudan ürüne koymayalım; önce iki ek kayıtla karakter tutarlılığını iyileştirelim.

TR40-193: Bir yönlendirme cümlesi okuyorum: Lütfen mikrofonu hareket ettirmeyin, sandalyeyi geri çekmeyin ve kayıt bitene kadar pencereyi açmayın.

TR40-194: Bir çevre sesi uyarısı okuyorum: Klima, bilgisayar fanı, sokak gürültüsü ve masa titreşimi sessiz gibi görünse de eğitimde belirginleşebilir.

TR40-195: Bir konuşma hızı uyarısı okuyorum: Çok hızlı okursam kelimeler birbirine yapışır; çok yavaş okursam model gereksiz beklemeler öğrenebilir.

TR40-196: Bir duygu uyarısı okuyorum: Her satıra ayrı karakter yapmak yerine, aynı kişinin farklı bağlamlarda konuştuğunu korumak daha değerlidir.

TR40-197: Bir nefes uyarısı okuyorum: Nefes almak doğaldır, ama sert nefes patlamaları ve mikrofona yakın soluklar daha sonra temizlenmek zorunda kalır.

TR40-198: Bir son kontrol cümlesi okuyorum: Bu blok bittikten sonra kısa bir mola verebilir, su içebilir ve aynı mikrofon mesafesiyle devam edebilirim.

TR40-199: Bir teşekkür cümlesi okuyorum: Bu kaydı dikkatle hazırladığım için, sonraki fine-tune denemesinde neyin işe yaradığını daha net görebileceğiz.

TR40-200: Son cümle: Sesim burada bitmiyor; bu yalnızca temiz bir başlangıç, dikkatli bir ölçüm ve daha iyi bir modele giden ilk düzgün adımdır.

## Opsiyonel Ek Blok — 8 Dakikalık Doğal Konuşma

Eğer kayıt 40 dakikanın altında kaldıysa aşağıdaki serbest konuşma yönergelerini sırayla uygulayın. Her başlıkta 60-90 saniye konuşun. Çok dağılmadan, aynı sıcak ve doğal tonda kalın.

1. Bugün kayda hazırlanırken neler yaptığınızı anlatın: oda, mikrofon, sessizlik, küçük aksilikler.
2. Çocukken sevdiğiniz bir oyunu anlatın; kuralları, nasıl başladığını ve neden eğlenceli olduğunu açıklayın.
3. Birine yeni bir teknolojiyi sabırla anlatıyormuş gibi konuşun; zor kelimeleri sadeleştirin.
4. Kısa bir müşteri destek senaryosu uydurun; sorun, kontrol, çözüm ve nazik kapanış içersin.
5. Sakinleştirici bir gece rutini anlatın; nefes, ışık, ses ve güven hissi üzerinde durun.
6. Türkçe'de telaffuzunu zor bulduğunuz kelimeleri söyleyin ve neden zor olduklarını açıklayın.

## Sonradan İşleme Notu

- Kaydı 8-20 saniyelik parçalara bölmek idealdir.
- Her parçanın transcript'i bire bir aynı olmalıdır.
- Hatalı, patlamış, aşırı nefesli, yankılı veya arka plan sesli parçaları eğitimden çıkarın.
- İlk fine-tune için tüm 40 dakikayı kullanmak yerine temizliği yüksek 20-30 dakikayla başlamak daha iyi olabilir.
- Kalan temiz parçaları validation/eval için ayırmak, modelin gerçekten ilerleyip ilerlemediğini anlamayı kolaylaştırır.

## Kaynaklar

- VoxCPM2 fine-tuning guide: https://voxcpm.readthedocs.io/en/latest/finetuning/finetune.html
- VoxCPM GitHub: https://github.com/OpenBMB/VoxCPM
- NVIDIA Riva TTS script generation: https://docs.nvidia.com/deeplearning/riva/archives/2-18-1/public/tts/tts-script-generation.html
- Homai TTS recording guide: https://homai.tech/tts.html
- Smallest AI voice cloning best practices: https://docs.smallest.ai/waves/documentation/best-practices/voice-cloning-best-practices
- VoiceCheap voice cloning best practices: https://www.voicecheap.ai/docs/voice/voice-cloning-best-practices
- Transfer learning for new TTS speakers: https://arxiv.org/abs/2110.05798
