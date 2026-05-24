# Data Model — PostgreSQL Schema (kanonik)

**Doc owner:** Backend lead · **Bağlı:** [scale-roadmap.md §8](scale-roadmap.md)
**Geçerli sürüm:** v1.0 · **Postgres:** 16+ · **Migration aracı:** Alembic 1.13+

Bu dokuman NQAI Voice'un veri planının tek kaynağıdır. Her schema değişikliği önce buraya satır olarak yansır, sonra Alembic migration yazılır, sonra deploy edilir. Sapma yok.

---

## 0. Genel kurallar

| Kural | Sebep |
|---|---|
| Primary key her tabloda **`UUID` + `gen_random_uuid()`** (pgcrypto), bigserial sadece insert-only log tablolarında | Distribution + leakage önleme |
| Timestamp her tabloda **`TIMESTAMPTZ` + `now()` default** | UTC tek standart |
| **Soft delete yok**, sadece `revoked_at` / `deleted_at` nullable kolonu — hard DELETE yapılmaz | Audit trail + GDPR (DPA gerektiğinde explicit erase prosedürü) |
| `tenant_id` her cross-tenant tabloda **NOT NULL + FK + index** | Multi-tenant zorunlu filter (D-08) |
| Indices: tüm tenant_id'li tablolarda `(tenant_id, created_at DESC)` covering index | Tenant-scoped son N kayıt sorgusu O(log N) |
| **Row-Level Security (RLS) Faz C+'ta açılır** — uygulama katmanı filter'ı ilk savunma, RLS ikinci | Defense in depth (P5) |
| Migration **forward-only**; revert yerine `forward fix` yazılır | Production'da rollback risksiz değil |
| Schema değişikliği = yeni Alembic file + `pytest tests/test_migrations.py` (testcontainers'ta full migration run) | CI'da otomatik |

---

## 1. Şemalar ve tablolar

### 1.1 `tenants`

```sql
CREATE TABLE tenants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT NOT NULL UNIQUE
                  CHECK (slug ~ '^[a-z][a-z0-9-]{1,62}[a-z0-9]$'),
    display_name  TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 120),
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','suspended','deleted')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX ON tenants (status) WHERE deleted_at IS NULL;
```

**Notlar:**
- `slug` = canonical identifier (URL, log, dashboard'da görünür); kebab-case zorunlu
- `metadata` JSONB ileride esnek alan (contact email, plan_tier, vb.); ilk versiyon boş
- 4 seed tenant: `neeko-prod`, `niva-prod`, `neurocourse-prod`, `naro-prod`

### 1.2 `api_keys`

```sql
CREATE TABLE api_keys (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    prefix                 TEXT NOT NULL UNIQUE
                           CHECK (prefix ~ '^nqai_(prod|staging|dev)_[a-zA-Z0-9]{14}$'),
    secret_hash            TEXT NOT NULL,  -- argon2id('$argon2id$v=19$m=65536,t=3,p=4$...')
    scopes                 TEXT[] NOT NULL DEFAULT ARRAY['tts:read','tts:write']::TEXT[]
                           CHECK (scopes <@ ARRAY['tts:read','tts:write','voice:read','voice:write','admin:read']::TEXT[]),
    rate_limit_per_minute  INT NOT NULL DEFAULT 60 CHECK (rate_limit_per_minute > 0),
    label                  TEXT,  -- "production", "staging", "ci"
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_operator_id UUID REFERENCES operators(id),
    last_used_at           TIMESTAMPTZ,
    revoked_at             TIMESTAMPTZ,
    revoked_reason         TEXT
);

CREATE INDEX api_keys_tenant_idx ON api_keys (tenant_id);
CREATE INDEX api_keys_prefix_active_idx ON api_keys (prefix) WHERE revoked_at IS NULL;
```

**Notlar:**
- `prefix` lookup hızlı (B-tree index, ~10 µs), `secret_hash` argon2id verify ~50-100 ms (intentional — brute-force hardening)
- `scopes` enum yerine `TEXT[]` çünkü ileride kolayca yeni scope eklenebilir
- `revoked_at` set edildiğinde auth fail (silinmez — audit için tutulur)

### 1.3 `operators`

```sql
CREATE TABLE operators (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE CHECK (email ~ '^[^@]+@[^@]+\.[^@]+$'),
    password_hash TEXT NOT NULL,  -- argon2id
    full_name     TEXT,
    roles         TEXT[] NOT NULL DEFAULT ARRAY['admin']::TEXT[]
                  CHECK (roles <@ ARRAY['admin','operator','viewer']::TEXT[]),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    disabled_at   TIMESTAMPTZ
);
```

**Notlar:**
- Operator = NQAI çalışanı, admin UI'a login olur
- Tenant'lardan tamamen ayrı auth flow (JWT, ayrı log path)
- 3 başlangıç rolü: `admin` (her şey), `operator` (CRUD ama silme yok), `viewer` (read-only dashboard)

### 1.4 `voices`

```sql
CREATE TABLE voices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    voice_id            TEXT NOT NULL
                        CHECK (voice_id ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'),
    display_name        TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'tr' CHECK (language IN ('tr','en')),
    gender              TEXT NOT NULL DEFAULT 'neutral'
                        CHECK (gender IN ('neutral','female','male')),
    style_tags          TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    reference_uri       TEXT NOT NULL,  -- s3://r2/voices/<tenant_slug>/<voice_id>.wav
    reference_sha256    TEXT NOT NULL CHECK (length(reference_sha256) = 64),
    reference_seconds   REAL NOT NULL CHECK (reference_seconds > 0 AND reference_seconds <= 60),
    reference_sample_rate INT NOT NULL DEFAULT 16000,
    source              TEXT NOT NULL
                        CHECK (source IN ('elevenlabs','voice-talent','user-enroll','placeholder','bootstrap')),
    license             TEXT NOT NULL,
    engine_params       JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Faz 3 alanları (önceden tanımlı, başlangıçta NULL)
    adapter_uri         TEXT,
    adapter_sha256      TEXT,
    adapter_type        TEXT CHECK (adapter_type IN ('lora','full-finetune')),
    watermark_key_id    TEXT,
    eval_metrics        JSONB,
    release_status      TEXT NOT NULL DEFAULT 'draft'
                        CHECK (release_status IN ('draft','staging','production','deprecated')),
    --
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_key_id   UUID REFERENCES api_keys(id),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    UNIQUE (tenant_id, voice_id)
);

CREATE INDEX voices_tenant_idx ON voices (tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX voices_release_idx ON voices (tenant_id, release_status)
    WHERE deleted_at IS NULL;
```

**Notlar:**
- `voice_id` tenant içinde unique (composite); aynı `voice_id` farklı tenant'larda olabilir
- `engine_params` JSONB → per-voice override (cfg_value, inference_timesteps, mode tag overrides)
- Faz 3 alanları (adapter*, watermark*, eval*) bugünden tanımlı, NULL ile başlar — schema migration sonra gerek yok
- `release_status` voice lifecycle: draft → staging → production → (deprecated)

### 1.5 `usage_records` (time-series)

```sql
CREATE TABLE usage_records (
    id            BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id),
    api_key_id    UUID NOT NULL REFERENCES api_keys(id),
    voice_id      TEXT NOT NULL,
    request_id    UUID NOT NULL UNIQUE,  -- D-05 idempotency anahtarı
    text_char_count INT NOT NULL CHECK (text_char_count >= 0),
    sentence_count  INT NOT NULL CHECK (sentence_count >= 0),
    duration_ms     INT NOT NULL CHECK (duration_ms >= 0),  -- sentezlenen ses
    elapsed_ms      INT NOT NULL CHECK (elapsed_ms >= 0),   -- server-side wall clock
    ttfb_ms         INT CHECK (ttfb_ms >= 0),
    rtf             REAL,
    status          TEXT NOT NULL CHECK (status IN ('ok','error','timeout','partial')),
    error_code      TEXT,
    worker_id       TEXT,
    model_version   TEXT
);

CREATE INDEX usage_records_tenant_time_idx ON usage_records (tenant_id, occurred_at DESC);
CREATE INDEX usage_records_key_time_idx ON usage_records (api_key_id, occurred_at DESC);
```

**Migration plan to TimescaleDB:** Faz D'de bu tablo `usage_records` hypertable'a dönüştürülür (Timescale extension), `occurred_at` partition key. Retention policy 90 gün hot + aggregate continuous aggregate (gün/ay özet) sonsuza dek.

### 1.6 `audit_log` (append-only)

```sql
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_type      TEXT NOT NULL CHECK (actor_type IN ('api_key','operator','system')),
    actor_id        UUID,
    actor_label     TEXT,  -- prefix or operator email
    tenant_id       UUID REFERENCES tenants(id),
    action          TEXT NOT NULL,  -- 'auth.success', 'auth.fail', 'voice.create', 'key.revoke' ...
    result          TEXT NOT NULL CHECK (result IN ('success','denied','error')),
    target_type     TEXT,
    target_id       TEXT,
    ip_addr         INET,
    user_agent      TEXT,
    request_id      UUID,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_tenant_time_idx ON audit_log (tenant_id, occurred_at DESC);
CREATE INDEX audit_log_action_idx ON audit_log (action, occurred_at DESC);

-- Trigger: bu tabloya UPDATE ve DELETE engellenir (Faz A.2 hardening)
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
```

**Notlar:**
- Append-only — UPDATE / DELETE engellenir, sadece INSERT
- Standart action namespace:
  - `auth.success`, `auth.fail`, `auth.rate_limited`
  - `tenant.create`, `tenant.suspend`, `tenant.delete`
  - `key.create`, `key.revoke`, `key.rotate`
  - `voice.create`, `voice.update`, `voice.delete`
  - `tts.request`, `tts.error` (yalnızca security-relevant; usage_records normal akış için)
  - `operator.login`, `operator.logout`, `operator.password_change`

### 1.7 `job_idempotency` (cache, kısa TTL)

```sql
CREATE TABLE job_idempotency (
    request_id      UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    api_key_id      UUID NOT NULL REFERENCES api_keys(id),
    request_hash    TEXT NOT NULL,  -- sha256(json_canonical(body))
    response_uri    TEXT,           -- s3://r2/snapshots/<request_id>.wav
    status          TEXT NOT NULL CHECK (status IN ('processing','complete','failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours')
);

CREATE INDEX job_idempotency_expires_idx ON job_idempotency (expires_at);
```

**Notlar:**
- 24 saat TTL — aynı `request_id` 2. kez gelirse cache'lenmiş sonuç döner (D-05)
- `request_hash` body değişikliği detect eder (aynı request_id ile farklı text → 409 Conflict)
- Cron: günlük `DELETE FROM job_idempotency WHERE expires_at < now()` — Faz D pg_cron extension

---

## 2. Row-Level Security (Faz C+)

Faz A-B uygulama katmanı filter'ı yeterli. Faz C'de RLS açılır, defense-in-depth tamamlanır:

```sql
-- voices
ALTER TABLE voices ENABLE ROW LEVEL SECURITY;
ALTER TABLE voices FORCE ROW LEVEL SECURITY;
CREATE POLICY voices_tenant_isolation ON voices
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);

-- usage_records, audit_log — benzer
```

Application code her connection'da `SET LOCAL app.current_tenant_id = '<uuid>'` yapar (transaction scope). RLS bypass için ayrı `nqai_admin` rolü.

---

## 3. Migration policy (Alembic)

```
migrations/
├── env.py
├── script.py.mako
└── versions/
    ├── 2026_05_24_0001_initial_schema.py
    ├── 2026_05_25_0001_add_voice_engine_params.py
    └── ...
```

**Kurallar:**
1. **Naming:** `YYYY_MM_DD_NNNN_<snake_case_description>.py`
2. **Forward-only:** `downgrade()` boş `pass` veya en fazla `raise NotImplementedError("forward-only")` — geri dönülmez, sorun varsa yeni forward migration
3. **Online migration:** Faz D+'ta `pgroll` ile zero-downtime (kolon ekleme + backfill + cutover)
4. **Test:** her PR'da `pytest tests/test_migrations.py` (testcontainers Postgres 16 spin, full migrate, schema dump diff)
5. **Production deploy:** `alembic upgrade head` her release init container'ında
6. **Rollback yerine forward fix:** migration broken çıkarsa hotfix migration + 2. forward apply

---

## 4. Backup + DR

| Tier | Strategy | RPO | RTO |
|---|---|---|---|
| **Faz A-B** | Hetzner snapshot (günlük) + pg_dump (saatlik → R2) | 1 saat | 1 saat |
| **Faz C** | + WAL archiving (R2 üzerine, sürekli) | < 1 dk | 15 dk |
| **Faz D** | + Hot standby (streaming replication) + automatic failover (Patroni) | ~0 sn | < 1 dk |

---

## 5. Performans + tuning notları (Faz C+'ta sıkı)

```ini
# postgresql.conf (Hetzner CX31 8GB RAM örneği)
shared_buffers = 2GB
effective_cache_size = 6GB
maintenance_work_mem = 512MB
work_mem = 32MB
random_page_cost = 1.1  # SSD
effective_io_concurrency = 200
max_wal_size = 4GB
checkpoint_completion_target = 0.9
default_statistics_target = 100

# pgBouncer (Faz C)
pool_mode = transaction
default_pool_size = 25
min_pool_size = 5
max_client_conn = 200
```

Connection pool boyutu = `(worker_count × threads_per_worker) + (gateway_count × workers_per_gateway × 2) + 10 buffer`. 20-user baseline'da ~40 yeterli, 200-user'da ~120.

---

## 6. Migration: filesystem YAML → DB (Faz A.4)

Mevcut `configs/voices/*.yaml` ve `data/reference-audio/*` → DB + R2:

```bash
python scripts/migrate_filesystem_to_db.py --dry-run
python scripts/migrate_filesystem_to_db.py \
    --tenant-slug neeko-prod \
    --voices-dir configs/voices \
    --reference-dir data/reference-audio \
    --r2-bucket nqai-voice-prod
```

**Adımlar (idempotent):**
1. Tenant `neeko-prod` yoksa oluştur
2. Her YAML için:
   - reference audio'yu `data/reference-audio/`'dan R2'ye yükle (`voices/<tenant>/<voice_id>.wav`)
   - sha256 + sample_rate + duration hesapla
   - `voices` tablosuna INSERT (UNIQUE constraint nedeniyle re-run no-op)
3. Migration log → `audit_log` (`action='voice.migrate'`)

**Sonra:** `configs/voices/*.yaml` ve `configs/seed_voices.yaml` deprecate edilir; admin UI bu rolü devralır.
