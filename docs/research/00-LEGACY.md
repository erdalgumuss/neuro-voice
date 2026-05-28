# `docs/research/` — LEGACY (2026-05-19 öncesi araştırma)

> **Bu klasör bağlayıcı karar belgesi değildir.** İçindeki tüm dosyalar 2026-05-19'dan önce, repo'nun eski çerçevesinde yazıldı. O dönemin varsayımları artık geçerli değil:
>
> | Eski çerçeve (artık geçerli **değil**) | Yeni çerçeve (2026-05-28 itibarıyla geçerli) |
> | --- | --- |
> | NEEKO 3-7 yaş Türk çocukları için karakter-merkezli oyuncak | **NeuroVoice** uluslararası B2B TTS API platformu |
> | NQAI dört-ürün iç portföyü (NEEKO / NIVA / NeuroCourse / NARO) için kapalı ses omurgası | Dış müşteriye dağıtılabilir multilingual TTS API; iç ürünler birer müşteri olabilir |
> | "Türkçe TTS platformu" — dar dil konumlanması | "Underserved languages için kalite önderliği" — Türkçe ilk LoRA hattı, multilingual base |
> | "Faz A / Faz B / Dalga X.Y" milestone şeması | ADR'lerle yönetilen kararlar (bkz. [`docs/decisions/`](../decisions/)) |

## Şu an geçerli karar surface'i

Bağlayıcı kararlar için: [`docs/decisions/`](../decisions/) (ADR'ler) ve repo kökündeki [`CLAUDE.md`](../../CLAUDE.md).

Bu klasördeki dosyalardan **alıntı yapmak meşrudur** (vendor benchmark'ları, G2P literatürü, latency stratejisi, voice cloning industry patterns hâlâ kanıt değeri taşır). Ancak burada geçen herhangi bir **karar dili** ("D1 — şu seçildi", "ADR taslağı: X-001", "Faz-1 → Faz-2 yol haritası") tarihsel bağlamdır, mevcut kontrat değil.

Özellikle dikkat:

- [`02-distilled-findings.md`](02-distilled-findings.md) — "D1-D12 Kararlar Damıtması" bölümü yer alır; bunların hiçbiri şu anda bağlayıcı karar değildir. ADR-1..N ile birebir kıyaslama yapma; bağımsız okunmalıdır.
- [`00-research-brief.md`](00-research-brief.md) — NEEKO oyuncak ürün spec'i çerçevesindedir; ürün kararı olarak alma.
- [`01a-atlas-area1-5-tts-landscape.md`](01a-atlas-area1-5-tts-landscape.md), [`01b-...turkish-g2p.md`](01b-atlas-area2-turkish-g2p.md), [`01c-...speaker-ip.md`](01c-atlas-area3-8-speaker-ip.md), [`01d-...cds-data.md`](01d-atlas-area4-6-cds-data.md), [`01e-...eval.md`](01e-atlas-area7-eval.md) — Atlas araştırma brief'leri; teknik literatür özetleri olarak hâlâ kullanışlı.
- [`VENDOR_TTS_API_REFERENCE.md`](VENDOR_TTS_API_REFERENCE.md) — ElevenLabs / MiniMax / Deepgram / PlayHT API yüzey karşılaştırması; vendor parity tasarımı için referans değerini korur.
- [`03-voice-cloning-industry-patterns.md`](03-voice-cloning-industry-patterns.md), [`04-latency-80-100ms-strategy.md`](04-latency-80-100ms-strategy.md) — endüstri pattern'leri; spesifik karakter/ürün adlandırmaları LEGACY ama desen çıkarımı hâlâ alıntılanabilir.

## Bu klasöre kim/ne dokunmalı

- **Yeni içerik EKLEME.** Yeni araştırma için ayrı bir konum (örn. `docs/notes/` veya doğrudan ADR-pre-work) düşünülmeli.
- **Var olan dosyalara dokunma.** İçerik bozulmazsa cross-link + git history korunur.
- **Düzeltme/eklemeyi düşünüyorsan,** ADR yaz (bkz. [`docs/decisions/`](../decisions/)); araştırma dosyasını upstream'e itme.

---

Bu klasörün statüsüne karar veren ADR: [`docs/decisions/2026-05-28-research-legacy-disposition.md`](../decisions/2026-05-28-research-legacy-disposition.md).
