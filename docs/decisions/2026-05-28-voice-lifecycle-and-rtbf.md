# ADR-11 — Voice lifecycle + right-to-be-forgotten

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** ADR-10 voice ownership + license + consent şemasını DB-enforce hale getirdi (`license_kind` kapalı liste, `voice_consent_records` 1:N, `talent_contracts`). **Runtime tarafı boş.** Schema yazılı, davranış değil:
  - `voice_consent_records.revoked_at` set ediliyor — ama synthesis worker'ı consent durumunu **kontrol etmiyor**, ses üretmeye devam ediyor.
  - `talent_contracts.expires_at` geçti — ama bağlı voice'lar otomatik durmuyor; tek bir trigger satırı yok.
  - `voices.deleted_at` (soft delete) var — ama "frozen" (geri dönülebilir durdurma) yok; reference audio + adapter weights silinmiyor (R2 forever).
  - **KVKK madde 11 + GDPR madde 17** (right to erasure) yolu yok. Bir tenant veya voice talent'in temsilcisi "verilerimi silin" derse 30 günlük yasal limit içinde silmemiz mümkün değil.

  İlk B2B müşterisinin compliance review'u veya bir voice talent'in revoke talebi gelirse bu boşluklar canlı yangın.

## Karar

Üç katmanlı lifecycle layer. **Schema küçük + runtime'a çoğu yatırım + cron worker out of scope**.

### 1. Schema — 3 yeni `voices` kolonu + 1 yeni tablo

```sql
-- voices: lifecycle state kolonları (hepsi nullable; NULL = "yok")
ALTER TABLE voices ADD COLUMN frozen_at      TIMESTAMPTZ;
ALTER TABLE voices ADD COLUMN frozen_reason  TEXT;
ALTER TABLE voices ADD COLUMN purge_after_at TIMESTAMPTZ;
ALTER TABLE voices ADD COLUMN purged_at      TIMESTAMPTZ;

-- partial indexes — operator queries + (future) cron worker scan
CREATE INDEX ix_voices_frozen
  ON voices (frozen_at)
  WHERE frozen_at IS NOT NULL AND purged_at IS NULL;
CREATE INDEX ix_voices_purge_pending
  ON voices (purge_after_at)
  WHERE purge_after_at IS NOT NULL AND purged_at IS NULL;
```

```sql
CREATE TABLE data_deletion_requests (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  voice_slugs        TEXT[] NOT NULL,           -- empty array = "all my voices"
  jurisdiction       TEXT,                       -- ISO 3166 alpha-2 / "EU"
  status             TEXT NOT NULL CHECK (
                       status IN ('pending','in-progress','completed','rejected')
                     ),
  requested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  requested_by_actor_id UUID,                    -- api_keys.id; no FK (polymorphic future)
  reason             TEXT,
  completed_at       TIMESTAMPTZ,
  completion_notes   TEXT
);
CREATE INDEX ix_data_deletion_pending
  ON data_deletion_requests (status, requested_at)
  WHERE status IN ('pending','in-progress');
```

`tenants.id` ON DELETE **RESTRICT** — finansal/compliance kayıt; tenant gerçek-silinemez (status='deleted' soft).

### 2. lifecycle_state — computed, stored değil

Lifecycle state kolon değil, fonksiyon. `Voice` row üzerindeki 4 timestamp'ten türetilir:

```python
def lifecycle_state(v: Voice) -> str:
    if v.purged_at is not None:
        return "purged"
    if v.deleted_at is not None:
        return "deleted"
    if v.purge_after_at is not None:
        return "purge-pending"
    if v.frozen_at is not None:
        return "frozen"
    return "active"
```

State sırası önem taşır (sondan başa: en spesifik state kazanır). `VoicePublic` API response'una eklenir; tenant frozen voice'unu listede `lifecycle_state: "frozen"` ile görür, ama sentez 410 Gone döner.

