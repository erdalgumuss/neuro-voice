# neeko-voice — Repo Disiplini

Neeko'nun TTS yığınını uçtan uca yöneten repo. Üst-katman `/home/alfonso/neeko-firmware/CLAUDE.md` 7 disiplin kuralı geçerlidir; aşağıdaki disiplinler bu repoya özeldir.

## Repo-özel disiplinler

1. **Kaynaklı iddia.** Her sayı, her benchmark, her "X iyi" cümlesi link/dayanak ister. Memory: `feedback_evidence_over_convention`. "Bence iyi" yetmez; MOS X, Elo Y, kaynak Z formatı.
2. **Reproducibility.** Her deney = config dosyası + seed + commit hash + çıktı klasörü. Format: `experiments/YYYY-MM-DD-<slug>/{config.yaml, log.md, output/}`.
3. **Önce karar, sonra kod.** Mimari/model/eval değişiklikleri `docs/decisions/` altında bir satır olmadan kodlanmaz. Kararın gerekçesi yazılmalı.
4. **Eval kataloğu sabit.** Aynı test cümleleri (`data/test-sets/`), aynı metrikler. Modeller değişir, ölçü değişmez — yoksa karşılaştırma anlamsız.
5. **Veri lisansı net.** `data/raw/` içine giren her ses dosyası için kaynak + lisans + voice talent kontratı `data/raw/MANIFEST.md`'de.

## Klasör yapısı

| Yol | Ne yapar |
| --- | --- |
| `docs/research/` | Araştırma brief'leri ve damıtmaları (ChatGPT, Şefika, kendi taramamız) |
| `docs/decisions/` | TTS-özel decision log |
| `docs/architecture/` | Pipeline, model stack, deployment mimari notları |
| `src/g2p/` | Türkçe text frontend (grapheme → phoneme + normalizasyon) |
| `src/bench/` | Baseline modelleri karşılaştırma scriptleri |
| `src/finetune/` | LoRA + adaptation scriptleri |
| `src/eval/` | MOS, NISQA, WER, speaker similarity |
| `data/phonemes/` | Türkçe fonetik sözlük + kural seti |
| `data/test-sets/` | Kürate edilmiş test cümleleri (versiyonlu) |
| `data/raw/` | gitignore'lu — ses kayıtları + MANIFEST.md |
| `notebooks/` | Kaggle/Colab defterleri |
| `experiments/` | Her deneyin config + log + çıktı klasörü |

## Hassas veri

- `data/raw/` gitignore'lu — ses kayıtları + voice talent materyali repo'ya commit edilmez
- Voice talent kontratları + IP belgesi → `NeuroQubit_NEEKO/private/` (bu repo dışı)
- API anahtarları (HuggingFace, RunPod, Lambda Labs, WandB) → `.env`, asla commit edilmez

## Çalışma akışı

1. Araştırma brief (`docs/research/00-...`) → ChatGPT/Atlas çıktısı → kürate damıtma (`docs/research/02-...`)
2. Damıtmadan karar (`docs/decisions/`)
3. Karardan deney (`src/` + `experiments/<slug>/`)
4. Deney → eval (`src/eval/`)
5. Eval → yeni karar veya iterasyon

## Bu repoda yapmayacaklar

- Üst-katman karar log'unu burada tekrar tutma — TTS dışı kararlar `NeuroQubit_NEEKO/admin/decision-log.md`'de
- Voice talent ses dosyalarını commit etme
- Henüz kaynaklanmamış model/metrik için kod yazma — önce decision satırı
- ElevenLabs/OpenAI/Google ticari API'larını "rekabet" değil, sadece referans olarak konumlandır
