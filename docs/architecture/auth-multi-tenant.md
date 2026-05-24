# Auth + Multi-tenancy (kanonik)

**Doc owner:** Backend lead / Security review · **Bağlı:** [scale-roadmap.md §8](scale-roadmap.md), [data-model.md §1.2-1.3](data-model.md)
**Sürüm:** v1 · **Threat model:** OWASP API Security Top 10 (2023)

İki ayrı auth flow:
- **API key** — tenant uygulamaları (NEEKO/NIVA/NeuroCourse/NARO), uzun ömürlü Bearer credential
- **JWT** — NQAI operatörleri (admin UI), kısa ömürlü access + refresh

---

## 1. API key flow (tenant)

### 1.1 Key formatı (D-02)

```
nqai_<env>_<14-char-prefix>_<40-char-secret>
        │           │                │
        │           │                └─ base62 random secret, 40 char = ~238 bit entropy
        │           └─ base62 random prefix, 14 char (DB lookup index)
        └─ environment: prod | staging | dev
```

Tam örnek: `nqai_prod_a1b2c3d4e5f6g7_VqXp7kLZ2Mn4Rs8Tw5Yz9aB6cE3fH1iJ0lOpQrSt`

**Prefix sebebi:**
- DB'de plain text → B-tree index lookup ~10 µs
- Secret kısmı asla DB'de plain değil, sadece argon2id hash
- Leak case'inde log'da görünen prefix'le hangi key'in tehlikede olduğu hemen tespit edilir

### 1.2 Generation algoritması

```python
import secrets, string
from argon2 import PasswordHasher

ALPHABET = string.ascii_letters + string.digits  # base62

def generate_api_key(environment: str = "prod") -> tuple[str, str, str]:
    """Returns (full_key, prefix, secret_hash). Full key shown to user once."""
    prefix = "".join(secrets.choice(ALPHABET) for _ in range(14))
    secret = "".join(secrets.choice(ALPHABET) for _ in range(40))
    full_key = f"nqai_{environment}_{prefix}_{secret}"
    secret_hash = PasswordHasher(
        memory_cost=65536,  # 64 MB
        time_cost=3,
        parallelism=4,
        hash_len=32,
    ).hash(secret)
    return full_key, f"nqai_{environment}_{prefix}", secret_hash
```

**Hashing parametreleri (argon2id, OWASP 2024 önerisi):**
- `m=65536` (64 MiB memory cost)
- `t=3` (iteration count)
- `p=4` (parallelism)
- `hash_len=32`

Verification süresi ~50-100 ms — brute-force pratik olarak imkansız.

### 1.3 Validation flow

```
Client sends:  Authorization: Bearer nqai_prod_a1b2c3d4e5f6g7_VqXp7kLZ...
                                     │              │              │
                                     │              │              └─ secret
                                     │              └─ prefix
                                     └─ env tag
                                            ▼
1. Parse: env + prefix + secret split (regex)
2. DB lookup: SELECT * FROM api_keys WHERE prefix = $1 AND revoked_at IS NULL
3. argon2id verify: ph.verify(row.secret_hash, secret) → constant time
4. Scope check: required_scope IN row.scopes
5. Rate limit: Redis sliding window check (per-key + per-tenant)
6. Update last_used_at (async, fire-and-forget — UPDATE'in critical path'i yok)
7. Audit: INSERT audit_log (action='auth.success' veya 'auth.fail')
8. Return: ApiKey instance + Tenant instance (cached 60s in Redis)
```

**Hata cevapları (sabit timing — timing attack önleme):**
- Geçersiz format → `401` + generic message + minimum 50 ms artificial delay
- Prefix yok → `401` + 50 ms delay
- Secret yanlış → `401` (argon2id verify zaten ~50-100 ms — sabit timing)
- Revoked → `403` (revoked vs invalid ayrımı yok, generic 401 da OK)
- Scope yetersiz → `403` + `WWW-Authenticate: Bearer error="insufficient_scope"`
- Rate limited → `429` + `Retry-After: <seconds>`

### 1.4 Scope sistemi

