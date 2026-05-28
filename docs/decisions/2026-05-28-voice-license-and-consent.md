# ADR-10 — Voice license taxonomy, consent records, talent contracts

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** Voice catalog'unda kim-sahip / hangi-izinle sorularının yazılı cevabı yok:
  - `voices.license: Text NOT NULL` ([src/db/models.py:256](../../src/db/models.py#L256)) freeform string; DB constraint yok. Biri `"premium-extra"` yazsa geçer.
  - `voices.source` eski CHECK enum'u (`'elevenlabs','voice-talent','user-enroll','placeholder','bootstrap'`, [migrations/0001:158](../../migrations/versions/2026_05_24_0001_0001_initial_schema.py#L158)) yeni kimlikle (NeuroVoice multilingual TTS API, ADR-7/9) çelişir. `elevenlabs` source değeri vendor-parity döneminden kalma; voice'un nasıl yakalandığı semantiği yanlış kalıba oturmuş.
  - ADR-7 license vocabulary'sini (`example`, `synthetic`, `user-owned`, `talent-contract:<id>`) **informal** yazdı — sadece YAML sample voice'larda; DB veya API enforcement yok.
  - Consent verification flow yok. `POST /v1/voices` ([src/server/main.py:678](../../src/server/main.py#L678)) tek bool `voice_talent_consent` alıyor; bu `engine_params.requires_verification` flag'ine maps oluyor ama izin **kanıtı** (recorded statement, signed contract, estate permission, sözleşme referansı) hiçbir yerde tutulmuyor.
  - Talent contract data model'i yok. `talent-contract:<id>` formundaki `<id>` hangi tabloya pointing belirsiz.

  Bu boşluklar ilk gerçek B2B müşterisinin legal review'u veya voice cloning'in monetize edildiği ilk adımda duvar yaratır. ADR-9 native API yüzeyini sözleşmeye aldı; bu ADR voice-katalog yüzeyini hukuki sözleşmeye alır.

## Karar

Esnek bir mimari ile dört eşzamanlı değişiklik. Kapalı liste enforcement'ı **Postgres ENUM yerine CHECK constraint** ile — value eklemek tek `ALTER TABLE ... DROP CONSTRAINT / ADD CONSTRAINT` migration'ı, ENUM type'a yeni value eklemek tüm production'u kilitler.

### 1. License kind taksonomisi (CHECK constraint, kapalı liste)

`voices.license` Text kolonu `license_kind`'a yeniden adlandırılır + CHECK constraint eklenir. Yeni opsiyonel `license_ref` Text kolonu — polymorphic reference (talent_contracts.id veya external URL veya rationale notu).

Kapalı liste:

| `license_kind` | Anlamı | `license_ref` beklentisi |
| --- | --- | --- |
| `example` | Bundled sample voice, demo amaçlı | NULL |
| `synthetic` | Tamamen sentetik, gerçek kişi referansı yok | NULL |
| `user-owned` | Tenant kendi sahipliğini beyan ediyor (kendi sözcüsü, kendi sözleşmeli ses sanatçısı) | NULL veya tenant-side reference (free text) |
| `talent-contract` | NeuroVoice-side talent sözleşmesi (biz tutuyoruz) | `talent_contracts.id` (UUID string) — uygulama katmanında doğrulanır |
| `public-figure` | Kamuya mal olmuş kişi / tarihi figür / kurgusal karakter | Estate consent ref, public-domain rationale, veya partner agreement URI |
| `partner-licensed` | Partner platformdan lisanslanmış (onların kendi consent zincirleri ile) | Partner agreement URI / contract ref |

DB CHECK constraint sadece `license_kind`'i kapalı tutar; `license_ref` ile cross-validation app katmanında.

### 2. Consent records — 1:N voice → consent

Yeni tablo `voice_consent_records`. Bir voice süresince birden çok consent kaydı birikebilir (initial → renewed → revoked); "geçerli consent" application logic'i her zaman en son `revoked_at IS NULL` row'unu okur.

```sql
CREATE TABLE voice_consent_records (
  id              UUID PRIMARY KEY DEFAULT new_uuid(),
  voice_id        UUID NOT NULL REFERENCES voices(id) ON DELETE CASCADE,
  consent_kind    TEXT NOT NULL CHECK (consent_kind IN (
                    'tenant-asserted', 'recorded-statement',
                    'signed-contract', 'estate-permission'
                  )),
  evidence_uri    TEXT,                -- R2 s3:// for non-tenant-asserted
  evidence_sha256 TEXT,
  evidence_notes  TEXT,
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  recorded_by_kind TEXT NOT NULL CHECK (recorded_by_kind IN ('tenant','operator')),
  recorded_by_actor_id UUID,           -- api_keys.id veya operators.id; FK YOK (polymorphic)
  revoked_at      TIMESTAMPTZ,
  revoked_reason  TEXT,
  CONSTRAINT ck_consent_evidence_presence CHECK (
    (consent_kind = 'tenant-asserted' AND evidence_uri IS NULL)
    OR
    (consent_kind != 'tenant-asserted' AND evidence_uri IS NOT NULL)
  )
);

CREATE INDEX ix_voice_consent_voice_recorded
  ON voice_consent_records (voice_id, recorded_at DESC);
```

Consent kinds:

| `consent_kind` | Anlamı | Evidence URI |
| --- | --- | --- |
| `tenant-asserted` | Tenant API çağrısında "izin aldım" deklare etti; kanıt bizde değil | NULL (zorunlu) |
| `recorded-statement` | Audio recording of consent statement (R2'de) | R2 URI zorunlu |
| `signed-contract` | İmzalı sözleşme PDF (R2'de) | R2 URI zorunlu |
| `estate-permission` | Estate/heir consent (public-figure license_kind için) | R2 URI zorunlu |

v0'da **tenant-asserted yeterli** (esneklik kararı, yönetici onayı 2026-05-28). Premium tier'da ileride evidence zorunlu hale getirilebilir — application layer policy, schema değişmez.

### 3. Talent contracts — operator-managed

NeuroVoice-side sözleşmelerin tutulduğu sistem tablosu. Tenant-scoped DEĞIL (operator iş akışı); `license_kind='talent-contract'` voice'lar `license_ref` üzerinden referans verir.

```sql
CREATE TABLE talent_contracts (
  id                    UUID PRIMARY KEY DEFAULT new_uuid(),
  talent_full_name      TEXT NOT NULL,
  contract_pdf_uri      TEXT NOT NULL,             -- R2 s3://
  contract_pdf_sha256   TEXT NOT NULL,
  signed_at             TIMESTAMPTZ NOT NULL,
  expires_at            TIMESTAMPTZ,                -- NULL = perpetual
  jurisdiction          TEXT,                       -- ISO 3166-1 alpha-2 veya "EU"
  notes                 TEXT,
  created_by_operator_id UUID REFERENCES operators(id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at            TIMESTAMPTZ                 -- voice freeze trigger (ayrı ADR)
);
```

v0'da bu tabloya yazma path'i **sadece operator** (admin UI / future endpoint). Tenant API'sinden erişim yok. Şimdilik UI'sı yok — operator manuel SQL ile insert eder; admin UI ileride. Bu ADR sadece tabloyu kurar.

### 4. `voices.source` enum refresh

Eski → yeni:

| Eski | Yeni | Not |
| --- | --- | --- |
| `placeholder` | `bootstrap` | Daha doğru semantik |
| `user-enroll` | `tenant-enroll` | "Tenant" kelimesi multi-tenant kimliğimize uygun |
| `voice-talent` | `talent-recorded` | NeuroVoice talent contract'lı stüdyo kaydı |
| `elevenlabs` | (kaldırıldı) | Vendor-parity URL'leri kalıyor, ama "voice **kaynağı** ElevenLabs" semantiği yanlış |
| (yok) | `synthetic-from-prompt` | Gelecek synthesizer pipeline çıktısı için yer |
| (yok) | `partner-import` | Partner platform'dan içeri alınan voice |

Yeni CHECK constraint: `source IN ('bootstrap','tenant-enroll','talent-recorded','synthetic-from-prompt','partner-import')`.

v0.x'te production row yok — temiz migration. `placeholder` ve `user-enroll` (seed sample varsa) `bootstrap`'a yeniden eşlenir; `elevenlabs` ve `voice-talent` zaten kullanılmıyor.

### 5. API surface değişiklikleri

**`POST /v1/voices` (native, kanun biz)** — form fields:

- **Eklenir:**
  - `license_kind: Literal[6 values]` zorunlu, default yok
  - `license_ref: str | None` opsiyonel
  - `consent_kind: Literal[4 values]` zorunlu, default yok
  - `consent_evidence_uri: str | None` opsiyonel; `consent_kind != 'tenant-asserted'` ise app-layer validation şart koşar
  - `consent_evidence_notes: str | None` opsiyonel free-text

- **Kaldırılır:**
  - `source` (form field) — server-side `'tenant-enroll'` hardcoded
  - `license` (form field) — `license_kind` ile değişti
  - `voice_talent_consent: bool` — `consent_kind` ile değişti; `tenant-asserted` consent dengi

- **Davranış:** Voice row + voice_consent_records row aynı transaction'da yazılır. License kind/ref tutarlılık check'i (örn. `license_kind='talent-contract'` ise `license_ref` zorunlu) app-layer.

**`POST /v1/voices/add` (ElevenLabs parity)** — ADR-9 "native extensions on parity routes yasak" kuralı:

- License/consent form fields **kabul edilmez** (Pydantic / Form signature'da yok).
- Server-side force defaults: `license_kind='user-owned'`, `consent_kind='tenant-asserted'`, `recorded_by_kind='tenant'`, `evidence_uri=NULL`.
- Davranış [docs/api/vendor-parity.md](../api/vendor-parity.md)'ye eklenir.

**`GET /v1/voices` ve `GET /v1/voices/{id}`** — response shape `VoicePublic`'e `license_kind` eklenir (eski `license` field'ı v0.x breaking change ile kaldırılır; ADR-9 versioning policy v0.x anytime-breaking).

Consent records public API'ye expose **edilmez** — internal audit, operator-only via admin (UI ileride).

### 6. v0.x backward-compat disiplini

v0.x'te anytime-breaking serbest (ADR-9 / versioning.md). Migration mevcut row'larda:

- `license = 'internal-bridge'` veya `license = 'example'` veya `license = 'internal-placeholder'` → `license_kind = 'example'`
- `source = 'placeholder'` → `source = 'bootstrap'`
- `source = 'user-enroll'` → `source = 'tenant-enroll'`
- Diğer license string'leri (free-form) → `license_kind = 'user-owned'` (defansif default; v0.x'te zaten neredeyse hiç row yok)

Eski bool `engine_params.requires_verification` field'ı korunur (mevcut tüketici varsa); yeni consent flow ile çakışmaz.

## Yeni / değişen dosyalar

| Dosya | Tip | Rol |
| --- | --- | --- |
| `docs/decisions/2026-05-28-voice-license-and-consent.md` | yeni | bu ADR |
| `migrations/versions/2026_05_28_0010_voice_license_taxonomy_and_consent.py` | yeni | migration |
| `src/db/models.py` | update | Voice: `license` → `license_kind` + `license_ref`; yeni `TalentContract`, `VoiceConsentRecord` modelleri |
| `src/repos/talent_contract.py` | yeni | `TalentContractRepo` (operator-scoped, tenant_id filtresi yok) |
| `src/repos/voice_consent.py` | yeni | `VoiceConsentRecordRepo` (voice-scoped üzerinden tenant via voice owner) |
| `src/repos/__init__.py` | update | yeni repo'ları expose et |
| `src/repos/voice.py` | update | `create()` signature'ı license_kind/license_ref alır; eski `license` parametresi gider |
| `src/server/schemas.py` | update | `VoicePublic.license_kind`; yeni `TalentContractPublic`, `VoiceConsentRecordPublic` schemas |
| `src/server/main.py` | update | `_enroll_voice_impl` license + consent persists; `/v1/voices` native + `/v1/voices/add` parity ayrılır |
| `src/registry/catalog.py` | update | `Voice.license` → `license_kind` + `license_ref` |
| `configs/voices/tr-warm-storyteller-v0.yaml` | update | `license` → `license_kind: example` + `license_ref: null` |
| `configs/seed_voices.yaml` | update | aynı |
| `docs/api/vendor-parity.md` | update | parity route license defaults bölümü |
| `CLAUDE.md` | update | ADR-10 tablosu; ertelenmiş kararlar listesi |

Follow-up (bu ADR'de **bağlanmayan**):

- Right-to-be-forgotten lifecycle (frozen voice, purge_after_at, audit retention) — ayrı ADR; consent revocation tetiklerini bu ADR'in `revoked_at` kolonları zaten taşır, runtime semantics ayrı.
- Operator admin UI: talent_contracts CRUD + voice consent verification list — ileride.
- Premium tier consent evidence policy (evidence zorunluluğu) — billing ADR'i ile beraber.
- KVKK/GDPR multi-region consent residency — multi-region ADR'i (ertelenmiş #2) ile beraber.
- Test paketi (Codex).

## Sebep

- **CHECK constraint > Postgres ENUM.** ENUM extend etmek `ALTER TYPE ... ADD VALUE` requires DB transaction lock + her client'i reload; CHECK constraint single ALTER. v0.x'te taksonomi genişlemesi muhtemel — esnek tutmak doğru.
- **Polymorphic `license_ref` FK enforce edilmez.** `license_ref` bazen UUID (talent_contracts), bazen URL (partner agreement), bazen serbest rationale. Hard FK koymak esnekliği kıracak; app-layer integrity v0.x için yeterli.
- **Consent 1:N voice → records.** Tek consent kolonu yeterli görünebilir, ama tenant-asserted → signed-contract upgrade, annual renewal, revocation hep ayrı record gerektirir. 1:N forward-compat şart.
- **Talent contracts operator-managed, tenant-scope DEĞIL.** Bu NeuroVoice'in iş kayıtları; tenant'lar bu tabloyu görmez/yazmaz. Tenant kendi sözleşmeli sanatçısını kullanıyorsa `license_kind='user-owned'`.
- **Tenant-asserted v0'da yeterli (esneklik kararı).** Entegratör sürtünmeyi minimize ediyoruz; sorumluluk tenant'ta. Premium tier'da evidence zorunluluğu sonradan eklenebilir (application policy, schema değişmez).
- **Parity route license force-default ADR-9 ile tutarlı.** Native-extension yasağı parity yüzeyinde; license/consent native disipline tabidir, parity caller bu alanları görmez, defaults atanır.

## Risk

- **`license_ref` polymorphic yapısı app-layer disipline bağımlı.** Yanlış type'ta value yazma riski (örn. talent-contract için URL yazma) — repo katmanında runtime validation şart. Codex testlerinde cover edilmeli.
- **Consent record append-only disiplini formalize değil.** v0'da update'i kapatmıyoruz (sadece `revoked_at` set); ileride append-only enforcement gerekirse trigger eklenebilir.
- **Migration sırasında v0.x prod row yok varsayımı.** Tek doğru başlangıç koşulu; yanlışsa migration `license` rename'inde freeform string'leri kaybedebiliriz. Defansif olarak `license_kind = 'user-owned'` fallback'i migration script'inde.
- **Existing `engine_params.requires_verification` legacy bool**'u kullanan kod parçası varsa kırılma (`engine_params` JSONB, schema değişmez). Sadece okuma side'ında — fail-soft.
- **Public-figure license kind hukuki olarak hassas.** v0'da açık tutuyoruz (yönetici kararı); ileride jurisdiction-specific kısıtlar (örn. EU'da ünlü kişi seslerinde extra disclosure) eklenebilir.

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| Postgres ENUM type (`license_kind_enum`, `consent_kind_enum`) | Extend cost yüksek; CHECK constraint daha esnek |
| `license` Text kolonunu olduğu gibi tut, sadece convention belge | Drift garantili; freeform string ilk tenant'ta kirlenir |
| Talent contracts tenant-scoped tablo | Yanlış; bu NeuroVoice'in business kayıtları, multi-tenant değil |
| Consent record 1:1 voice → record | Renewal / revocation yolunda 1:N forward-compat şart |
| `license_ref` FK ile talent_contracts'a hard bağla | Public-figure ve partner-licensed için ref UUID değil — polymorphic gerekiyor |
| Parity route'a license_kind/consent_kind form field'ları ekle | ADR-9 native-extension yasağı ihlali; parity ↔ native ayrımı bulanıklaşır |
| v0'da public-figure license kind'ı yasakla | Yönetici onayı 2026-05-28: açık tut, esnek mimari + permissioned flow |
| Migration'ı 2 ayrı turda (license schema → consent flow) | Schema yarım kalır (`talent-contract` enum value var ama tablo yok); aynı pakette ship etmek tutarlı |

## İlgili

- [[project-framing]] — uluslararası TTS API SaaS, ElevenLabs/MiniMax referans
- ADR-1 — `X-NV-*` header prefix
- ADR-7 — voice manifest schema v2 (license vocabulary'sini informal yazdı; bu ADR DB-enforce eder)
- ADR-8 — LoRA fine-tune pipeline (talent voice'lar genelde fine-tune adapter ile gelir)
- ADR-9 — public API spec stratejisi (parity route native-extension yasağı buradan gelir)
- CLAUDE.md ertelenmiş karar #3 — voice ownership + KVKK/GDPR (bu ADR ile karara bağlandı; lifecycle/RTBF ayrı entry'ye taşındı)
