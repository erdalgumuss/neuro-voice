# neuro-voice

NQAI'nin Türkçe + voice-cloning + streaming TTS yığını. **VoxCPM2** (Apache 2.0, OpenBMB, 2B param) üzerine multi-tenant API gateway + Türkçe text frontend + Stripe-style async job queue. Endüstri-standardı tek-yönlü streaming TTS API'sı (ElevenLabs / OpenAI Audio / Cartesia uyumlu); duplex voice-agent (NIVA) ürünleri ayrı bir transport ile gelir.

## Şu an (2026-05-25 — Faz C v1 audit hotfix turu)

**Gateway/worker süreç ayrımı tamam, streaming bridge canlı.** Gateway CPU/I/O katmanı (Hetzner CX22 sığar); worker GPU node'unda `python -m worker.main` ile koşar. Worker pipeline frame-by-frame publish ediyor (drain-then-emit pattern öldü); ilk cümle üretilir üretilmez gateway result stream'den çekip `/v1/tts/stream` chunked HTTP üzerinden client'a iletiyor. Sync `/v1/tts` aynı Redis queue üzerinden geriye-uyumlu proxy (RFC 8594 `Deprecation`/`Sunset` header, 2026-09-01 sunset). At-least-once delivery XAUTOCLAIM + bounded retry/DLQ ile korunur.

- Checkpoint + Faz B yol haritası: [docs/audit/checkpoint-2026-05-24-faz-a-exit.md](docs/audit/checkpoint-2026-05-24-faz-a-exit.md)
- Kanonik mimari (v1.0 hedefi): [docs/architecture/scale-roadmap.md](docs/architecture/scale-roadmap.md)
- Karar log'u: [docs/decisions/README.md](docs/decisions/README.md)
- VoxCPM2 entegrasyon detayları: [docs/architecture/voxcpm2-integration.md](docs/architecture/voxcpm2-integration.md)
- Mimari index: [docs/architecture/README.md](docs/architecture/README.md)

