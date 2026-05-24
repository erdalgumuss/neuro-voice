# Checkpoint — Faz A çıkışı, Faz B girişi

**Tarih:** 2026-05-24 · **Commit:** `ba6be69` · **Suite:** 182/182 · **Lint:** clean

Bu doc bir **karar checkpoint**'idir. Faz A bitti, Faz B'nin gateway-tarafı iskeleti kondu, ama Faz B'nin asıl ağırlığı (GPU worker süreci, WebSocket, at-least-once chunk semantikleri) henüz yazılmadı. Aşağıdaki satırlar repo'da neyin **bittiğini**, neyin **yarı durduğunu**, ve worker yazımına geçmeden önce hangi 5 işin yapılması gerektiğini netleştirir.

> Önceki audit'ler: [`faz-a-self-audit.md`](faz-a-self-audit.md) (lint + dosya), [`faz-a-mlops-audit.md`](faz-a-mlops-audit.md) (mimari + karşılaştırma).
> Canlı kanonik mimari: [`../architecture/scale-roadmap.md`](../architecture/scale-roadmap.md).

---

## 1. Bir cümle ile

Tek-process VoxCPM2 prototipinden 4-tenant × 5-concurrent omurgaya geçişin **veri planı + auth + storage + idempotency** kısmı çalışır durumda; **inference planı henüz gateway-içi**, worker süreci ve WebSocket eksik. Üretime gitmenin önündeki en büyük tek blok worker'dır.

---

## 2. Uçtan uca ne yaptık (7 commit özeti)

