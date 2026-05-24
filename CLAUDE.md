# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`neuro-voice` — NQAI'nin Türkçe + voice-cloning + streaming TTS yığını. **VoxCPM2 (Apache 2.0, OpenBMB, 2B param)** üzerine multi-tenant API gateway + Türkçe text frontend + Stripe-style async job queue + R2 object storage. Üst-katman `/home/alfonso/neeko-firmware/CLAUDE.md` 7 disiplin kuralı geçerlidir; aşağıdakiler bu repoya özel ek disiplinlerdir.

## Şu anki durum (2026-05-25 — Faz C v1 + 5-layer audit hotfix turu)

**Faz B.1 (worker/gateway ayrımı) + B.1.5 (frame-by-frame streaming bridge) + C v0/v1 (Prometheus + heartbeat backpressure + waterfall persistence + Grafana + pgBouncer + load/chaos harness) tamam.** Üstüne 5 paralel agent ile end-to-end audit + 4-commit hotfix yapıldı (`ca0b47c..` audit-fix dizisi): migration 0002 in-place fix, `/v1/tts/stream` deprecation header temizliği, CORS credentials, retry-aware `seen_seq` (attempt epoch + dedupe reset), sync paths `reserve_or_get`, PoisonJob → DLQ archive, TTS_REQUESTS{status=auth_failed/backpressure} + dedicated `nqai_tts_deprecated_endpoint_total`, FK ondelete policies, iter_engine_chunks cancellation, gateway_first_byte_ms timer + WAV header semantics, XAUTOCLAIM env var disambiguation, observability.md aspirational banner, dead `src/live/*` + worker/live primitives temizliği.

> **Audit özeti:** [docs/audit/](docs/audit/) — `checkpoint-2026-05-24-faz-c-v1-exit.md` + agent task transcripts. Faz D'ye geçmeden önce real-GPU latency_bench + load_bench koşumları operator turu olarak bekleniyor (kod değil, ground-truth eksiği).

