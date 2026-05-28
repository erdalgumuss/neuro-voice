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

## Kararlaştırılmış ADR'ler

`docs/decisions/` altında yaşar:

| ADR | Konu | Tarih |
| --- | --- | --- |
| ADR-1 | HTTP header + env var prefix → NeuroVoice (`X-NV-*`, `NEUROVOICE_*`) | 2026-05-28 |
| ADR-2 | Eval harness system slug `nqai` → `neurovoice` (dosya + class + CLI) | 2026-05-28 |
| ADR-3 | Model_id preset slug'lardan brand prefix'i kaldır (`voxcpm2-tr-*`) | 2026-05-28 |
| ADR-4 | API key format + admin auth cookie isimleri `nv_` prefix | 2026-05-28 |
| ADR-5 | `docs/research/` LEGACY işaretleme (`00-LEGACY.md` üst-marker) | 2026-05-28 |
| ADR-6 | Multilingual frontend — pluggable `lang_packs/<iso>/` mimarisi | 2026-05-28 |
| ADR-7 | Voice manifest schema v2 (`schema_version`, `base_model_id`, `lexicon`, `watermark`, `eval_pin`) + brand-neutral sample voice | 2026-05-28 |
| ADR-8 | LoRA fine-tune pipeline: notebook → `src/finetune/` paketi + `scripts/finetune.py` CLI | 2026-05-28 |
| ADR-9 | Public API spec stratejisi — native = FastAPI auto-OpenAPI + CI snapshot; parity = ElevenLabs spec'i `vendors/elevenlabs/` altında pin'lenip contract test ile sözleşmeye alınır (MiniMax v0 dışı; SDK gen ayrı ADR) | 2026-05-28 |
| ADR-10 | Voice license taxonomy + consent records + talent contracts — `voices.license_kind` kapalı liste CHECK constraint (6 değer); polymorphic `license_ref`; yeni `talent_contracts` + `voice_consent_records` tabloları; v0'da tenant-asserted consent yeterli, esnek genişletilebilir mimari | 2026-05-28 |
| ADR-11 | Voice lifecycle + right-to-be-forgotten — `voices.{frozen_at, frozen_reason, purge_after_at, purged_at}` kolonları + computed `lifecycle_state`; yeni `data_deletion_requests` tablosu; synthesis gate (HTTP 410 / WS 1008); operator freeze/unfreeze/purge + tenant deletion-request endpoint'leri; 30 gün GDPR retention default | 2026-05-28 |
| ADR-12 | Eval pin — 3-metric suite (Whisper-WER+CER mevcut, UTMOSv2 wire, yeni SECS via WavLM-base-plus-sv), `VoiceRepo.pin_eval` + `POST /admin/voices/{id}/eval-pin` + `scripts/eval_pin.py`, `VoicePublic.eval_metrics` expose, `[project.optional-dependencies] eval` grubu | 2026-05-28 |

## Bilinçli ertelenmiş kararlar (sıradaki ADR'ler)

1. **Billing / metering / quota modeli** — usage_records'tan billing'e bağlanma mantığı; per-tenant quota enforcement; subscription tier'ları.
2. **Multi-region deployment** — R2 region seçimi + worker pool coğrafyası + cross-region eviction politikası + jurisdiction-spesifik retention override'ları (ADR-11'in default 30 gününü EU/TR/US başına override etmek).
3. **Cron worker / scheduler infra** — periyodik expired-contract scan, scheduled purge auto-execute, operator notification queue. ADR-11 manuel/operator-driven path'i kurdu; otomatik scheduler yatırımı bu ADR'de.
4. **Worker-side synthesis re-check** — ADR-11 gateway-side gate'i kurdu; queue'da bekleyen job freeze sonrası worker re-validate etmiyor (race window ~3-30s). Yüksek-trafikte gerekirse.
5. **Eval promotion gate** — `release_status='production'` transition'ında `eval_metrics IS NOT NULL` zorunluluğu. ADR-12 pin path'i kurdu; mevcut voice'lar pin'lendikten sonra gate eklenir.
6. **Watermark generation** — `voices.watermark_key_id` runtime synthesis'te imzalama; ayrı bir ADR konusu (fine-tune değil).
7. **Multi-tenant fine-tune servisi** — `src/finetune/`'ı queue-backed multi-tenant servisle sarmak (`POST /v1/finetune-jobs`); şu an CLI-only.
8. **SDK generation** — kendi Python/TS SDK'larımızın üretimi ve yayın disiplini (ADR-9 kapsamı dışında bırakıldı; pyproject yayın disiplini önce oturmalı).
9. **MiniMax parity** — v0'da yok; karar değişirse ayrı ADR.
10. **Operator admin UI** — ADR-10 + ADR-11 endpoint'leri canlı (`/admin/voices/{id}/freeze|unfreeze|purge`, `/admin/data-deletion-requests/...`, talent_contracts CRUD); UI (HTMX/SPA) ileride.
11. **GDPR madde 15 right-to-access (data export)** — silme ile simetrik export endpoint'i; ADR-11'in dışı.
12. **Test suite (Codex authoring)** — `tests/` boş, `old_tests/` parkta; Codex mimariye göre adım-adım yeniden yazıyor (bkz. `feedback_codex_writes_tests` memory). ADR-9/10/11 follow-up'ları (CI snapshot + ElevenLabs contract test + license/consent enrollment + lifecycle gate) bu paketin parçası.

Her biri kararlaştığında `/nqv-adr <slug>` ile kayda geçer.