| # | Commit | Konu | Sonuç |
|---|---|---|---|
| 1 | `0272917` | **Faz A.1** Postgres veri planı | SQLAlchemy 2 async + asyncpg + Alembic forward-only; 7 ORM tablosu (`tenants`, `api_keys`, `voices`, `usage_records`, `audit_log`, `operators`, `job_idempotency`); aiosqlite test path; PRAGMA FK on |
| 2 | `d41e19c` | **Faz A.2-A.3** repos + security primitives | 7 repository (her biri tenant_id constructor zorunlu — D-08); argon2id (m=64MiB, t=3, p=4); JWT HS256 access/refresh; Redis Lua sliding window |
| 3 | `8412bd1` | **Faz A.3.c** DB-backed Bearer auth | `authenticate_bearer()` pipeline: prefix lookup → argon2id verify → tenant status → scope → rate limit → audit; `require_auth(*scopes)` FastAPI dependency factory |
| 4 | `49613d4` | **Faz A.4-A.6** admin UI + docker-compose + filesystem→DB migration | FastAPI + Jinja2 + HTMX 1-sayfa CRUD; `docker-compose.dev.yaml` (gateway + Postgres + Redis); `scripts/migrate_filesystem_to_db.py` |
| 5 | `4b4f0a8` | **Faz A audit pass 1** | 7 kritik + 5 polish fix (`__import__` removal, magic numbers, package_data, ruff config) |
| 6 | `30e93f3` | **Faz A audit pass 2** (MLOps) | 5 kritik karar tespiti: LoRA cache, GPU provider, async API, R2, AudioSeal — somut $/saat tablosu + web kaynaklı karşılaştırma |
| 7 | `a160f12` | **F1 fix** LoRA cache LRU eviction | `OrderedDict` + `_evict_oldest_locked()` + `torch.cuda.empty_cache()`; `NQAI_LORA_CACHE_SIZE` env (default 3); audit F1 kapandı |
| 8 | `6b19ab7` | **A.6 cutover** TTS endpoints → DB auth | `/v1/tts*` + `/v1/voices*` artık `require_auth("tts:*"/"voice:*")` üzerinden; legacy `auth_legacy.py` silindi; VoiceView duck-typed adapter; cross-tenant 404 (existence-leak prevention) |
| 9 | `ce5ed35` | **R2 storage** Cloudflare R2 (S3-compat) | `S3URI` parser; `R2Storage` (upload/download/exists/presigned/delete); `download_to_cache()` sha256-keyed cache; `reference_resolver` (file:// + bare path + s3:// + r2://); moto[s3] test path |
| 10 | `ba6be69` | **Async TTS jobs** | `POST /v1/tts/jobs` (Stripe `Idempotency-Key` zorunlu) + `GET /v1/tts/jobs/{id}` polling; `TtsJobQueue` Redis Streams wrapper; backpressure (XLEN > limit → 503); 15 yeni test (15/15 yeşil) |

**Toplam:** 182 test geçer. Repo bir multi-tenant TTS platform iskeleti — sadece GPU work hâlâ gateway içinde.

---

## 3. Faz A — bitti mi? **Evet, tamamen.**

Scale-roadmap §10'daki 8 adımın hepsi karşılandı:

| Adım | Spec | Durum | Kanıt |
|---|---|---|---|
| **A.1** | Postgres + Alembic + SQLAlchemy 2 async | ✅ | `migrations/versions/2026_05_24_0001_*.py`, `tests/test_db_models.py` 9 test |
| **A.2** | Repository pattern (tenant_id zorunlu) | ✅ | `src/repos/*.py` 7 dosya, `tests/test_repos.py` 16 test |
| **A.3** | Auth refactor DB-backed | ✅ | `src/server/auth.py` `authenticate_bearer()`, `tests/test_auth_flow.py` 11 test |
| **A.4** | `migrate_filesystem_to_db.py` | ✅ | idempotent + dry-run, NEEKO voice taşınır |
| **A.5** | Admin endpoints | ✅ | `src/server/admin/router.py` 12 test |
| **A.6** | Admin UI + cutover | ✅ | `tests/test_admin_flow.py` + TTS endpoint'leri DB auth'lu |
| **A.7** | `docker-compose.dev.yaml` | ✅ | gateway + postgres + redis profile |
| **A.8** | R2 storage adapter | ✅ | `src/storage/r2.py` + `tests/test_r2_storage.py` (moto) + reference resolver |

**Checkpoint A doğrulaması** (scale-roadmap §10):
> "Admin UI'dan tenant + key generate edilir, her key kendi tenant'ının voice'larını listeler, başkasını göremez. NEEKO referans audio R2'ye taşınabilir, DB'de URI + sha256 tutulur. TTS endpoint hâlâ tek-process."

→ **Karşılandı.** Cross-tenant test (`test_repos.py`, `test_async_jobs.py::test_status_of_another_tenants_job_returns_404`) yeşil. R2 upload/presign moto ile yeşil. TTS endpoint tek-process — bu zaten Faz A sınırı.

---

## 4. Faz B — nereye kadar geldik?

Scale-roadmap §11'deki 8 adımdan **3'ü tamamen, 1'i kısmen yapıldı**; 4'ü açık.

| Adım | Spec | Durum | Yapılan / eksik |
|---|---|---|---|
| **B.1** | `src/worker/` paketi — Redis Streams consumer + VoxCPM2 engine | ❌ **açık** | Klasör yok. Engine + frontend hâlâ gateway'de yaşıyor |
| **B.2** | Job schema (msgspec.Struct) | 🟡 **kısmi** | Gateway tarafı `TtsJobPayload` dataclass (`src/server/queue.py`); JSON wire format; worker tarafı yok |
| **B.3** | Gateway `POST /v1/tts` → XADD + result subscribe | 🟡 **kısmi** | `POST /v1/tts/jobs` async path açıldı; **sync `POST /v1/tts` hâlâ in-process** çağırıyor (geri-uyumluluk için bilinçli) |
| **B.4** | WebSocket endpoint `/v1/tts/ws` | ❌ **açık** | Hiç yazılmadı |
| **B.5** | Idempotency (D-05) | ✅ **bitti** | Stripe `Idempotency-Key` header; `IdempotencyRepo` reserve/complete/fail; cache 24 h `expires_at`; replay testi yeşil |
| **B.6** | Backpressure (D-14) | ✅ **bitti** | `XLEN > NQAI_QUEUE_DEPTH_LIMIT` → 503 + `Retry-After`; varsayılan 200; testi yeşil |
| **B.7** | At-least-once (D-06) | ❌ **açık** | Worker olmadığı için XACK / XAUTOCLAIM yok |
| **B.8** | `docker-compose` + worker (GPU passthrough) | ❌ **açık** | Worker servisi compose'da yok |

**Checkpoint B doğrulaması** (scale-roadmap §11):
> "Gateway + 1 worker. 5 concurrent request kuyrukta sıralanır, sıralı işlenir. Worker SIGKILL → başka worker devraldı. Idempotency duplicate request'i yakaladı."

→ **Sadece son madde gerçek**, ilk üç madde için worker süreci gerekli. Faz B'nin asıl gövdesi B.1 + B.3 + B.4 + B.7'dir; geri kalanı tamamlanmış.

---

## 5. Yarı-durumlar (bilinçli, ama belgelenmesi şart)

Pazartesi ekibe açılırken sürpriz olmaması için:

1. **İki TTS hattı paralel:**
   - `POST /v1/tts` (sync) — hâlâ in-process VoxCPM2 çağırıyor. Smoke + demo + Colab için korundu.
   - `POST /v1/tts/jobs` (async) — XADD ediyor ama **tüketici yok**, dolayısıyla iş `queued` durumunda kalır.
   - Bu kasıtlı: worker yazılana kadar sync path canlı, async path test'lerle doğrulanmış olarak duruyor.

2. **R2 opsiyonel sayılır:**
   - `voices.reference_uri` bir `file://` path veya bir `s3://` URI olabilir. `reference_resolver` ikisini de çözer.
   - Production'da hepsi R2'ye taşınır; dev'de filesystem yeterli.
   - `NQAI_R2_ACCOUNT_ID` + `NQAI_R2_BUCKET` env yoksa R2 bağlamı initialize edilmez (lazy singleton).

3. **Worker tarafı schema henüz yok:**
   - `TtsJobPayload.decode()` yazıldı ama yalnızca test edildi — worker `XREADGROUP` döngüsünde nasıl tüketileceği `src/worker/main.py` ile yazılır.
   - `TtsResult` (chunk yapısı) henüz tanımlı değil — B.2'nin ikinci yarısı.

4. **Observability nominal:**
   - `audit_log` ve `usage_records` yazılıyor (D-04 OK), ama Prometheus exporter + structured logging Faz C.
   - Şu an stdlib `logging` + `info` seviyesi.

5. **Lifespan shutdown drain yok:**
   - SIGTERM gelirse uvicorn anında kapanır; in-flight request düşer.
   - 20-user scale'de tolere edilebilir; Faz C'nin runbook'unda graceful drain eklenmeli.

---

## 6. Faz B asıl gövdesine geçmeden önce — 5 iş

Worker yazımına başlamadan **mutlaka** kapatılması gereken 5 madde. Bunlar olmadan worker yazılırsa ya re-yazım gerekir ya da production'da kırılır.

### Ö1 · Worker paket iskeleti karar + spec [~1 saat]

`src/worker/` klasörünün dosya planı, hangi modüllerden ne import edileceği, engine ve frontend'in nasıl taşınacağı (kopya değil hareket) tek bir mimari karar satırı + 1-sayfa spec doc. Gateway'in `engine.py` + `streaming.py` + `frontend/` import'larını worker'a aktarmak gateway'i sadeleştirir ama testleri kırar — taşıma planı önce yazılmalı.

**Çıktı:**
- `docs/architecture/worker-process.md` (B.1-B.4 + B.7 referansı — kanonik)
- `docs/decisions/README.md`'ye 1 karar satırı: "Gateway-worker ayrımı: engine ve frontend src/worker/ altına taşınır; gateway sadece I/O kalır"

### Ö2 · `TtsResult` chunk schema + Redis result channel [~1 saat]

Worker → gateway sonuç akışı için tek `TtsResult` schema'sı:

```python
@dataclass(frozen=True)
class TtsResult:
    request_id: str
    seq: int                # 0-indexed chunk numarası
    pcm_bytes: bytes        # int16 PCM
    sentence_text: str | None
    final: bool             # son chunk'ta True
    error: str | None = None
```

Wire format: per-request Redis Stream `nqai.tts.results.{request_id}` veya tek shared stream + consumer-side filtering. Scale-roadmap §3 diyagramı per-request stream öneriyor; karar netleştirilmeli.

**Çıktı:**
- `src/server/queue.py`'a `TtsResult` + encode/decode (gateway worker'dan önce schema sahibi)
- 1 unit test (schema round-trip)