**Şu an çalışan:** 4 tenant × N API key, DB-backed Bearer auth (argon2id), R2 voice catalog + artifact storage, sync `POST /v1/tts` (queue proxy, `Deprecation: true`), `POST /v1/tts/stream` (chunked WAV, frame-by-frame bridge → first byte cümle üretildikçe gateway'e iletilir), async `POST /v1/tts/jobs` (Stripe Idempotency-Key + worker uçtan uca tamamlar + presigned R2 URL), admin UI (FastAPI + Jinja2 + HTMX), worker süreci (`python -m worker.main`) — gerçek consumer, R2 archive, periyodik XAUTOCLAIM, SIGTERM graceful drain.

**B.1.5 yön:** Endüstri-standardı tek-yönlü streaming TTS API. Worker pipeline'ı `iter_engine_chunks` thread→asyncio queue bridge'i ile drain-then-emit pattern'inden çıktı; engine cümle yield ettikçe gateway result stream'e XADD ediyor, `/v1/tts/stream` chunk'ı client'a aktarıyor. Duplex voice-agent (WebRTC) bilinçli olarak scope dışı — NIVA gibi 2-yönlü ürünler ayrı transport ile gelir.

## Hedef

12 ay içinde **Türkçe + 3-7 yaş çocuk konuşması + sürdürülebilir karakter sesi** alt-domain'inde premium kalite. VoxCPM2 base + Türkçe SFT + per-character LoRA + production-grade streaming. Genel TTS yarışına girmiyoruz; dar niche'te derinleşiyoruz. 4 tenant × 5 concurrent başlangıç, yatay ölçek ile 200 user.

## Quickstart — Docker Compose (önerilen)

```bash
git clone git@github.com:erdalgumuss/neuro-voice.git
cd neuro-voice

cp .env.example .env
# .env içindeki minimum set:
#   NQAI_JWT_SECRET=<min 32 char random>
#   NQAI_REQUIRE_AUTH=true

docker compose -f docker-compose.dev.yaml up -d
docker compose -f docker-compose.dev.yaml exec gateway alembic upgrade head
docker compose -f docker-compose.dev.yaml exec gateway \
    python scripts/seed_operator.py --email <your-email>

open http://localhost:8000/admin/         # operator login + tenant/key CRUD
```

Stack: gateway (FastAPI + admin UI) + Postgres 16 + Redis 7. GPU worker `docker compose -f docker-compose.dev.yaml --profile gpu up worker` ile başlar (NVIDIA Container Toolkit gerekli); CPU dev için worker'ı yerel `python -m worker.main` ile boot edebilirsiniz.

## Quickstart — Colab (GPU'lu uçtan uca)

[notebooks/03-platform-server-colab.ipynb](notebooks/03-platform-server-colab.ipynb)'yi Colab'da aç → T4 / A100 GPU → cell 1 → kernel restart → cell 3-9. ~6-8 dakika sonra `https://*.trycloudflare.com` URL'i ve iki API key.

Tek tıklama: <https://colab.research.google.com/github/erdalgumuss/neuro-voice/blob/main/notebooks/03-platform-server-colab.ipynb>

> **Not:** Notebook hâlâ v0.2 era flow'unu (legacy env-list auth + filesystem catalog) gösterir. A.6 cutover sonrası DB-backed auth ile uyumlu güncelleme Faz B'nin Ö3 kararından sonra yapılacak.

## Quickstart — bare metal (GPU varsa, ~8 GB VRAM)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# DB + Redis ayağa kalkmalı (docker compose veya manuel)
export NQAI_DATABASE_URL=postgresql+asyncpg://...
export NQAI_REDIS_URL=redis://localhost:6379
export NQAI_JWT_SECRET=<random>

alembic upgrade head
python scripts/seed_operator.py --email <your-email>

PYTHONPATH=src python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

İlk request model'i yükler (T4 ~90-120 s, A100 ~45-60 s). Daha temiz: `POST /admin/warmup`.

## API yüzeyi

### Tenant API (Bearer `Authorization: Bearer nqai_<prefix>_<secret>`)

> **Mental model:** "tenant" = workspace/account (`erdal-dev`, `neeko-prod`, `partner-x`). API key tenant'a ait. Voice'lar tenant'a ait ama `visibility=public` veya `voice_access` grant ile cross-tenant paylaşılabilir. Ürün attribution `X-NQAI-App` header ile yapılır (`neeko-mobile`, `niva-agent` vb.) — `usage_records.app_label`'a düşer, billing tenant'a kalır.

| Method | Yol | Scope | Ne yapar |
|---|---|---|---|
| GET | `/health` | — | model yüklendi mi, voice sayısı, sürüm |
| POST | `/admin/warmup` | — | VoxCPM2 ağırlıklarını eager yükle (auth zorunlu) |
| GET | `/v1/voices` | `voice:read` | bu workspace'in erişebildiği catalog (owned + public + shared) |
| GET | `/v1/voices/{id}` | `voice:read` | tek voice manifest (accessible olmalı) |
| POST | `/v1/voices` | `voice:write` | yeni voice enroll (multipart: `reference_audio` + form) — owner = bu tenant, default `visibility=private` |
| DELETE | `/v1/voices/{id}` | `voice:write` | voice sil (sadece owner; non-owner için 404) |
| POST | `/v1/tts` | `tts:write` | sync 48 kHz WAV / PCM16 (tek-process) |
| POST | `/v1/tts/stream` | `tts:write` | sentence-chunked streaming WAV / PCM16 |
| POST | `/v1/tts/jobs` | `tts:write` | **async** — `Idempotency-Key` header (UUID) zorunlu, 202 + `job_id` |
| GET | `/v1/tts/jobs/{job_id}` | `tts:read` | job durumu (queued / running / complete / failed) + presigned audio URL |

**Opsiyonel request header'ları (tüm `/v1/*` endpoint'lerinde):**

