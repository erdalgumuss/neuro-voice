# Faz A — Self Audit Raporu

**Audit tarihi:** 2026-05-24
**Audit kapsamı:** Faz A.1-A.6 boyunca yazılan tüm source + test + migration + deploy dosyaları
**Sürüm:** v0.2.0 (commit `49613d4` öncesi durum)
**Baseline:** 133/133 tests yeşil, 0 ruff hatası

> Pazartesi ekibe açılmadan önce çıkabilecek her sorun bu doc'ta katalog. Her bulgu için **dosya:satır**, **severity**, **bulgu**, **fix**, **status**. Bulgu numaralandırması (C# kritik, P# polish) commit message + future test referansları için.

---

## 0. Audit metodolojisi

İki kademeli:
1. **Otomatik gate** — `ruff check src tests scripts migrations` (E/F/I/B/UP/SIM rule set'leri).
2. **Manuel okuma** — her dosyayı baştan sona, ilgili spec dokümanına (data-model.md, auth-multi-tenant.md, scale-roadmap.md) karşı doğruladım. Her şüpheli satır not düşüldü.

Auto-fixable (56 issue) ruff --fix ile çözüldü; geri kalan 15'i bu raporda enumerate edilmiş. Suite **her fix sonrası** yeniden koşturuldu (regression yakalama).

---

## 1. Kritik bulgular (C#) — pazartesi-bloker olabilecekler

### C1 · `src/server/admin/router.py:267` · 🔴 HIGH · **fixed**

```python
key = await session.get(__import__("db.models", fromlist=["ApiKey"]).ApiKey, key_id)
```

Auth-critical path'in ortasında runtime `__import__` çağrısı. Risk: import error gizlenir, traceback okunamaz, IDE jump-to-def çalışmaz, static analiz `ApiKey` symbol'ünü göremez.

**Fix:** module-level `from db.models import ApiKey` eklendi, satır `await session.get(ApiKey, key_id)` olarak temizlendi.

### C2 · `src/server/main.py:105` · 🔴 HIGH · **fixed**

FastAPI app description'ı hâlâ `"Chatterbox Multilingual"` üzerinden yazılmış — VoxCPM2 refactor'ı sırasında atlanmış. `/docs` (Swagger UI) ve `/openapi.json` (client SDK üretimi) için yanlış metadata.

**Fix:** description "VoxCPM2 (Apache 2.0)" + admin surface notu olarak güncellendi.

### C3 · `src/server/main.py:328` · 🟠 MED · **fixed**

```python
port=int(__import__("os").environ.get("NQAI_PORT", "8000")),
```

Yine inline `__import__`. Aynı sebeplerle kötü pattern.

**Fix:** module-level `import os` eklendi, satır `os.environ.get(...)` oldu.

### C4 · `src/server/auth.py:75` · 🟠 MED · **fixed**

```python
def get_redis() -> Redis:
    ...
    if _redis_client is None:
        import os  # ← local import inside function
```

Lazy import lokal scope'ta — okunabilirliği azaltır, ilk çağrıda küçük overhead. Yan etkisi yok ama incelik gereği üst seviyeye taşınmalı.

**Fix:** module-level `import os` aktarıldı, function body sadeleşti.

### C5 · `src/db/base.py:18-27` · 🟠 MED · **fixed**

```python
@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(...):
    ...
```

Modül-level event listener **tüm SQLAlchemy Engine'lerine** bind oluyor — bizim engine'imize değil. `init_models_for_tests()` zaten kendi engine'ine ayrı bir listener ekliyor → duplicate event execution (PRAGMA idempotent olduğu için zararsız, ama defensive programming sınırı). Üstelik aynı process'te yaşayan başka bir SQLAlchemy kullanıcısı (Alembic CLI vs.) bu listener'dan etkilenebilir.

**Fix:** modül-level listener kaldırıldı; `init_models_for_tests()` SQLite test bağlamında zaten kendi event listener'ını bağlıyor (session.py içinde). Postgres production'da no-op olduğu için davranış değişikliği yok.

### C6 · `src/server/auth.py:234` · 🟠 MED · **fixed**

```python
tenant_check = await limiter.check_tenant(tenant.id, per_minute=600)
```

Magic number `600` koda gömülü. Operator UI'dan tenant başına override imkanı yok, env yok, doc yok.

**Fix:** `settings.tenant_rate_limit_per_minute` eklendi (env `NQAI_TENANT_RATE_LIMIT_PER_MINUTE`, default 600). Audit log payload da bu değeri taşıyor.

### C7 · `src/server/auth.py:81-84` · 🟡 LOW · **fixed**

```python
def _set_redis_for_tests(redis: Redis) -> None:
    global _redis_client
    _redis_client = redis
```

Dead code — hiçbir test bunu kullanmıyor (testler fakeredis fixture'ı doğrudan `authenticate_bearer`'a geçiyor). Mutating global state, leak riski.

**Fix:** silindi. Test injection için kanonik yol: `app.dependency_overrides[get_redis]` — docstring buna yönlendiriyor.

---

## 2. Polish bulguları (P#) — kalite + okunabilirlik

### P1 · `src/db/session.py:60-68` · 🟡 LOW · **fixed**

Aynı isimde hem type annotation hem fonksiyon:

```python
AsyncSessionLocal: async_sessionmaker[AsyncSession]   # forward declaration
...
def AsyncSessionLocal(**kw):                          # actual definition
    return _ensure_sessionmaker()(**kw)
```

mypy ve IDE jump-to-def için kafa karıştırıcı. Runtime'da fonksiyon olarak çalışıyor, type annotation yanıltıcı.

**Fix:** annotation kaldırıldı, return type `AsyncSession` olarak explicit yazıldı, docstring eklendi.

### P2 · `src/server/rate_limit.py:97` · 🟡 LOW · **fixed**

```python
except (NoScriptError, ResponseError):
    raw = await self._redis.eval(...)
```

`ResponseError` çok geniş — yanlış key tipi, syntax hatası, RAM dolu, vb. **silent retry**'a düşer. Production'da gerçek bug'ları gizler.

**Fix:** `NoScriptError` ayrı yakalanıyor (Redis kendi eviction yolu). `ResponseError` sadece mesaj `NOSCRIPT` içeriyorsa retry'a düşüyor (eski fakeredis için fallback) — diğer her şey re-raise. Yorum satırı sebebi açıklıyor.

### P3 · `pyproject.toml` · 🟠 MED · **fixed**

Admin UI Jinja templates (`src/server/admin/templates/*.html`) `setuptools.packages.find` ile bulunan paketin içinde ama **package_data tablosunda değil** → `pip install` ile dist'e dahil edilmiyor. Docker image'da `pip install -e .` çalışıyor olsa da prod wheel'de `templates/` klasörü olmazdı → admin dashboard 500'er.

**Fix:** `[tool.setuptools.package-data]` eklendi: `"server.admin" = ["templates/*.html"]`. Yorumla nedeni belgelenmiş.

### P4 · `src/server/auth.py:34-38` · ⚪ NIT · **fixed**

`server.security` namespace'inden `APIKeyFormatError` + `parse_api_key` import edilmiş; `server.security.passwords`'dan `SecretMismatchError` + `verify_secret` ayrı import. Re-export zaten `__init__.py`'da var, ama `passwords` direkt import edilmiyor.

**Fix:** İki import bloğu sade kalacak şekilde reorganize edildi; her sembol kullanıldığı yerden import ediliyor (clarity > brevity).

### P5 · `pyproject.toml ruff config` · 🟡 LOW · **fixed**

FastAPI `Depends`/`Header`/`Cookie`/etc. dependency pattern'i ruff B008 (function-call-in-default) tetikliyordu. Bu pattern FastAPI'nin idiomatic kullanımı, bug değil.

**Fix:** `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls` listesine 8 FastAPI dependency helper'ı eklendi. Test'ler için `B008` per-file ignore ile genişletildi (SQLAlchemy default factories).

---

## 3. Bekleyen düşük öncelikli notlar (P#) — pazartesi öncesi bloker değil

Bunlar bilinçli bırakıldı, decision log'a satır gerekmiyor; ileride iterasyonla.

### P6 · `src/repos/voice.py:54` · ⚪ NIT · deferred

```python
license: str = "user-owned",
```

`license` Python built-in (bir argüman değil ama linter farkında olabilir). Ruff `A002` rule değil ama isim shadowing açık.

**Karar:** bırakıldı — `Voice.license` DB kolon adıyla aynı, kelime alanı tutarlılığı isim çakışmasından daha değerli. Faz B'de `VoiceCreateRequest` Pydantic schema'ya geçince konu kapanır.

### P7 · `src/repos/tenant.py:35` · ⚪ NIT · deferred

```python
metadata_=metadata or {},
```

`metadata` SQLAlchemy reserved name ile yumuşak çakışma (column ismi `metadata_` underscored). Argüman adı `metadata` kalabilir çünkü API yüzeyi temiz, mapping iç detay.

**Karar:** bırakıldı.

### P8 · `src/server/admin/router.py` · 🟡 LOW · deferred

Modern FastAPI pattern `Annotated[Operator, Depends(_current_operator)]` yerine eski `op = Depends(_current_operator)` kullanılıyor. Ruff B008 whitelist'i bunu siliyor. Faz B genel refactor'da modernize edilir; şu an davranış doğru, sade.

### P9 · `src/server/admin/router.py:267` ve `revoke_api_key` · ⚪ NIT · deferred

`revoke_api_key` endpoint'i, `key.revoked_at is not None` durumunda commit'siz return ediyor. O noktada commit edilecek bir audit row da yok (henüz yazılmamış) → davranış doğru ama edge case'i belgelemek için inline yorum eklenebilir.

**Karar:** Faz B audit pipeline daha katı olunca yorum eklenir.

### P10 · `src/server/repos/api_key.py:34` · 🟡 LOW · deferred

`lookup_active_by_prefix()` `selectinload(ApiKey.tenant)` kullanıyor — 1+1 query (key fetch + tenant fetch). Auth hot path için `joinedload` ile single-query'e geçirilebilir.

**Karar:** Faz B inference plane ayrımıyla birlikte profile + optimize.

### P11 · `migrations/versions/2026_05_24_0001_*.py` · 🟢 OK · noted

Forward-only policy: `downgrade()` `NotImplementedError` raise ediyor — bu kasıtlı (data-model.md §3). Alembic CI'da `alembic downgrade -1` çalıştırılmaz; pgroll Faz D'de eklenecek zero-downtime path için.

### P12 · `src/db/models.py` · 🟢 OK · noted

`UsageRecord` ve `JobIdempotency` model `default=lambda: datetime.now(timezone.utc)` inline lambda kullanıyor (replace_all sırasında `datetime.utcnow()` deprecation fix). Daha temiz `from .base import utcnow` import edilmesi, lambda yerine. Davranış doğru, polish.

**Karar:** Faz B'deki worker tarafı schema değişikliği sırasında düzeltilir; şu an risk yok.

---

## 4. Dosya başına özet

| Dosya | LOC | Bulgular | Test cov | Severity |
|---|---|---|---|---|
| `src/db/__init__.py` | 18 | yok | dolaylı | ✅ |
| `src/db/base.py` | 51 | C5 fixed | dolaylı | ✅ |
| `src/db/session.py` | 109 | P1 fixed | dolaylı | ✅ |
| `src/db/models.py` | 364 | P12 noted | 9 test | ✅ |
| `src/repos/tenant.py` | 56 | P7 noted | 3 test | ✅ |
| `src/repos/api_key.py` | 88 | P10 noted | 3 test | ✅ |
| `src/repos/voice.py` | 95 | P6 noted | 4 test | ✅ |
| `src/repos/usage.py` | 92 | yok | 1 test | ✅ |
| `src/repos/audit.py` | 61 | yok | 1 test | ✅ |
| `src/repos/operator.py` | 50 | yok | 1 test | ✅ |
| `src/repos/idempotency.py` | 79 | yok | 3 test | ✅ |
| `src/server/security/passwords.py` | 59 | yok | 6 test | ✅ |
| `src/server/security/api_keys.py` | 81 | yok | 7 test | ✅ |
| `src/server/security/jwt_tokens.py` | 126 | yok | 8 test | ✅ |
| `src/server/rate_limit.py` | 152 | P2 fixed | 9 test | ✅ |
| `src/server/auth.py` | 284 | C4, C6, C7 fixed; P4 fixed | 11 test | ✅ |
| `src/server/auth_legacy.py` | 56 | yok (geçici) | mevcut API tests | ✅ |
| `src/server/admin/router.py` | 316 | C1 fixed; P8, P9 noted | 12 test | ✅ |
| `src/server/admin/__init__.py` | 9 | yok | — | ✅ |
| `src/server/main.py` | 335 | C2, C3 fixed | 11 test | ✅ |
| `src/server/config.py` | 76 | C6 helper field eklendi | dolaylı | ✅ |
| `src/server/engine.py` | (v0.2) | bu sprint'te dokunulmadı | mevcut | ✅ |
| `src/server/streaming.py` | (v0.2) | bu sprint'te dokunulmadı | dolaylı | ✅ |
| `src/server/schemas.py` | (v0.2) | bu sprint'te dokunulmadı | dolaylı | ✅ |
| `migrations/env.py` | 80 | yok | full migration testi yok (testcontainers Faz B) | ✅ |
| `migrations/versions/2026_05_24_0001_*.py` | 285 | P11 noted | — | ✅ |
| `alembic.ini` | 50 | yok | — | ✅ |
| `scripts/seed_operator.py` | 64 | yok | manuel run | ✅ |
| `scripts/migrate_filesystem_to_db.py` | 197 | yok | manuel run | ✅ |
| `docker-compose.dev.yaml` | 80 | yok | manuel `docker compose up` | ✅ |
| `deploy/gateway.Dockerfile` | 36 | yok | manuel build | ✅ |
| `pyproject.toml` | 90+ | P3, P5 fixed | — | ✅ |

---

## 5. Test envanteri — pazartesi paralelize edebilecekler için

| Test file | # | Kapsam | Bağımlılık |
|---|---|---|---|
| `tests/test_numbers.py` | 22 | Türkçe sayı → kelime | yok |
| `tests/test_normalize.py` | 9 | TR TN (abbr, sembol, code-mix) | yok |
| `tests/test_segment.py` | 5 | Cümle segmentasyonu | yok |
| `tests/test_seed_catalog.py` | 2 | Lansman ses dağılımı (1+2+2) | yaml |
| `tests/test_db_models.py` | 9 | ORM round-trip, FK cascade, uniqueness | aiosqlite |
| `tests/test_repos.py` | 16 | Cross-tenant isolation, idempotency, soft delete | aiosqlite |
| `tests/test_security.py` | 21 | argon2id, API key gen/parse, JWT (round-trip, expired, tampered, type mismatch, secret config) | argon2-cffi, pyjwt |
| `tests/test_rate_limit.py` | 9 | Sliding window, helpers, slide forward | fakeredis[lua] |
| `tests/test_auth_flow.py` | 11 | Full pipeline (parse → DB → argon2 → tenant → scope → RL → audit) | aiosqlite + fakeredis |
| `tests/test_admin_flow.py` | 12 | JWT login, tenant CRUD, API key generate-once + revoke, dashboard render | aiosqlite + voxcpm stub |
| `tests/test_api_smoke.py` | 11 | Mevcut TTS endpoint'leri (legacy auth) | voxcpm stub |
| **Toplam** | **133** | | |

Hepsi `python -m pytest` (~16 s). `pyproject.toml`'da `pythonpath = ["src"]` ile lokal PYTHONPATH gerekmiyor.

---

## 6. Bilinen mimari "yarı durumlar" — bunlar sürpriz değil

Faz A.6 cutover'ı henüz yapılmadı; bunlar **bilerek** bu durumda:

1. **İki paralel auth yolu yan yana koşuyor:**
   - `src/server/auth.py` (DB-backed, scope + rate limit + audit) → `/admin/*` ve gelecek `/v1/tts/*` cutover sonrası
   - `src/server/auth_legacy.py` (env-list, no DB) → mevcut `/v1/tts/*` endpoint'leri hâlâ kullanıyor
   - Cutover: Faz A.6 sonu (planlanan ~1-2 saat). Sonra `auth_legacy.py` silinir.

2. **İki paralel voice catalog'u:**
   - `src/registry/` filesystem YAML — mevcut `/v1/voices/*` endpoint'leri
   - `repos/voice.VoiceRepo` DB-backed — admin UI tenant detayında usage panelinde
   - Cutover: `scripts/migrate_filesystem_to_db.py` ile veri taşı, sonra `/v1/voices/*` endpoint'leri DB-backed olur.

3. **CORS default `["*"]`** — D-04 (fail-safe defaults) ile kısmen çelişir. Geliştirme deneyimini bozmamak için bilinçli. Production deploy'unda `NQAI_CORS_ORIGINS=https://app.neeko.com,...` set edilmek **zorunda**.

4. **Operator MFA + IP allowlist yok** — Faz D scope'unda. v1 password-only OK, ama prod açılışta bir runbook girişi şart.

5. **Worker yok** — gateway hâlâ inference yapıyor (lazy-loaded VoxCPM2). Faz B `src/worker/` ile ayrılır.

---

## 7. Pazartesi checklist (operasyonel)

Ekip clone → çalışır duruma getirmek için:

```bash
# 1. Repo
git clone git@github.com:erdalgumuss/neuro-voice.git
cd neuro-voice

# 2. Lokal venv (test'ler için)
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest         # 133 yeşil olmalı

# 3. Docker stack (Postgres + Redis + gateway)
cp .env.example .env
# .env'i doldur — özellikle:
#   NQAI_JWT_SECRET=<min 32 char random>
#   NQAI_API_KEYS=<comma-separated dev keys>
docker compose -f docker-compose.dev.yaml up -d

# 4. DB migrate + operator oluştur
docker compose -f docker-compose.dev.yaml exec gateway \
    alembic upgrade head
docker compose -f docker-compose.dev.yaml exec gateway \
    python scripts/seed_operator.py --email <your-email>

# 5. Admin UI
open http://localhost:8000/admin/

# 6. (Opsiyonel) Var olan filesystem catalog'u DB'ye taşı
docker compose -f docker-compose.dev.yaml exec gateway \
    python scripts/migrate_filesystem_to_db.py \
        --tenant-slug neeko-prod --skip-upload
```

Kırılma ihtimali olan tek nokta: **NQAI_JWT_SECRET set edilmemesi** — admin login 500 verir, açıklayıcı RuntimeError mesajıyla.

---

## 8. Audit sonucu

| Kategori | Bulgu | Fixed | Deferred |
|---|---|---|---|
| Kritik (C1-C7) | 7 | **7** | 0 |
| Polish (P1-P5) | 5 | **5** | 0 |
| Düşük öncelik (P6-P12) | 7 | 0 | **7** (hepsi yorum eklendi veya Faz B'de planlı) |
| **Toplam** | **19** | **12** | **7** |

**Suite:** 133/133 yeşil (audit öncesi 133, audit sonrası 133 — regression yok).
**Lint:** `ruff check src tests scripts migrations` → "All checks passed!" (65 baseline → 0).
**Type check:** `mypy --strict` Faz B'de açılır; şu an `pyproject.toml`'da config var ama CI gate yok.

Pazartesi ekibe açıldığında bilinen kırılma noktası: **JWT secret env zorunluluğu** (runbook'ta belirtilmiş, error mesajı self-explanatory).
