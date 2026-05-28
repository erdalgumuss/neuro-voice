# CLAUDE.md — nqai-voice

> Üst-katman bağlamı için: `/home/alfonso/neeko-firmware/CLAUDE.md`. Bu dosya o repo'ya özel ve bağlayıcı; çelişme olursa **bu dosya** geçerli.

## Kimlik

Bu repo **uluslararası ölçekte dağıtılabilir bir TTS API** servisidir. Referans noktaları: **ElevenLabs**, **MiniMax Speech**, **Deepgram TTS**, **PlayHT**. Konum: "underserved languages için kalite önderliği yapan, çok dilli base + dil/karakter özgül LoRA katmanlarıyla farklılaşan, B2B API SaaS". Türkçe ilk LoRA hattımız; sadece-Türkçe konumlanma değil.

**Base model:** VoxCPM2 (Apache 2.0). Üzerine LoRA adapter katmanları (dil / karakter / domain). LoRA pipeline'ı bu repo'da paralel kurulur; "üretim hazır" değildir.

### Bu repo NE DEĞİL

- NEEKO oyuncak ses omurgası değil.
- NQAI dört-ürün iç portföyü (NEEKO / NIVA / NeuroCourse / NARO) için kapalı servis değil.
- "Sadece Türkçe TTS platformu" değil — bu eski çerçeve.

Üst-katman (`neeko-firmware/`) iş katmanında bu repo dış-müşteriye dağıtılabilir API; iç ürünler bu API'nin **müşterileri** olabilir.

## Hedef ve hipotez

- **Kim için:** Geliştiriciler, AI-agent şirketleri, content/dub stüdyoları, audiobook üreticileri — özellikle düşük-orta hizmet gören dillerde iyi TTS arayanlar.
- **Niye çalışır:** ElevenLabs/MiniMax İngilizce'de iyi; özgün dillerde (Türkçe, Lehçe, Endonezce, Fars, Vietnamca, ...) telaffuz/prozodi/kod-karışımı kırılır. **VoxCPM2 + dil-özgül LoRA + dil-özgül text frontend** üçlüsü dar dilde kalite önderliği + API parity = farklılaşma.
- **Ölçek varsayımı:** Bugün 1, yarın 100, sonra 100K eş zamanlı kullanıcı. Mimari milyonlarca isteğe **uygun** olmalı; **prematür ölçeklendirme yasak** (tek node + queue 100K req/gün taşır).

## Mimari nirengi noktaları (v0)

Aşağıdakiler kabul. Değişecekse `/nqv-adr` ile ADR yaz.

1. **Gateway / Worker ayrımı.** API hiçbir zaman in-process inference yapmaz. Tüm istek Redis kuyruğuna düşer; worker tüketir; sonuç stream / artifact olarak döner.
2. **Streaming-first.** Default chunked transfer + WebSocket input streaming. Buffered yanıt opsiyonel.
3. **Multi-tenant from day one.** Tenant → API Key (Argon2id, tek yön) → Voice access list. "Single user" ayrı mod değil; tek-tenant'lı multi-tenant.
4. **Object storage tek kaynak.** Üretilen ve referans ses **her zaman** R2/S3'te. Lokal disk asla long-term değil.
5. **Idempotency from day one.** Her job submit `Idempotency-Key` ile dedup'lanabilir.
6. **Observable by default.** Trace ID, job lifecycle event, model call latency + token + LoRA hit/miss metriği.
7. **Vendor parity (selective).** ElevenLabs benzeri URL alias'ları korunur (entegrasyon dostu). API'miz kendi spec'imiz; vendor bağımlılığı yok.
8. **Multi-language base.** VoxCPM2 30 dili destekliyor; LoRA katmanları dil-özgül temizleme. Text frontend (G2P + normalization + lexicon) **pluggable language pack**.
9. **Model registry kod-içi değil, manifest.** Voice = (base_model_id, adapter_id, lexicon_id, frontend_pack_id, eval_pin). Registry DB'de, ağırlıklar R2'de.
10. **Reproducibility birinci sınıf.** Her job için engine_inputs audit trail (seed, cfg, timesteps, model_id, adapter_id) Postgres'te. Aynı input → aynı output kanıtlanabilir.

## Mevcut kod taban (2026-05-28 itibarıyla)

**Tutulacak kemikler (surgical reset — kod kalır, çerçeve değişir):**

| Modül | Rol | Durum |
| --- | --- | --- |
| `src/server/` | FastAPI gateway, auth, queue submit, voice CRUD | Sağlam; string'ler İngilizce'ye + yeni framing'e |
| `src/worker/` | VoxCPM2 inference, asyncio, Redis queue consumer | Sağlam; LoRA cache + warmup mevcut |
| `src/db/` + `migrations/` | Postgres + Alembic 9 migration | Sağlam |
| `src/storage/r2.py` | Cloudflare R2 (S3 API) | Sağlam |
| `src/observability/metrics.py` | Prometheus | Sağlam |
| `src/audio/` | mp3/opus/wav encoder + LUFS/DC/peak postprocess | Sağlam |
| `src/repos/` | Tenant / API key / voice / usage / audit | Sağlam |
| `src/server/security/` | Argon2id, JWT, password | Sağlam |
| `src/frontend/` | text normalize + numbers + segment (TR-bound) | Refactor: dil-pluggable yap |
| `src/eval/` | Whisper-WER + system adapters (scaffold) | Genişlet |
| `tests/` | 38 test, testcontainers'lı integration | Sağlam |

**Yeniden ele alınacak veya boş:**

