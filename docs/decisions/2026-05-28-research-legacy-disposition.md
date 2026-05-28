# ADR-5 — `docs/research/` LEGACY işaretleme stratejisi

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** `docs/research/` altındaki 13 dosya (00-research-brief'ten 04-latency-stratejiye, VENDOR_TTS_API_REFERENCE dahil) 2026-05-19 öncesinde "NEEKO çocuk oyuncağı + NQAI dört-ürün iç portföyü + dar Türkçe domain" çerçevesinde yazıldı. Surgical-reset turunda repo'nun kimliği "uluslararası TTS API SaaS — NeuroVoice" olarak yeniden tanımlandı. Bu eski araştırma dosyaları **silinmedi** (kanıt zinciri, alıntı, geçmiş referans değerli) ama **bağlayıcı karar dokümanı olarak görünmemeli**.

Özellikle riskli: `02-distilled-findings.md` "D1-D12 kararlar" bölümüyle bağlayıcı bir karar belgesi gibi okunabilir. Aslında bunlar 2026-05-19 itibarıyla eski-çerçevenin damıtmasıdır; bugünkü ADR'lerle çakışırlar.

## Karar

1. **Klasör yerini koru** (`docs/research/` altında kalır). Dosyaları taşıma; git history + cross-link'ler korunsun.
2. **Üst-marker ekle:** `docs/research/00-LEGACY.md` (`00-` prefix `ls -1` ASCII sırasında diğer `00-`/numerik dosyalardan önce, `L` (76) `r` (114)'den küçük olduğu için `00-research-brief.md`'den de önce gelir; yani klasör listing'inde en üstte görünür). Bu dosya:
   - Klasörün tarihsel statüsünü ilan eder (2026-05-19 öncesi araştırma).
   - "Bağlayıcı karar belgesi değil" der.
   - Yeni karar surface'inin `docs/decisions/` olduğunu söyler.
   - Hangi çerçevenin (NEEKO oyuncak / NQAI dört-ürün / dar Türkçe) artık geçerli olmadığını listeler.
   - Tarihsel okuma değeri olan dosyaları sayar (kanıt zinciri, vendor benchmark notları, G2P literatür taraması).
3. **İçerikleri rewrite etme:** D1-D12 kararları, ADR'leri vb. yeniden yazma; sadece üst-marker ile bağlamı netleştir.
4. **`docs/legacy-research/`'e taşımama:** Path değişimi cross-link ve external referansları kırar; underscore marker daha az invaziv.

## Sebep

- **Veri kaybı yok:** Araştırma çalışması (Atlas brief'leri, ChatGPT/Google findings, vendor reference) gerçek emek + hâlâ alıntılanır. Silme yanlış.
- **Karar uyuşmazlığı net:** Marker dosyası "bu klasör eski-çerçeve" der; gelecek okuyucu kafa karışıklığı yaşamaz.
- **Düşük maliyet:** 1 dosya yazma, klasör taşıma yok, dosya başına banner yok.
- **`00-` prefix:** Mevcut dosyalar (`00-research-brief`, `01-..`, vs) zaten numerik prefix kullanıyor; `00-LEGACY.md` aynı konvansiyona uyar ve `L` < `r` olduğu için `00-research-brief.md`'den önce sıralanır.

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| `docs/legacy-research/`'e taşı | Cross-link kırılması; eski PR/external ref'leri 404 |
| Her dosyaya frontmatter banner | 13 dosya × ~6 satır banner = 78 satır bloat; tek yerden bakım daha kolay |
| Sil + git log'a bırak | Aktif okumalar (D8/D9 hâlâ alıntılanabilir) için kayıp; Atlas brief'leri research değer taşır |
| `docs/research/README.md` adlandırma | `00-LEGACY.md` daha net sinyal verir; README "burada ne var" anlamı taşır, "bu eski" değil |

## Etkilenen dosyalar

| Dosya | Değişiklik |
| --- | --- |
| `docs/research/00-LEGACY.md` | **Yeni** — üst-marker (sıralamada başa düşer) |
| Diğer `docs/research/*.md` (13 dosya) | **Dokunma** |

## Doğrulama

`ls docs/research/` çıktısında `00-LEGACY.md` ilk satırda görünür (`00-` prefix + ASCII order). Bağımsız okuyucu bu dosyayı önce açar.

## İlgili

- [[reference-legacy-docs]] memory — bu kararın referansı buraya bağlı
- ADR-1..4 — yeni karar surface'i `docs/decisions/` altında