| Header | Etki |
|---|---|
| `X-NQAI-App: <slug>` | Ürün/app attribution — `usage_records.app_label`'a yazılır (max 64 char). Örn: `neeko-mobile`, `niva-agent-prod` |
| `X-Request-Id: <uuid>` | Trace correlation; yoksa gateway UUID atar |
| `Idempotency-Key: <uuid>` | `/v1/tts/jobs` için zorunlu (Stripe pattern) — aynı key + farklı body → 409 |

### Operator API (JWT cookie, ayrı login)

| Method | Yol | Ne yapar |
|---|---|---|
| GET | `/admin/` | tenant list + API key CRUD UI |
| POST | `/admin/login` | operator login → JWT cookie |
| POST | `/admin/tenants` | yeni tenant |
| POST | `/admin/tenants/{id}/keys` | yeni API key (secret tek seferlik gösterilir) |
| DELETE | `/admin/tenants/{id}/keys/{kid}` | revoke |
| GET | `/admin/usage` | son 30 g usage |

OpenAPI: server ayaktayken `GET /docs` (Swagger UI) ve `GET /openapi.json`.

### Async job örneği

```bash
KEY="nqai-prod-..."
URL="http://localhost:8000"
RID=$(python -c 'import uuid; print(uuid.uuid4())')

# 1. Submit (X-NQAI-App opsiyonel ama tavsiye edilir)
curl -X POST $URL/v1/tts/jobs \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: $RID" \
  -H "X-NQAI-App: neeko-mobile" \
  -H "Content-Type: application/json" \
  -d '{"text":"Merhaba, ben Neeko.","voice_id":"neeko-v01"}'
# → 202 {"job_id":"<RID>","status":"queued",...}

# 2. Poll (worker yok hâlâ — `queued` döner)
curl -H "Authorization: Bearer $KEY" $URL/v1/tts/jobs/$RID
```

### Sync örnek (Faz B.3 deprecation döngüsüne girecek)

```bash
curl -X POST $URL/v1/tts \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Bir varmış, bir yokmuş.","voice_id":"neeko-v01"}' \
  --output out.wav
```

## Testler

```bash
python -m pytest                    # 384 test (~55 s)
python -m pytest tests/test_async_jobs.py -v
python -m pytest tests/test_repos.py::test_cross_tenant_isolation -v
ruff check src tests                # lint
```

API smoke + async + repo + auth + R2 testleri VoxCPM2'yi stub'larla; torch dışında ağır bağımlılık istemez. Tam end-to-end için Colab notebook veya GPU'lu lokal.

## Repo disiplini

Üst-katman [`/home/alfonso/neeko-firmware/CLAUDE.md`](../CLAUDE.md) 7 disiplin kuralı + bu repo'nun [CLAUDE.md](CLAUDE.md)'sindeki ek 7 disiplin geçerli. Özet:

- Önce karar (decision log satırı), sonra kod
- Her sayı / her benchmark / her "X iyi" cümlesi link + tarih
- Eval kataloğu sabit, modeller değişir
- Premium = dar domain (TR + karakter + child-directed), genel TTS yarışı yasak
- NEEKO değil **NQAI ses omurgası** (4 ürün ortak altyapı)
- **Birincil base model: VoxCPM2** (Apache 2.0); engine adapter pattern arkasında
- Forward-only Alembic; tenant_id filter mandatory (D-08); audit log append-only (D-04); idempotency required (D-05)

## Üst katmanla ilişki

Bu repo `/home/alfonso/neeko-firmware/` workspace'inde bağımsız git deposu. `neeko_server/` (backend), `NeuroQubit_NEEKO/` (iş), `neeko-design-framework/` (tasarım) ile aynı seviyede. NEEKO + NIVA + NeuroCourse + NARO dört üründe ortak ses omurgası buradan akar.
