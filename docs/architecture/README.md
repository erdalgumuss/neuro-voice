# Architecture — neeko-voice

İlk araştırma brief'i (`docs/research/00-research-brief.md`) sonuçlanmadan mimari kararı yok. Bu klasör araştırma damıtması sonrası dolar.

## Planlanan dokümanlar (araştırma sonrası)

- `pipeline.md` — text → ses uçtan uca akış diyagramı + her aşama sorumluluğu
- `model-stack.md` — seçilen mimariler (G2P, akustik, vocoder, speaker, prozodi)
- `data-pipeline.md` — veri toplama, ön işleme, augmentation, manifest formatı
- `eval-protocol.md` — değerlendirme metrikleri ve A/B test akışı
- `deployment.md` — cloud + edge mix, latency stratejisi, streaming yaklaşımı
- `latency-budget.md` — TTFB hedefi ve katman bazlı bütçe (network + inference + vocoder)