### Ö3 · Sync `POST /v1/tts` deprecation politikası [~30 dk]

Üç seçenek:
- **A)** Sync path silinir → tüm istemciler async/WS'e zorlanır (Colab notebook + smoke_test güncellenir).
- **B)** Sync path internal proxy haline gelir → arkada XADD + bekle + bütün audio'yu döndür (worker varsa).
- **C)** Sync path korunur ama `Deprecation` header ve metrik eklenir → Faz C'de silinir.

Cevap karar log'una. Mevcut tüm dökümanlar (README, CLAUDE.md, notebook) sync path'i tanıtıyor → körlük yaratmadan değiştirmek için karar şart.

### Ö4 · IdempotencyRepo `body_hash` enforcement [~1 saat]

Mevcut `IdempotencyRepo.reserve()` aynı `request_id` ikinci kez geldiğinde **body hash'ini kontrol etmiyor** — Stripe semantiği aynı key + farklı body = 409 Conflict ister. Şu an silent replay dönüyor. Async jobs PR'ında `_hash_job_body()` helper yazıldı ama `reserve()`'e bağlı değil.

**Çıktı:**
- `IdempotencyRepo.reserve(request_id, body_hash=...)` → hash uyuşmazlığında `IdempotencyConflict` exception
- `POST /v1/tts/jobs` yakalayıp 409 döndürür
- 1 test: aynı key + farklı text → 409