Postgres GENERATED column **kullanılmadı** — SQLite test-portability (TypeDecorator pattern'ine uymaz) + app-layer derivation daha esnek. Read-side gereksiniminde tek yerde.

### 3. Trigger — orchestration route'ta, repos thin

Service layer eklenmedi (prematür). Repo'lar thin kalır; freeze/unfreeze orchestration **route katmanında**. Pattern:

```python
# operator route: consent revoke
async def revoke_consent(consent_id, session):
    consent = await consent_repo.revoke(consent_id, reason="..." )
    # cascade: if this was the latest active consent on the voice, freeze
    if await consent_repo.latest_active(consent.voice_id) is None:
        await voice_repo_by_owner(consent.voice_id).freeze(
            voice_slug, reason="consent revoked", by_actor_kind="operator", ...
        )
    await session.commit()
```

Aynı pattern talent_contract revoke için — `license_kind='talent-contract'` + `license_ref=contract_id` voice'ları freeze.

### 4. Synthesis gate — `_ensure_voice_synthesizable`

Mevcut `_assert_voice_accessible_or_404` ([src/server/main.py:155](../../src/server/main.py#L155)) read AND synth path'lerin **ikisi** tarafından kullanılıyor — gating'i orada yapamayız (`GET /v1/voices/{id}` frozen voice'u 200 ile döndürmeli, sentez 410 dönmeli).

Yeni helper: synth path'lerde ek bir satır:

```python
db_voice = await _assert_voice_accessible_or_404(voice_id, ...)
await _ensure_voice_synthesizable(db_voice, session)  # ← new
```

İçeride:

1. `lifecycle_state(voice) != "active"` → `VoiceNotSynthesizable("voice is <state>")`
2. `latest_active_consent(voice.id) is None` → `VoiceNotSynthesizable("voice has no active consent")`

`VoiceNotSynthesizable` custom exception. HTTP route'lar **410 Gone** ile map eder (RFC 7231 — kaynak vardı ama artık kalıcı olarak yok). WebSocket close code **1008** + reason string ile kapatır.

**Worker re-check yok (v0).** Gateway gate'i queue submit'i bloklar; queue'da bekleyen bir job sentez sırasında durum değişimi olursa job tamamlanır. Bu sürede üretilen 1 ses (~3-30s) kabul edilebilir gecikme; race condition follow-up.

### 5. API surface

**Native (tenant):**

| Method | Path | Rol |
| --- | --- | --- |
| `POST /v1/data-deletion-requests` | KVKK/GDPR madde 17 talep oluşturur (voice slugs veya empty=all) | tenant |
| `GET /v1/data-deletion-requests/{id}` | Talep durumunu okur | tenant |
| `GET /v1/voices/{id}` | Response'a `lifecycle_state` eklenir | tenant |
| `GET /v1/voices` | Listede `lifecycle_state` eklenir | tenant |

`POST /v1/data-deletion-requests` davranışı:
1. Tenant'ın belirttiği voice'lara `freeze_at = now()` + `purge_after_at = now() + 30 days` set eder
2. `data_deletion_requests` row'u `status='in-progress'` ile yazılır
3. Operator manuel olarak (admin endpoint'inden) purge'u execute eder; tüm voice'lar purged → request `status='completed'`

**Operator (admin):**

| Method | Path | Rol |
| --- | --- | --- |
| `POST /admin/voices/{voice_db_id}/freeze` | Manuel freeze (incident response, abuse) | operator |
| `POST /admin/voices/{voice_db_id}/unfreeze` | Frozen'i geri al (cause cleared) | operator |
| `POST /admin/voices/{voice_db_id}/purge` | Manuel purge — R2 artifacts + DB anonymize | operator |
| `GET /admin/data-deletion-requests` | Operator inbox | operator |
| `POST /admin/data-deletion-requests/{id}/process` | Talebi işle (purge execute + status update) | operator |

Voice slug yerine **internal UUID** (`voices.id`) — admin endpoint'i cross-tenant context'te çalışır, slug uniqueness owner-scoped.

### 6. Purge mekaniği

Operator `POST /admin/voices/{id}/purge` çağırdığında:

1. **R2 silme:** `R2Storage.delete(voice.reference_uri)` (mevcut helper, [src/storage/r2.py:318](../../src/storage/r2.py#L318))
2. **Adapter silme:** `voice.adapter_uri` set ise R2'den sil
3. **DB anonimize:**
   - `reference_uri = NULL`
   - `reference_sha256 = NULL`
   - `adapter_uri = NULL`, `adapter_sha256 = NULL`
   - `display_name = '[purged voice]'`
   - `description = NULL`
   - `labels = NULL`
   - `preview_url = NULL`
4. `purged_at = now()` set
5. Audit log: `action='voice.purge'`, payload = before/after counts + R2 keys silinen

**audit_log toxic-touched — kalır.** Compliance / financial retention zorunluluğu. `usage_records` da kalır (voice_id slug stringi olarak referans tutar, FK değil).

`voices` row'u **silinmez** — `purged_at` flag ile "tombstone" olarak kalır. `tenant_id` + voice_slug FK'leri (örn. `voice_access`) hala valid; cross-reference kaybı yok.

### 7. Retention defaults

- **Frozen → purge_after_at:** 30 gün (GDPR pratiği; data subject erasure SLA üst sınırı 30 gün)
- **Soft delete (`deleted_at`) → frozen otomatik mi?** Hayır; soft delete sentezi durdurmaz; eski davranış korundu (sentez wrapper'ı zaten owner-only voice'ları okuyor, deleted_at filter'ı var). Soft delete + purge'u tenant ayrı isterse `data_deletion_requests` üzerinden.
- **Jurisdiction-specific retention:** v0'da yok; tek 30-gün default. Multi-region ADR'inde override.

### 8. Worker boot-time veri yükleme

Worker boot'unda `NEUROVOICE_WORKER_WARMUP_VOICES` ile pre-load yapan path ([src/worker/engine.py:246](../../src/worker/engine.py#L246)) `lifecycle_state == 'active'` filter'ı eklemeli — frozen/purged voice'u warmup'a almayız. Bu küçük bir kod değişikliği, ADR'in parçası.

## Yeni / değişen dosyalar

| Dosya | Tip | Rol |
| --- | --- | --- |
| `docs/decisions/2026-05-28-voice-lifecycle-and-rtbf.md` | yeni | bu ADR |
| `migrations/versions/2026_05_28_0011_voice_lifecycle_and_rtbf.py` | yeni | migration |
| `src/db/models.py` | update | Voice lifecycle kolonları + `DataDeletionRequest` modeli |
| `src/repos/voice.py` | update | `freeze`, `unfreeze`, `schedule_purge`, `execute_purge`, `lifecycle_state` helper'ları |
| `src/repos/data_deletion.py` | yeni | `DataDeletionRequestRepo` |
| `src/repos/__init__.py` | update | yeni repo'yu expose et |
| `src/server/schemas.py` | update | `VoicePublic.lifecycle_state` + `DataDeletionRequestPublic` + `VoiceNotSynthesizableError` |
| `src/server/main.py` | update | `_ensure_voice_synthesizable` helper + 3 synth route gate + tenant data-deletion endpoint'leri + `_voice_to_public` lifecycle_state geçirme |
| `src/server/ws.py` | update | WebSocket synth gate |
| `src/server/admin/router.py` | update | operator freeze/unfreeze/purge + deletion request inbox |
| `src/worker/engine.py` | minor | warmup filter (`lifecycle_state == 'active'`) |
| `CLAUDE.md` | update | ADR-11 row; ertelenmiş #3 düşer |

Follow-up (ADR'de **bağlanmayan**):

- **Cron worker / scheduler infra** — periodic expired-contract scan, scheduled purge auto-execute, notification queue. Ayrı ADR, scheduler altyapısı kurulduğunda.
- **Worker-side re-check** — gateway-side gate v0; race condition yazılı follow-up.
- **Multi-jurisdiction retention overrides** — multi-region ADR'i ile beraber.
- **Operator admin UI** — operator endpoint'leri canlı, UI ileride (ADR-10 kuyruğunda zaten var, ortak).
- **Right-to-Access (GDPR madde 15)** — data export endpoint'i; deletion ile simetrik ama ayrı kapsam.
- **Test paketi (Codex).**

## Sebep

- **Schema küçük + runtime ağır** — ADR-10 zaten 3 `revoked_at` kolonu ekledi; bu ADR onların runtime davranışını kurar. Yeni schema 3 column + 1 table (data_deletion_requests audit trail). Geri kalan runtime kod.
- **lifecycle_state computed:** stored field değil çünkü truth zaten 4 kolonda var. Stored field iki yerde tutmak = drift. Read-side derive et, source-of-truth single.
- **Custom exception > HTTPException everywhere:** Synthesis gate hem HTTP hem WS path'lerinden çağrılır; HTTPException WebSocket'ten fırlatılamaz. `VoiceNotSynthesizable` domain exception caller'a göre map eder (HTTP 410 vs WS close 1008).
- **30 gün GDPR pratiği:** Madde 17 SLA üst sınırı 30 gün ("without undue delay" → max 1 ay). Bu süre boyunca voice frozen olur (sentez yapmaz), data preserved (operator/audit istek geliştirebilir), sonra purge eligible.
- **Operator-confirmed purge v0:** Otomatik purge tehlikeli (yanlış data_deletion_requests + cron = veri kaybı). Manuel kapı bir göz daha vermek; v1 opt-in cron eklenebilir.
- **audit_log toxic-touched değil:** Finansal/compliance retention zorunlu. Voice tombstone ile reference integrity korunur (usage_records voice_id slug'ını okumaya devam eder).
- **410 Gone > 404:** Frozen/purged voice "var" — sadece artık kullanılamıyor. 410 doğru semantik (RFC 7231); SDK client'ları 410'u 404'ten farklı handle edebilir.
- **Trigger orchestration route'ta:** Service layer pattern güzel ama prematür (tek triggering var); cross-repo import'a değer yok. Route 5 satır orchestration yapar, açık.

## Risk

- **Race: queue'da bekleyen job freeze sırasında** — gateway gate öncesi geçen job freeze'den sonra synth yapar. v0'da kabul (1 ses, 3-30s); worker re-check follow-up.
- **`reference_uri` `file://` scheme'i ile başlayan eski satırlar** — R2 delete sadece `s3://` çağrılır; local file silme follow-up (eski enrollment'lar local'de). Production deployment R2 only varsayımı.
- **GENERATED column kullanmadık** — query'de `WHERE lifecycle_state = 'frozen'` direkt yazılamaz; `WHERE frozen_at IS NOT NULL AND purged_at IS NULL` yazılır. App-layer derive bunu helper ile gizler.
- **`data_deletion_requests.voice_slugs TEXT[]`** — tenant silme talebi geldiğinde slug'lar valid voice'lara işaret etmeyebilir (yanlış input, silinmiş voice). App-layer validate eder; eksik voice slug = warning, devam et.
- **`purged_at` set edilen row geri alınamaz** — operator yanlış purge çağırırsa data kaybı. Endpoint confirm prompt + dry-run flag follow-up.
- **Multi-tenant cascade:** Bir talent_contract revoke'u **birden çok tenant'a ait** voice'u freeze eder (talent NeuroVoice-side, voice tenant-side). Cascade route operator endpoint olduğu için yeterli; tenant kendi consent revoke'u sadece kendi voice'ını etkiler.

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| Stored `lifecycle_state` kolonu | İki yerde truth = drift; 4 timestamp zaten var |
| Postgres GENERATED column lifecycle_state | SQLite test portability bozulur (TypeDecorator pattern'ine sığmaz); Postgres-specific |
| Service layer (`LifecycleService`) | Tek triggering surface, premature abstraction; route 5 satır orchestration yapar |
| Worker-side synthesis re-check | v0 scope dışı; race koridoru dar (1 ses), follow-up |
| Otomatik cron purge | İlk versiyon riskli (yanlış deletion request → data kaybı); operator confirm gate'i şart |
| Soft delete = otomatik freeze | Existing tenant davranışını kırar; opt-in via data_deletion_requests daha temiz |
| audit_log purge etmek | Finansal/compliance retention zorunluluğu; tombstone yeterli |
| Tenant'a admin freeze/unfreeze API'si vermek | Tehlikeli; tenant manuel freeze yapması nadir (delete + new enroll yeterli) |
| 90 gün retention | GDPR SLA üst sınırı 30 gün; daha uzun süre risk + müşteri sözleşmesi rahatsızlığı |

## İlgili

- [[project-framing]] — uluslararası TTS API SaaS
- ADR-7 — voice manifest schema v2 (`watermark`, `eval_pin` future-shape kolonları)
- ADR-9 — public API spec stratejisi (lifecycle_state native yüzeyde)
- ADR-10 — license + consent (bu ADR onun runtime tarafı)
- CLAUDE.md ertelenmiş karar #3 — voice lifecycle / RTBF (bu ADR ile karara bağlandı)