Canlı doc'lar:
- Kanonik mimari (v1.0 hedefi): [docs/architecture/scale-roadmap.md](docs/architecture/scale-roadmap.md)
- Mimari index: [docs/architecture/README.md](docs/architecture/README.md)
- Veri modeli: [docs/architecture/data-model.md](docs/architecture/data-model.md)
- Multi-tenant auth: [docs/architecture/auth-multi-tenant.md](docs/architecture/auth-multi-tenant.md)
- Streaming protokol (HTTP chunked primary + async jobs + sync deprecated proxy; WS yok): [docs/architecture/streaming-protocol.md](docs/architecture/streaming-protocol.md)
- Observability (Faz C spec'i): [docs/architecture/observability.md](docs/architecture/observability.md)
- VoxCPM2 entegrasyon detayları + LoRA hattı: [docs/architecture/voxcpm2-integration.md](docs/architecture/voxcpm2-integration.md)
- v0.2 single-process MVP (referans, kısmen superseded): [docs/architecture/platform-v0.2.md](docs/architecture/platform-v0.2.md)

| Komut | Ne yapar |
| --- | --- |
| `pip install -e ".[dev]"` | dev + test bağımlılıkları (boto3, fakeredis, moto, aiosqlite, argon2-cffi, pyjwt dahil) |
| `python -m pytest` | **384 test** (~55 s) — frontend + auth + repos + R2 + async jobs + API smoke + observability + bench (VoxCPM stub'lu) |
| `ruff check src tests` | lint (clean baseline) |
| `docker compose -f docker-compose.dev.yaml up -d` | gateway + Postgres 16 + Redis 7 lokal stack |
| `alembic upgrade head` | DB schema forward-only migrate |
| `python scripts/seed_operator.py --email <you>` | admin UI için operator oluştur |
| `PYTHONPATH=src python -m uvicorn server.main:app` | server'ı lokalde başlat (DB + Redis env zorunlu, ilk request'te ~8 GB VRAM) |
| `python scripts/smoke_test.py --base-url ... --api-key ...` | sync `/v1/tts` eval, per-call RTF + WAV dump |
| `python scripts/bootstrap_voices.py --base-url ... --api-key ...` | `configs/seed_voices.yaml` üzerinden 5-slot toplu enroll |
| `python scripts/migrate_filesystem_to_db.py` | filesystem voice YAML'ları → DB (+ opsiyonel R2 upload) |
| Colab → [notebooks/03-platform-server-colab.ipynb](notebooks/03-platform-server-colab.ipynb) | T4/A100'de server + cloudflared tunnel, ~6-8 dk (v0.2 era — A.6 cutover sonrası refresh bekliyor) |

Türkçe SFT + per-character LoRA Faz 2-3'e ertelendi — bkz. [scale-roadmap.md §10-13](docs/architecture/scale-roadmap.md) ve [voxcpm2-integration.md §10](docs/architecture/voxcpm2-integration.md).

Araştırma katmanı paralel devam ediyor:

- `docs/research/02-distilled-findings.md` — NQAI ses omurgası v0.1 sentezi (12 karar, Faz-1/2/3 yol haritası)
- `docs/decisions/README.md` — stratejik karar log'u (en yeni: 2026-05-24 async TTS jobs + R2 + A.6 cutover + LoRA cache LRU)
- `docs/audit/` — `faz-a-self-audit.md` (lint + dosya), `faz-a-mlops-audit.md` (mimari + $/saat tablosu), `checkpoint-2026-05-24-faz-a-exit.md` (mevcut faz checkpoint)
- `notebooks/01-voxcpm2-tr-demo.ipynb` — VoxCPM2 baseline 10 cümle (Kaggle T4)
- `notebooks/03-platform-server-colab.ipynb` — full platform deploy (Colab, v0.2 era)

## Çalışma akışı (sıkı sırada)

1. Araştırma brief (`docs/research/00-...`) → harici LLM/research çıktısı `docs/research/01-...` olarak iner
2. Damıtma `docs/research/02-distilled-findings.md` altında kürate edilir
3. Damıtmadan karar `docs/decisions/README.md`'ye **satır olarak** eklenir (en yeni üstte; sütunlar: Tarih · Konu · Karar · Gerekçe · Etki)
4. Karardan deney: `experiments/YYYY-MM-DD-<slug>/{config.yaml, log.md, output/}`
5. Deney → `src/eval/` üzerinden ölçü → yeni karar veya iterasyon

**Önce karar, sonra kod.** Mimari/model/eval değişiklikleri decision log'a satır olmadan kodlanmaz.

## Çekirdek mimari katmanları

Damıtmadaki üç-katmanlı spec ([02-distilled-findings.md §4](docs/research/02-distilled-findings.md)) Faz A'da multi-tenant data plane + auth + storage ile genişledi. Mevcut katmanlar:

**Data plane (Faz A):**
- `src/db/` — SQLAlchemy 2 async + asyncpg + aiosqlite (test); 7 ORM tablosu
- `src/repos/` — repository pattern, **tenant_id constructor-zorunlu** (D-08); 7 repository
- `src/storage/` — R2 (S3-compat) adapter; `s3://` URI ile her yer konuşur
- `migrations/` — Alembic forward-only (downgrade `NotImplementedError`)

**Inference plane (Faz B'de ayrılır — şu an gateway'de):**
- `src/frontend/` → **`neeko-voice-frontend`** (Türkçe NFKC + cümle segmentasyonu + sayı/kısaltma/sembol açma + code-mix lexicon). 36 birim test. Hedef: 300+ golden test, Zemberek + espeak-ng + geminasyon yaması + style mode tag enjekte etme.
- `src/registry/` → filesystem manifest YAML CRUD + reference audio trim/resample to 16 kHz mono + RLock. A.6 cutover sonrası enroll dışında kullanılmıyor; voice catalog DB'de yaşıyor. Faz B.1'de `src/worker/` altına taşınır.
- `src/server/engine.py` → **`VoxCPM2Engine`** + `BaseSynthEngine` protocol + LRU LoRA cache (audit F1). Faz B.1'de `src/worker/` altına taşınır.
- `src/server/streaming.py` → sentence-chunked WAV header trick + yield generator. Faz B.4'te WebSocket eş güzergâhı eklenir.

**API gateway (Faz A.6 cutover):**
- `src/server/main.py` — FastAPI app, 12 endpoint (sync TTS, async jobs, voice CRUD, health, admin warmup)
- `src/server/auth.py` — DB-backed Bearer pipeline (parse → argon2id → tenant → scope → RL → audit)
- `src/server/queue.py` — Redis Streams XADD wrapper + `TtsJobPayload` + `parse_idempotency_key`
- `src/server/reference_resolver.py` — `file://` + bare path + `s3://` + `r2://` çözer
- `src/server/admin/` — operator JWT + Jinja2 + HTMX 1-sayfa CRUD
- `src/server/security/` — argon2id passwords + API key gen/parse + JWT HS256
- `src/server/rate_limit.py` — Redis Lua sliding window (per-key + per-tenant)

**Governance layer (Faz 3, henüz yok):**
- `src/governance/` — KVKK + AudioSeal watermark + voice fingerprint + sözleşme registry + takedown

**Henüz boş katmanlar:**
- `src/worker/` — Faz B.1: Redis Streams consumer (engine + frontend buraya taşınır)
- `src/bench/` — Faz 1: baseline karşılaştırma scriptleri
- `src/finetune/` — Faz 3: VoxCPM2 LoRA pipeline (`train_voxcpm_finetune.py` wrap)
- `src/eval/` — Faz 1: 5-katmanlı eval suite (UTMOSv2 + NISQA + Whisper-TR-WER + WavLM-SECS + TTSDS2)
- `src/g2p/` — Faz 1: Zemberek + espeak-ng + geminasyon yaması

## Repo-özel disiplinler

0. **Tenant = account/workspace, ürün değil** (Refactor R, 2026-05-24). Bir tenant kendi voice catalog'unu, kendi API key'lerini, kendi usage'ını yönetir. Ürün attribution `X-NQAI-App` header → `usage_records.app_label`. Voice'lar `owner_tenant_id` + `visibility` (`private/shared/public`) + `voice_access` ile paylaşılır. Single voice slug owner içinde unique (`erdal-dev/ayse` ve `niva-prod/ayse` ayrı voice). ElevenLabs/OpenAI mental model.
1. **Kaynaklı iddia.** Her sayı / her benchmark / her "X iyi" cümlesi link + tarih ister. "Bence iyi" yetmez: `MOS X / Elo Y / kaynak Z (link, tarih)` formatı. Memory: `feedback_evidence_over_convention`.
2. **Reproducibility.** Her deney = config + seed + commit hash + çıktı klasörü. Yeni notebook çıktısı yeni `experiments/` klasörüne iner — eskilerin üstüne yazılmaz.
3. **Eval kataloğu sabit.** Aynı test cümleleri ([data/test-sets/v0.1-mini.md](data/test-sets/v0.1-mini.md) bugün; Faz 1'de v1.0-full = 120 cümle), aynı metrikler. Modeller değişir, ölçü değişmez.
4. **Veri lisansı net.** `data/raw/` ve `data/reference-audio/` içine giren her dosya için kaynak + lisans + (varsa) voice talent kontrat ID'si manifest'te. ElevenLabs çıktısı **referans audio** modunda OK, **LoRA fine-tune training data olarak YASAK** (ToS gri alan).
5. **Premium = dar domain.** TR + karakter + call-center + child-directed kesişiminde ElevenLabs'ı geçmek; **genel TTS yarışına girmek yasak**. Ticari TTS API'lar (ElevenLabs/OpenAI/Google/Azure) "rakip" değil, sadece benchmark referansı.
6. **NEEKO değil NQAI ses omurgası.** Mimariyi multi-product baştan kur. Tek karaktere özel hardcode yok — `voice-adapter-registry`'de yeni karakter = yeni YAML, yeni kod değil.
7. **Birincil base model: VoxCPM2.** Adapter pattern (`BaseSynthEngine`) arkasında yaşıyor; alternatifler bench/eval üzerinden ölçülmeden swap yok.
8. **Multi-tenant zorunlu disiplinler** (scale-roadmap §6):
   - **D-04 audit log append-only** — `audit_log` tablosuna asla `UPDATE`/`DELETE` yok, sadece `INSERT`
   - **D-05 idempotency** — her async iş `request_id` ile idempotent; `IdempotencyRepo.reserve()` body hash ile gate'lenir
   - **D-08 tenant_id filter** — her cross-tenant query'de `WHERE tenant_id = ?` zorunlu; repo'lar constructor'da tenant_id alır, başka türlü instantiate edilmez
   - Cross-tenant erişim girişimi → **404** (existence-leak prevention), 403 değil
9. **Migrations forward-only.** Alembic `downgrade()` `NotImplementedError` raise eder. Schema değişikliği geri alınmaz; bozuk migration için yeni forward migration yazılır. Zero-downtime migration (`pgroll`) Faz D'de.

## Klasör yapısı

| Yol | Ne yapar |
| --- | --- |
| `docs/research/` | Araştırma brief'leri, dış çıktılar (01-*), damıtmalar (02-*) |
| `docs/decisions/` | Stratejik karar log'u (en yeni üstte) |
| `docs/architecture/` | `scale-roadmap.md` (canlı kanonik), `platform-v0.2.md` (referans), `data-model.md`, `auth-multi-tenant.md`, `streaming-protocol.md`, `observability.md`, `voxcpm2-integration.md` |
| `docs/audit/` | `faz-a-self-audit.md`, `faz-a-mlops-audit.md`, `checkpoint-2026-05-24-faz-a-exit.md` |
| `docs/character/` | NEEKO karakter spec'i, casting brief, voice talent outreach şablonları |
| `docs/legal/` | KVKK + FSEK + voice talent rider taslakları |
| `src/db/` | SQLAlchemy 2 async + Alembic, 7 ORM tablosu (tenants, api_keys, voices, usage_records, audit_log, operators, job_idempotency) |
| `src/repos/` | Repository pattern (tenant_id constructor-zorunlu); 7 repo |
| `src/storage/` | Cloudflare R2 (S3-compat) adapter, `S3URI` parser, cache |
| `src/frontend/` | Türkçe text frontend (NFKC + cümle segmentasyonu + sayı/kısaltma/sembol/code-mix) |
| `src/registry/` | Voice manifest YAML CRUD + reference audio I/O (16 kHz mono) — A.6 sonrası enroll dışı kullanılmıyor |
| `src/server/` | FastAPI app, **VoxCPM2 engine adapter**, auth, streaming, queue, admin UI, reference resolver |
| `src/worker/` | (boş) Faz B.1: Redis Streams consumer (engine + frontend buraya taşınır) |
| `src/g2p/` | (boş) Faz 1: Zemberek + espeak-ng + geminasyon yaması |
| `src/bench/` | (boş) Faz 1: baseline karşılaştırma scriptleri |
| `src/finetune/` | (boş) Faz 3: VoxCPM2 LoRA pipeline (`train_voxcpm_finetune.py` wrap) |
| `src/eval/` | (boş) Faz 1: UTMOSv2 / NISQA / Whisper-TR-WER / WavLM-SECS / TTSDS2 |
| `migrations/` | Alembic forward-only; ilk migration `2026_05_24_0001_*.py` (initial schema) |
| `configs/voices/` | Voice manifest YAML'ları (seed: `neeko-v01.yaml`) |
| `configs/seed_voices.yaml` | Bootstrap toplu enroll için 5-slot katalog (1 NEEKO + 2 NIVA + 2 NeuroCourse) |
| `scripts/` | `bootstrap_voices.py`, `smoke_test.py`, `seed_operator.py`, `migrate_filesystem_to_db.py` |
| `tests/` | 30 dosya, **384 test** — frontend + API smoke + auth + repos + admin + R2 + async jobs + LoRA cache + observability/dashboard + heartbeat + benches |
| `deploy/` | `gateway.Dockerfile`, `docker-compose.dev.yaml` (gateway + Postgres + Redis) |
| `alembic.ini` | Alembic CLI config (env'den DB URL okur) |
| `data/phonemes/` | Türkçe fonetik sözlük + kural seti + NEEKO lexicon overrides |
| `data/test-sets/` | Kürate test cümleleri (versiyonlu) |
| `data/casting-prompts/` | Voice talent audition prompt pack'leri |
| `data/reference-audio/` | Onaylı referans sesler + MANIFEST (NEEKO v0.1 köprü sesi) |
| `data/raw/` | gitignore'lu — voice talent ham kayıtları + MANIFEST.md |
| `notebooks/` | Colab/Kaggle defterleri (`01-voxcpm2-tr-demo`, `03-platform-server-colab`) |
| `experiments/` | Her deney: `YYYY-MM-DD-<slug>/{config.yaml, log.md, output/}` |

## Notebook'ları çalıştırma

VoxCPM2 ~8 GB VRAM ister — lokal makine yetmez, **Colab T4/A100** veya **Kaggle T4 x2** üzerinde koşturulur. Talimatlar her notebook'un başında ve [notebooks/README.md](notebooks/README.md)'de:

1. Colab'da Runtime → Change runtime type → **GPU (T4 veya A100)**
2. `01-voxcpm2-tr-demo.ipynb` (baseline) veya `03-platform-server-colab.ipynb` (full server) aç
3. Cell 1 (install) → kernel restart → cell 3+
4. `03` notebook'unda ~6-8 dakika sonra `https://*.trycloudflare.com` public URL alırsın

Tipik hatalar: HF rate limit (5 dk bekle), CUDA OOM (A100'e geç), UTF-8 bozulması (Türkçe karakterler).

## Hassas veri ve sınırlar

- `data/raw/` gitignore'lu — voice talent ham kayıtları **commit edilmez**
- Voice talent kontratları + IP belgesi → `NeuroQubit_NEEKO/private/` (bu repo dışı, üst-katmanda)
- API anahtarları (HuggingFace, RunPod, Lambda Labs, WandB) → `.env`, asla commit edilmez
- Üst-katman karar log'unu burada tekrar tutma — TTS dışı kararlar `NeuroQubit_NEEKO/admin/decision-log.md`'de
- Henüz kaynaklanmamış model/metrik için kod yazma — önce decision satırı, sonra config, sonra kod
- **VoxCPM2 yasak kullanımlar** (OpenBMB ToS): kişi taklidi (impersonation), dolandırıcılık, dezenformasyon. AI label zorunluluğu jurisdiction'a göre.

## Üst katmanla ilişki

`/home/alfonso/neeko-firmware/` workspace'inde `neeko_server/` (backend), `NeuroQubit_NEEKO/` (iş), `neeko-design-framework/` (tasarım) ile aynı seviyede bağımsız repo. NEEKO + NIVA + NeuroCourse + NARO dört üründe ortak ses omurgası bu repodan akar; backend entegrasyonu `neeko_server/`'da yapılır, kontrat/finans/strateji `NeuroQubit_NEEKO/`'da tutulur.