| Modül | Eski niyet | Yeni karar |
| --- | --- | --- |
| `src/g2p/` (boş) | Türkçe G2P | Multi-dil G2P stratejisi; ADR ile tasarım |
| `src/bench/` (boş) | Bench paketi | `scripts/*_bench.py` taşı + standartlaştır |
| `src/finetune/` (boş) | LoRA training | Notebook'tan kod tabanına taşı; ML pipeline ADR'i |
| `src/live/` (sadece protocol.py) | LiveKit/RTC | İhtiyaç doğrulanana kadar parking; karar ertelenebilir |
| `src/registry/` | Voice catalog | Voice manifest schema'sını **ADR**'le yeniden tanımla |
| `configs/voices/neeko-v01.yaml`, `data/neeko-test-ses/` | NEEKO karakter | Sample/seed olarak yeniden adlandır veya `examples/` altına |
| `docs/research/` (14 dosya) | Eski araştırma | `docs/research/_LEGACY.md` notu ekle veya `docs/legacy-research/` |
| `pyproject.toml` description | "NQAI Türkçe TTS platformu" | "Multilingual TTS API platform" |
| `README.md` (silinmiş) | — | Yeniden, uluslararası kimlikle, İngilizce |
| `profosyonel-destek-amaca-yonelik-aciklama.md` | Yanlış yerde | Sil |

## Drift disiplini (bu repo'nun en yüksek riski)

Bu repo bilinçli olarak amaç-kaydırılmış bir geçmişten geliyor. Drift'in en olası sızıntı noktaları:

1. **`pyproject.toml`** — paket adı + description.
2. **`README.md`** — yeniden yazılırken eski dili taşımama.
3. **Log/error string'leri** Türkçe gömülü olabilir (`src/server/`, `src/worker/`) — public-API'de İngilizce.
4. **Voice catalog seed'leri** — NEEKO referansları sample olarak işaretli, "ürün spec" olarak değil.
5. **Test isimleri/docstring'leri** — eski Faz/Dalga adlandırması.
6. **`docs/research/`** — bağlayıcı görünmemesi için LEGACY etiketi.
7. **Commit mesajları** — yeni iş eski Faz/Dalga şemasını kullanmaz.

**Kural:** Cross-cutting bir değişiklik öncesi `/nqv-drift-check` çalıştır. Sonra **tek turda** tüm yansımaları güncelle (kod + test + config + docs + ADR).

## Repo dil disiplini

- **Kod** (identifier, comment, log, error message): **İngilizce**.
- **CLAUDE.md, ADR'ler, runbook'lar:** **Türkçe** (üst-katman tutarlılığı).
- **README, API spec, SDK docs, public docs:** **İngilizce**.
- **Commit mesajları:** İngilizce.
- **PR açıklaması:** Bağlama göre; iç tartışma TR, ana açıklama EN.

## Yapılmayacaklar

- NEEKO-spesifik özellik tasarımı (child-directed prozodi, pedagog network referansı, NQAI iç-ürün entegrasyonu). Bu API'nin müşterileri yapar; biz yapmayız.
- "Sadece Türkçe iyi yapıyoruz" dar konumlanma. Türkçe başlangıç; başlık "underserved languages well".
- "Kanıtlandı / kanıt" iddia dili → "gözlemlendi / doğrulandı / ölçüldü".
- Boş bardak / risk-listesi formatı → önce çalışan + sonra eksik.
- "AI dolu" jargon, "devrim niteliğinde" → Stripe / Linear / Anthropic tonu.
- Yorumda "ne yaptığını" anlatma → "niçin" anlat veya yorum yazma.
- Yarım soyutlama, premature factoring, "ileride lazım olur" kod.
- Boş `except`, `print` log, "deneme-yanılma" debug bırakma.
- v0.x boyunca backwards-compat kabuğu; v1.0'dan sonra semver.

## Slash komutlar

Bu repo'ya özel:
- `/nqv-drift-check` — kimlik / dil / API / voice / config drift taraması.
- `/nqv-adr <slug>` — `docs/decisions/YYYY-MM-DD-<slug>.md` iskeleti.

Built-in (ihtiyaca göre): `/code-review`, `/run`, `/verify`, `/security-review`, `/init`, `/simplify`.

## Subagent'lar

- `tts-platform-reviewer` — diff/PR'ı TTS API SaaS + ML pipeline + drift gözüyle review. `/code-review` yanında çağrılabilir.

## Memory

`/home/alfonso/.claude/projects/-home-alfonso-neeko-firmware-neeko-voice/memory/` altında. Bu repo'ya özel; üst-katman ve `neeko-server` memory'leriyle KARIŞMAZ.

## Hassas dosyalar

`.claude/settings.json` deny ile korunan:
- `.env*`, `**/private/**`, `**/secrets/**`
- `*.pem`, `*.key`, `credentials.*`
- WebFetch + dış HTTP (curl/wget) varsayılan kapalı.

Model checkpoint (`*.safetensors`, `*.bin`) — Git LFS olmadan commit etme. Ağırlıklar R2'de, manifest'le referans.

## Bilinçli ertelenmiş kararlar (ADR'leri sırada)

1. Multi-language text frontend / G2P stratejisi (`src/g2p/` doldurma planı).
2. LoRA training kodunu notebook'tan kod tabanına taşıma şekli (`src/finetune/` mimari).
3. Voice manifest schema v2 (dil + karakter + lisans + watermark + eval pin).
4. Public API spec'i (kendi spec mi, OpenAI/ElevenLabs uyumlu mu, ikisi de mi).
5. Billing / metering / quota modeli.
6. Multi-region deployment (R2 + worker pool coğrafyası).
7. Voice ownership + KVKK/GDPR/voice-talent kontrat çerçevesi (uluslararası).
8. `docs/research/` LEGACY işaretleme stratejisi.

Her biri kararlaştığında `/nqv-adr <slug>` ile kayda geçer.
