# Decision Log — neeko-voice

Her stratejik karar burada satır olur. Mikro koddan çok mimari/yön kararları: "X modeli seçtik", "Y mimari yaklaşımını terk ettik", "Z eval metriği eklendi", "W veri seti dahil edildi" gibi.

## Format

Yeni karar her zaman tabloya **en üste** eklenir (en yeni üstte). Sütunlar:

- **Tarih** — `YYYY-MM-DD`
- **Konu** — bir-iki kelime başlık
- **Karar** — bir cümle: ne karar verildi
- **Gerekçe** — neden bu karar (kaynaklı tercih edilir)
- **Etki** — hangi dosyalar/klasörler etkilendi

## Kararlar

| Tarih | Konu | Karar | Gerekçe | Etki |
| --- | --- | --- | --- | --- |
| 2026-05-24 | Lansman ses dağılımı | Başlangıç katalog dağılımı **1 NEEKO + 2 NIVA + 2 NeuroCourse** olarak kilitlendi; NARO ve developer sandbox Faz-2'ye ertelendi | Erdal direktifi: minimum 5 ses net, ilk hizmet kapsamı 2 NIVA + 1 NEEKO + 2 NeuroCourse. 5 slot yeterli ama placeholder değil, her slot gerçek veya hukuken temiz referans ses ister | `configs/seed_voices.yaml`, `tests/test_seed_catalog.py` |
| 2026-05-24 | NQAI TTS Platform v0.1 — base model + cloning + API | **Chatterbox Multilingual (MIT)** üzerine zero-shot voice cloning + FastAPI HTTP/streaming server + 5-slot voice registry (YAML-backed, runtime enroll/delete) ilk platform release; Türkçe text frontend v0 (cümle segmentasyonu + temel TN) inline; Türkçe SFT + LoRA Faz-2'ye ertelendi | Saha kanıtı: Chatterbox MTL Türkçe voice clone T4'te RTF 1.2-1.4, 15 sn referansla çalışıyor (`experiments/2026-05-20-chatterbox-voice-clone/`). VoxCPM2 ikinci aday olarak ileride drop-in swap için engine adapter pattern korunur. 5 ses minimum (Erdal direktifi 2026-05-24) — 40-50 ses dağınıklık, 5 ses voice catalog + IP sahipliği temiz. Geliştirici ucu zero-shot enroll endpoint olarak gelir, public clone değil (auth'lu) | `src/server/`, `src/registry/`, `configs/voices/`, `docs/architecture/platform-v0.1.md` |
| 2026-05-19 | NEEKO ses v0.1 köprü | ElevenLabs Multilingual v2 / NeutralGuide / sp86 s50 sb75 preset ile Erdal'ın ürettiği ses NEEKO karakter sesi v0.1 olarak onaylandı | Voice talent casting+sözleşme+kayıt 4-6 hafta sürer; ElevenLabs köprü olarak Faz-1 demolarını hızlandırır; voice talent süreci paralel | `data/reference-audio/neeko-v0.1-reference.mp3` + `MANIFEST.md` |
| 2026-05-19 | NEEKO cinsiyet | NEEKO karakter sesi **cinsiyet nötr (androgynous)** — kadın/erkek değil, fantastik karakter | Modern + inclusive konumlanma; gender stereotyping yok; Pepee/Niloya'dan marka ayrımı; Erdal kararı | `docs/character/neeko-v1-spec.md` |
| 2026-05-19 | Premium hedef çerçeve | Premium = ElevenLabs'ı genel TTS'de geçmek değil; **TR + karakter + call-center + child-directed dar domain'de** geçmek | Dar domain savaşı 6-9 ayda kazanılabilir; genel TTS 2-3 yıl + ölü savaş | `docs/research/02-distilled-findings.md` |
| 2026-05-19 | NQAI ses omurgası kapsamı | TTS yığını NEEKO'ya özel değil; NEEKO + NIVA + NeuroCourse + NARO dört üründe ortak omurga | Ölçek ekonomisi + IP konsolidasyonu + NeuroQubit "Türkçe konuşan AI omurgası" konumlanması; Erdal beyanı 2026-05-19 | Tüm repo mimarisi (multi-product baştan) |
| 2026-05-19 | İlk öncelik G2P | Türkçe text frontend ilk dokunulacak katman | Açık kaynak TTS modellerinin en zayıf olduğu + en yüksek leverage'lı katman (Atlas analizi 2026-05-19) | `docs/research/00-research-brief.md` alan 2 |
| 2026-05-19 | Repo açılışı | `neeko-voice` repo'su `/home/alfonso/neeko-firmware/` altında bağımsız git deposu olarak başlatıldı | TTS yığınını kendi disiplinine ayırma; `neeko_server/` (backend) ile kod karışıklığını önleme | Tüm repo |
