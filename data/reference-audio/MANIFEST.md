# NEEKO Referans Ses Manifesti

**Onay tarihi:** 2026-05-19
**Onaylayan:** Erdal Mert Karaaslan
**Statü:** v0.1 — Faz-1 köprü dönemi referans sesi (voice talent kayıt gelene kadar)

---

## Aktif dosyalar

| Dosya | Rol | Süre | Format |
|---|---|---|---|
| [`neeko-v0.1-reference.mp3`](neeko-v0.1-reference.mp3) | Birincil NEEKO karakter referans sesi | ~2 dk | MP3, 44.1 kHz |

---

## Üretim metadata

**Üretici:** ElevenLabs (Erdal'ın iterasyon çalışması ile)
**Model:** `eleven_multilingual_v2`
**Voice preset:** NEEKO_V0.1_TR_NeutralGuide (Erdal'ın özelleştirdiği)
**Settings:**
- Speed: 0.86
- Stability: 50
- Similarity Boost: 75
- Style Exaggeration: 0
- Speaker Boost: aktif

**İçerik:** Damıtmadaki test paragrafı — NEEKO intro monoloğu + 10 cümle test seti (oyun / uyku / ders / şefkat / soru / heyecan / sayı-tarih / kısaltma / kod-karışımı / hikaye)

**Orijinal dosya:** `experiments/2026-05-19-elevenlabs-reference/output/ElevenLabs_2026-05-19T20_17_18_NEEKO_V0.1_TR_NeutralGuide_gen_sp86_s50_sb75_se0_b_m2.mp3` (history için orada bırakıldı)

---

## Kullanım rolü

Bu ses, NEEKO karakterinin **köprü dönemi (Faz-1)** karakter sesidir. Şu amaçlarla kullanılır:

1. **Voice cloning referans audio'su** — Chatterbox Multilingual, VoxCPM2 ve diğer açık-ağırlık modellerin `reference_audio` parametresine bu dosya verilir. Modeller bu sesin tone color'ını taklit ederek üretim yapar.
2. **MVP demoları + ürün lansmanı önesi içerik üretimi** — pitch deck, ilk pazarlama videoları, ilk kullanıcı testleri için.
3. **Eval baseline** — voice talent kayıt sonrası fine-tune'lu modelin "bunu geçtik mi?" karşılaştırma noktası.

**Kullanım YASAĞI:**
- Bu dosyadan **LoRA fine-tune veri seti üretmek** ElevenLabs ToS açısından gri alan. Voice talent kayıt + LoRA fine-tune Faz-1 hafta 5-7'de gerçek dataset ile yapılacak. Bu dosya **sadece referans audio** modunda kullanılır, fine-tune training data değil.
- Bu dosyanın **ticari dağıtımı** (örn. Spotify, podcast platformu, ses kütüphanesi) yapılamaz. Sadece NEEKO ürün ekosistemi içinde, model referans audio'su olarak.

---

## Sonraki adımlar

1. **Voice cloning testi** — Chatterbox HF Space'inde bu dosyayı reference olarak yükle, `data/test-sets/v0.1-mini.md`'deki 10 cümleyi clone modda generate et. Çıktı → `experiments/2026-05-20-chatterbox-voice-clone/output/`
2. **VoxCPM2 voice cloning testi** — aynı 10 cümle, aynı referans, ikinci model adayı. Çıktı → `experiments/2026-05-20-voxcpm2-voice-clone/output/`
3. **Yan yana A/B karşılaştırma** — orijinal ElevenLabs çıktısı vs Chatterbox-clone vs VoxCPM2-clone, dinleme paneli (Erdal + Atlas + opsiyonel aile)
4. **Zor cümle testleri** (Erdal'ın "zor sesleri test edebiliriz" notu) — sayı/tarih/kısaltma/kod-karışımı + uzun-form (2-5 dakikalık masal) + cross-emotion drift testi
5. **Faz-1 hafta 5-7 voice talent kaydı geldiğinde** → bu referans sesin yerini gerçek profesyonel kayıt + LoRA fine-tune alır. Bu dosya tarihsel referans olarak arşivlenir.

---

## Voice talent süreci paralel devam ediyor mu?

**Karar Erdal'da:**
- **Evet, paralel:** Casting + sözleşme + kayıt süreci ilerler (4-6 hafta), ElevenLabs köprü olur, voice talent geldiğinde production geçişi yapılır.
- **Hayır, ertelendi:** ElevenLabs ile MVP lansmanı sonrası karar, voice talent ihtiyacı yeniden değerlendirilir.

**Atlas önerisi:** Paralel ilerleme. Casting outreach maillerini bu hafta yolla; ElevenLabs köprüsü ile ürün lansmanı sürerken arka planda voice talent süreci olgunlaşır. Bu, "bir tek vendor'a bağımlı" riski elimine eder + uzun vadeli IP sahipliği güvenceye alır.

---

## Sürüm geçmişi

- **v0.1 (2026-05-19):** Erdal ElevenLabs Multilingual v2 üzerinden NeutralGuide preset'inde NEEKO sesini üretti. İterasyon çalışması sonrası onaylandı.