Worker yazılmadan önce bu kapanmalı, çünkü worker'ın body hash'i validate etmesi gerekecek (XREADGROUP'tan gelen iş gerçekten reserve edilenle aynı mı?).

### Ö5 · `docker-compose.dev.yaml` worker yeri [~30 dk]

Worker servisinin compose'da nasıl tanımlanacağı — image, GPU passthrough (`runtime: nvidia` veya `--gpus all`), env'ler, healthcheck, queue env değişkeni — sadece **iskelet** olarak konabilir (içi boş image). Bu B.1'in fiziksel çerçevesi; dosya yapısı netleşince B.1 kod yazımı bu profile'a düşer.

**Çıktı:**
- `docker-compose.dev.yaml`'a `worker:` service stub (commented out for CPU-only dev)
- README'ye not: "worker servisi default `--profile gpu` ile gelir"

---

## 7. Faz B asıl gövdesi (Ö1-Ö5 sonrası)

| Adım | İş | Süre tahmini | Bağımlı olduğu Ö |
|---|---|---|---|
| **B.1** | `src/worker/main.py` — Redis Streams consumer (`XREADGROUP` döngüsü), engine + frontend taşıma | ~3 sa | Ö1 + Ö2 |
| **B.3** | Gateway sync path kararı uygulanır (Ö3'ün seçimine göre A/B/C) | ~1-2 sa | Ö3 |
| **B.4** | WebSocket endpoint `/v1/tts/ws` — Starlette WS handler, scale-roadmap §7.1 protocol | ~2 sa | Ö2 |
| **B.7** | XACK sonra-chunk semantiği + `XAUTOCLAIM` retry + DLQ stream | ~2 sa | B.1 |
| **B.8** | docker-compose worker servisi gerçekten ayağa kalkar, GPU passthrough doğrulanır | ~1 sa | Ö5 + B.1 |

**Toplam Faz B asıl gövdesi:** ~9-10 saat çalışma, 1.5-2 gün.

**Checkpoint B çıkış kriteri** (revize):
> "Gateway + 1 worker (CPU mock veya GPU). 5 concurrent async job kuyrukta sıralanır, worker tek tek tüketir, `GET /v1/tts/jobs/{id}` `complete` döner. Worker SIGKILL → XAUTOCLAIM başka worker'a devreder (chaos test). WebSocket istemci ilk chunk'ı < 2 s alır."

---

## 8. Faz C'ye ne kalır (Ö1-Ö5 + B.1-B.8 sonrası)

Hatırlatma — scale-roadmap §12:

- **C.1** Worker × 4 deploy (RunPod L4 spot pool)
- **C.2-C.5** Observability (Prometheus + Grafana + Loki + OTel)
- **C.6** Alertmanager → Slack
- **C.7** Health endpoints `/health/live` + `/health/ready` (şu an tek `/health` var)
- **C.8** k6 load test 20 vu
- **C.9** Nano-vLLM swap (opsiyonel, RTF iyileştirme bağlı)

Pazartesi-Salı asıl Faz B gövdesi → Çarşamba'dan itibaren Faz C başlar. **Faz C'de Faz A/B'nin oturmuş olduğu varsayılır** — observability eklenmesi yapısal değişiklik değildir, sadece instrumentation.

---

## 9. Bilinen riskler (Faz B'ye taşınanlar)

| # | Risk | Etki | Azaltma |
|---|---|---|---|
| **R-B1** | VoxCPM2 cold-load worker'da ~30-60 s (RunPod) — ilk job'lar timeout riski | İlk dakikada UX bozuk | `/admin/warmup` worker'da da olur, worker boot script ile eager load |
| **R-B2** | Per-request Redis Stream sayısı (`nqai.tts.results.{rid}`) → tek istek başına 1 stream yaratmak Redis memory'sini şişirir | Bellek baskısı | Stream TTL veya tek shared stream + filtering — Ö2'de karar |
| **R-B3** | Worker → gateway result push 5 ms hedef ama Redis round-trip her chunk için (5-10 chunk/istek) | TTFB +25-50 ms | Pub/Sub vs XADD tradeoff — Ö2'de yan karar |
| **R-B4** | Worker crash + XAUTOCLAIM 30 sn'de devralma → kullanıcı 30 sn pencere boyunca chunk almaz | UX dalgalanması | XAUTOCLAIM interval 5 sn'ye çekilir, retry'da resume seq# semantiği — B.7 |
| **R-B5** | Sync path silinince Colab notebook + smoke_test bozulur, ekipte kullanım var | Demo bozulması | Ö3 kararı sonra notebook + smoke güncellenir aynı PR'da |

---

## 10. Karar gereken 3 satır (decision log için)

Bunlar yazılmadan Faz B gövdesi spagettiye dönüşür. Her biri bir sonraki PR'da kod olur:

1. **Worker süreç ayrımı (B.1 kararı):** Engine + frontend `src/worker/` altına **taşınır** (kopyalanmaz); gateway pure I/O kalır. Geri-uyumluluk için sync `POST /v1/tts` deprecation döngüsüne girer (Ö3'ün cevabıyla netleşir).
2. **Result channel mimarisi (Ö2 kararı):** per-request stream (`nqai.tts.results.{rid}`) vs shared stream + filter — Redis memory + latency tradeoff'u net bir karar satırına bağlanır.
3. **Sync TTS deprecation politikası (Ö3 kararı):** A/B/C seçimi — silmek mi, proxy yapmak mı, header ile uyarmak mı?

Bu üç satır karar log'una girmeden Faz B kod PR'larına başlamamalı.

---

## 11. Şu anki taban sayılar (referans için)

| Boyut | Değer | Kaynak |
|---|---|---|
| Test sayısı | **182** | `python -m pytest` |
| Test süresi | ~50 s | aynı |
| Ruff hata | **0** | `ruff check src tests` |
| Endpoint sayısı (HTTP) | 12 | `grep '^@app\.' src/server/main.py` |
| ORM tablosu | 7 | `src/db/models.py` |
| Repository | 7 | `src/repos/` |
| Voice kataloğu yolu | filesystem + R2 (hybrid) | `reference_resolver.py` |
| Auth pipeline | DB argon2id + scope + RL + audit | `src/server/auth.py` |
| Idempotency cache TTL | 24 h | `IdempotencyRepo` default |
| Backpressure eşiği | `NQAI_QUEUE_DEPTH_LIMIT=200` | `src/server/main.py` |
| LoRA cache boyutu | `NQAI_LORA_CACHE_SIZE=3` | `src/server/engine.py` |
| Single-process throughput | ~0.36 req/s (RTF 2.77 Colab) | `faz-a-mlops-audit.md` §3 |

---

## 12. Tek söz

Faz A'yı **+4 köprü PR** ile bitirdik (F1 + A.6 + R2 + async jobs); Faz B'nin gateway-tarafı %40 hazır, ama asıl ağırlık (worker süreci, WebSocket, at-least-once) önümüzde. Worker yazımına geçmeden önce §6'daki 5 işin ve §10'daki 3 karar satırının kapanması gerek — yoksa B.1 yazıldıktan sonra geri dönüş maliyetli olur.
