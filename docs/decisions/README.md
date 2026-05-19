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
| 2026-05-19 | İlk öncelik G2P | Türkçe text frontend ilk dokunulacak katman | Açık kaynak TTS modellerinin en zayıf olduğu + en yüksek leverage'lı katman (Atlas analizi 2026-05-19) | `docs/research/00-research-brief.md` alan 2 |
| 2026-05-19 | Repo açılışı | `neeko-voice` repo'su `/home/alfonso/neeko-firmware/` altında bağımsız git deposu olarak başlatıldı | TTS yığınını kendi disiplinine ayırma; `neeko_server/` (backend) ile kod karışıklığını önleme | Tüm repo |