| Scope | Yetki |
|---|---|
| `tts:read` | `GET /v1/voices`, `GET /v1/voices/{id}`, `GET /v1/usage` |
| `tts:write` | `POST /v1/tts`, `POST /v1/tts/stream`, WebSocket connect |
| `voice:read` | `GET /v1/voices/*` (read variants) |
| `voice:write` | `POST /v1/voices`, `DELETE /v1/voices/{id}` (voice enroll/delete) |
| `admin:read` | `GET /admin/*` (read-only admin endpoint'ler — operator JWT ile birlikte gerekir) |

Default key scope: `["tts:read", "tts:write"]`. Voice enroll için admin UI'dan ek scope verilir.

### 1.5 Rotation runbook

```
1. Admin UI veya CLI ile yeni key oluştur (aynı tenant_id altında)
2. Yeni key client tarafına dağıt (Slack/email + 1Password vb.)
3. Client uygulaması yeni key'e geçer
4. 7 günlük grace window: eski key hâlâ çalışır
5. 7 gün sonra: eski key REVOKE (admin UI butonu)
6. audit_log'da rotation kaydı (action='key.rotate', payload={old_prefix, new_prefix})
```

Acil durum (key compromise): bekleme yok, hemen revoke + yeni key + client'ı bilgilendir.

### 1.6 Rate limit (Redis sliding window + Lua atomic)

**Algoritma:** sliding window log (timestamp set'i ZADD, ZREMRANGEBYSCORE, ZCARD).

```lua
-- KEYS[1] = "rl:key:<api_key_id>"
-- ARGV[1] = current_ts_ms
-- ARGV[2] = window_ms (60000 = 1 min)
-- ARGV[3] = limit
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    local oldest = tonumber(redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')[2])
    return {0, math.ceil((oldest + window - now) / 1000)}  -- denied, retry_after_s
end
redis.call('ZADD', KEYS[1], now, now .. '-' .. math.random())
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}  -- allowed
```

**Limit hiyerarşisi:**
- Per-key: `rate_limit_per_minute` (default 60, per-key override DB'de)
- Per-tenant: aggregate cap (default 600/dk, suspended tenant 0)
- Per-IP (DDoS): Cloudflare edge rate limit (1000/dk default)

Tenant cap altındaki key'ler fair-share değil — first-come-first-served. Faz E'de weighted fair queueing düşünülür.

### 1.7 Audit log (security-relevant)

Her API call için **değil**, sadece security-relevant olaylar:

| Action | Payload örnek |
|---|---|
| `auth.success` | `{"prefix":"nqai_prod_a1b2c3d4e5f6g7","ip":"...","ua":"..."}` |
| `auth.fail` | `{"prefix":"...","reason":"invalid_secret","ip":"..."}` |
| `auth.rate_limited` | `{"key_id":"...","limit":60,"window_s":60}` |
| `key.create` | `{"tenant_id":"...","prefix":"...","scopes":[...]}` |
| `key.revoke` | `{"key_id":"...","reason":"compromised"}` |
| `key.rotate` | `{"old_prefix":"...","new_prefix":"..."}` |
| `tenant.suspend` | `{"tenant_id":"...","reason":"non_payment"}` |

Audit log INSERT yolu kritik path dışında (async task), failure tolerant ama eventual durable.

---

## 2. Operator JWT flow (admin UI)

### 2.1 Login

```
POST /admin/auth/login
Content-Type: application/json

{"email":"erdal@nqai.com","password":"<secret>"}

→ 200 OK
Set-Cookie: nqai_admin_access=<jwt>; HttpOnly; Secure; SameSite=Strict; Max-Age=3600
Set-Cookie: nqai_admin_refresh=<jwt>; HttpOnly; Secure; SameSite=Strict; Max-Age=604800; Path=/admin/auth/refresh

{"operator_id":"...","email":"...","roles":["admin"]}
```

### 2.2 JWT yapısı

**Access token (1 saat TTL):**
```json
{
  "iss": "nqai-voice",
  "sub": "<operator_uuid>",
  "iat": 1716534000,
  "exp": 1716537600,
  "scope": "admin",
  "roles": ["admin"]
}
```

**Refresh token (7 gün TTL):**
```json
{
  "iss": "nqai-voice",
  "sub": "<operator_uuid>",
  "iat": 1716534000,
  "exp": 1717138800,
  "type": "refresh",
  "family": "<rotation-family-uuid>"
}
```

Algorithm: **HS256** (v1, single secret env var); **RS256 + key rotation** Faz D'de (Vault'tan key çekme).

### 2.3 Refresh + token rotation

Refresh token kullanıldığında **yenisi verilir + eskisi family-blacklist'e eklenir**. Family'deki herhangi bir token replay'i tespit edilirse tüm family revoke + operator email bildirim. (Token theft detection pattern.)

### 2.4 Password yönetimi

- Hash: argon2id (aynı parametreler API key ile)
- Min length: 12 char, OWASP composition rules ZORUNLU değil (NIST 800-63B), ama HIBP API ile breached password check
- Reset flow: email tek-kullanımlık link (15 dk TTL), TOTP eklenir Faz D

### 2.5 MFA (Faz D)

TOTP (RFC 6238) + WebAuthn (FIDO2). Faz A-C basic password only.

---

## 3. Cross-cutting konular

### 3.1 Tenant isolation enforcement (D-08)

**Üç katman:**

1. **Application layer (Faz A-D):** ORM repository pattern, her query'ye `WHERE tenant_id = :tid` parameter zorunlu.
```python
class VoiceRepo:
    async def list_for_tenant(self, tenant_id: UUID) -> list[Voice]:
        result = await self.session.execute(
            select(VoiceORM).where(VoiceORM.tenant_id == tenant_id)
        )
        return [v.to_domain() for v in result.scalars()]
    # NOT: list_all() YOK — tenant_id zorunlu.
```

2. **DB layer (Faz C+):** PostgreSQL Row-Level Security policies (bkz. [data-model.md §2](data-model.md)).

3. **Integration test (her PR'da):**
```python
async def test_cross_tenant_isolation(client, tenant_a_key, tenant_b_voice):
    # Tenant A try to read Tenant B's voice
    r = await client.get(f"/v1/voices/{tenant_b_voice.voice_id}",
                        headers={"Authorization": f"Bearer {tenant_a_key}"})
    assert r.status_code == 404  # NOT 403 — existence leak yok
```

### 3.2 Existence leak prevention

Tenant A, başka tenant'ın voice'larının var olup olmadığını anlayamamalı:
- `GET /v1/voices/{id}` → 404 her durumda (yok veya başkasının)
- `POST /v1/voices` aynı `voice_id` ile farklı tenant'larda OK
- Error mesajları generic — "voice not found", "voice not accessible" değil

### 3.3 CORS

```python
CORSMiddleware(
    allow_origins=["https://app.neeko.com", "https://niva.nqai.voice", ...],
    allow_credentials=True,
    allow_methods=["GET","POST","DELETE","OPTIONS"],
    allow_headers=["Authorization","Content-Type","X-Request-Id"],
    expose_headers=["X-NQAI-Request-Id","X-NQAI-Sample-Rate","X-NQAI-RTF",
                    "X-NQAI-Duration-Seconds","X-NQAI-Sentences"],
)
```

`allow_origins=["*"]` yasak (prod). Tenant per origin allowlist DB'de tutulur (Faz D), middleware dynamic okur.

### 3.4 HSTS + cookie security

```
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: microphone=(), camera=(), geolocation=()
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'
                         (admin UI için tighten — Faz D)
```

Admin cookies: `HttpOnly` + `Secure` + `SameSite=Strict`.

### 3.5 Secret yönetimi

| Secret | Saklama | Rotation |
|---|---|---|
| API key secret | argon2id hash DB'de | Client-initiated, grace window |
| Operator password | argon2id hash DB'de | NIST 800-63B: only on suspected compromise |
| JWT signing key | `NQAI_JWT_SECRET` env | Quarterly + suspected compromise |
| DB password | Doppler/Vault (staging+), env (dev) | Quarterly |
| R2 access keys | Doppler/Vault | Monthly (Faz D) |
| HF token (model download) | Vault | Yearly |
| Cloudflare API token | Vault | Yearly |

### 3.6 Brute-force defense

- Per-IP failed login: 5/dk → 5 dk soft block (Cloudflare rule)
- Per-prefix failed key: 10/dk → 1 saat soft block (Redis counter)
- Argon2id verify cost → tek deneme 50-100 ms (online brute-force yavaş)
- 10k+ deneme detect → Slack alert + auto-revoke

---

## 4. Test stratejisi

| Test | Araç | Kapsam |
|---|---|---|
| Argon2id round-trip | pytest | hash + verify, parametre değişimi |
| API key generation entropy | pytest | uniqueness over 100k, no collision |
| Auth success/fail paths | pytest + httpx | 200/401/403/429 timing benchmark |
| Cross-tenant isolation | pytest integration | 404 leak yok |
| Rate limit sliding window | pytest + fakeredis | exact count, retry_after correct |
| Audit log immutability | pytest | UPDATE/DELETE engellenir |
| JWT signature tampering | pytest | tamper → 401 |
| JWT expired | pytest | exp + clock skew tolerance |
| Refresh rotation + replay detection | pytest | family blacklist çalışır |
| Argon2id timing (constant) | manual + statistical | benchmark p95 within 10ms variance |
| Password breach check (HIBP) | mock + pytest | breached password → reject |

---

## 5. Bilinen sınırlar + Faz D iyileştirmeleri

| Sınır | Faz D çözüm |
|---|---|
| JWT HS256 single secret | RS256 + key rotation (Vault dynamic secret) |
| Operator MFA yok | TOTP + WebAuthn |
| API key son kullanım tarihi yok | Optional `expires_at` field (long-lived keys için bile rotation hedefi) |
| IP allowlist key başına yok | `allowed_ips CIDR[]` field eklenir (B2B müşteri ister) |
| Webhook signing key yok (callback ihtiyacı doğarsa) | HMAC-SHA256 signing + timestamp + replay protection |
| Tenant SSO (SAML/OIDC) | Operator UI için Auth0/WorkOS Faz E |
