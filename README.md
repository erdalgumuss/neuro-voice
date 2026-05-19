# neeko-voice

Neeko'nun Türkçe + çocuk-yönelimli TTS yığını. Karakter sesi sahipliği, maliyet kontrolü ve IP bağımsızlığı için açık-ağırlıklı modeller üzerine fine-tune + Türkçe text frontend katmanı kurar.

## Hedef

12 ay içinde **Türkçe + 3-7 yaş çocuk konuşması + sürdürülebilir karakter sesi** alt-domain'inde ElevenLabs-grade veya üstü kalite. Genel TTS yarışına girmiyoruz; kendi sahamızı kuruyoruz.

## Şu an

**Pre-research.** İlk sektör tarama brief'i hazır:
- [docs/research/00-research-brief.md](docs/research/00-research-brief.md) → ChatGPT (veya benzeri research-grade LLM) için doğrudan kopyalanabilir.

Sonuç döndüğünde `docs/research/01-chatgpt-findings.md` olarak repo'ya iner, `02-distilled-findings.md` altında damıtılır, oradan ilk karar (`docs/decisions/`) ve ilk deney (`experiments/`) çıkar.

## Planlanan mimari katmanlar

1. **Text frontend / G2P** — Türkçe fonetik + normalizasyon (sayı, tarih, kısaltma, vurgu)
2. **Akustik model** — açık-ağırlıklı baseline + Türkçe fine-tune
3. **Vocoder** — modern modellerde entegre, başlangıçta dokunmuyoruz
4. **Speaker embedding / karakter sesi** — Neeko'nun ses kimliği (LoRA)
5. **Prozodi / duygu** — çocuk-yönelimli ton varyantları
6. **Eval + deployment** — MOS, NISQA, WER, speaker similarity + streaming/edge optimizasyon

## Repo nasıl çalışır

Disiplin ve klasör yapısı için: [CLAUDE.md](CLAUDE.md).

## Üst katmanla ilişki

Bu repo `/home/alfonso/neeko-firmware/` workspace'inin altında bağımsız git deposudur. Üst-katman 7 disiplin kuralı geçerlidir; `neeko_server/`, `NeuroQubit_NEEKO/`, `neeko-design-framework/` ile aynı seviyede.
