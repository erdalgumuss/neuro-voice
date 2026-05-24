# Audit — Pre-Faz-B.1 (5 ajan konsolidasyonu)

**Tarih:** 2026-05-24 · **Commit baseline:** `1f7d0f4` · **Suite:** 204/204 · **Lint:** clean
**Audit kapsamı:** Repo'nun tamamı 5 bağımsız agent'a paralel verildi; her ajan modülün amacını öğrenip kendi metodolojisiyle audit yaptı.

> Önceki audit'ler: [`faz-a-self-audit.md`](faz-a-self-audit.md), [`faz-a-mlops-audit.md`](faz-a-mlops-audit.md), [`checkpoint-2026-05-24-faz-a-exit.md`](checkpoint-2026-05-24-faz-a-exit.md).

---

## 1. Ajan oyları

| # | Kapsam | Verdict | HIGH | MED | LOW |
|---|---|---|---|---|---|
| 1 | **Gateway** — `src/server/{main,queue,schemas,streaming,engine,reference_resolver}.py` | PROCEED | 5 | 12 | 5 |
| 2 | **Auth/Security/Admin** — `src/server/{auth,rate_limit,security/*,admin/*}` | PROCEED | 4 | 8 | 6 |
| 3 | **Data Plane** — `src/db/`, `src/repos/`, `migrations/` | PROCEED | 4 | 5 | 5 |
| 4 | **Storage** — `src/storage/r2.py`, `src/server/reference_resolver.py`, `src/registry/*` | **BLOCK** | 4 | 7 | 3 |
| 5 | **TR Frontend** — `src/frontend/{normalize,numbers,segment}.py` | **BLOCK** | 8 | 6 | 4 |

**Toplam:** 25 HIGH, 38 MED, 23 LOW = 86 bulgu.

---

## 2. Konsolide karar — CONDITIONAL PROCEED

İki BLOCK oyu var, ama mahiyetleri farklı:

- **Storage BLOCK gerçek.** Concurrent worker'lar `download_to_cache()` `.part` race'ine girer; sample rate default drift (24000 vs 16000) silent VoxCPM2 reference contract ihlali yapar; cache invalidation eksikliği aynı URI'a override silent stale audio'ya yol açar. **Bunlar Faz B.1 ile yeni failure modu açar** (single-process gateway'de yoktu).
- **Frontend BLOCK overcautious.** Time regex'i bozuk, segmenter ASCII `...` bilmiyor, telefon/tarih/sıra sayısı yok — bunlar gerçek production bug'ları, ama **gateway'de zaten varlar**. Worker shared modülü kullanırken aynı çıktıyı verecek (frontend deterministik, module-level state yok). Worker'a geçmek bug'ı **kötüleştirmiyor**; aynı bug iki süreçte tekrar ediyor. Frontend hardening Faz 1 scope'unda (CLAUDE.md "300+ golden test + Zemberek").

**Sonuç:** Storage BLOCK'undan çıkmak için 4 surgical fix yapılır + 1 idempotency cleanup, sonra Faz B.1 worker süreci açılabilir.

---

## 3. Şimdi yapılacak — Pre-Faz-B.1 (bu PR'da)

5 fix, hepsi az satır, hepsi test'le birlikte:

| # | Modül | Bulgu | Fix |
|---|---|---|---|
| **F1** | `src/repos/idempotency.py:100-111` | `reserve_or_get()` read-then-insert race; iki worker aynı `request_id` + farklı `request_hash` ile geçerse loser PK collision'da `IntegrityError` fırlatır, `IdempotencyConflict` değil | `INSERT ... ON CONFLICT DO NOTHING` (PG) / catch `IntegrityError` → re-read → reclassify; test ile iki concurrent reserve doğrulanır |
| **F2** | `src/storage/r2.py:175-201` | `download_to_cache` `.part` dosyası deterministik isim → concurrent download'da ikinci yarım dosya birincinin tamamladığını override eder + `tmp.unlink(missing_ok=True)` finally'de doğru dosyayı silebilir | `.part`'a `os.getpid()` + `uuid4().hex` suffix; cache key sha256(uri) içine ETag/last-modified karıştır; test ile concurrent fetch'te yarış doğrulanır |
| **F3** | `src/registry/audio_io.py:19` | `target_sr=24000` default; CLAUDE.md ve enroll signature 16 kHz diyor → caller default'a düşerse VoxCPM2 reference contract ihlali | Default `target_sr=16000`; `src_suffix` parametresi dead → kaldır veya kullan (librosa format hint için kullan) |
| **F4** | `src/db/models.py:158-160` + `:226` + `:319` + `:401` | `Operator.created_at` naive `datetime.utcnow()` ama column `DateTime(timezone=True)`; ayrıca inline `__import__("datetime").datetime.utcnow()` lambda 3 yerde (P12 audit deferred) | `from .base import utcnow` helper → tüm `default=` lambda'ları onunla değiştir; tek source-of-truth + tz-aware |
| **F5** | `src/server/main.py:723-733` | XADD failure path'inde `idem.fail()` çağrılıyor → key kalıcı poison olur, retry 409 alır; mesaj bile "retry with a new Idempotency-Key" diyor (D-05 ihlalini itiraf ediyor) | XADD failure'da `IdempotencyRepo.delete(rid)` (yeni metod) → reserved row silinir, aynı key + aynı body retry temiz çalışır; test ile XADD raise → 502, sonraki call → 202 |
| **F6** | `src/server/main.py:777` (status endpoint) + `src/storage/r2.py:205` (helper var ama bağlı değil) | Async job tamamlandığında `response_uri` (`s3://...`) raw olarak client'a dönüyor; presigned URL üretilmiyor; comment "Faz B+ when R2 storage is bound" diyerek itiraf ediyor — bu **internal URI leak** + client URI'ı kullanamaz | `_maybe_presigned_url(uri)` helper: `s3://`/`r2://` ise `get_r2_storage().presigned_get_url(uri, expires_in=3600)`; R2 env yoksa veya transient error'da raw URI fallback (dev `file://` path için) + structured warning; 2 yeni test: presigned mint + fallback |

**Tahmini iş yükü:** 2-3 saat.

---

## 4. Faz B.1 PR'ında (worker yazımıyla birlikte)

Bunlar worker rollout'unu amplify edenler, ama worker süreci olmadan fix yazılamaz veya bağlam eksik:

| Modül | Bulgu | Çözüm |
|---|---|---|
| `src/server/main.py:552-596` | `/v1/tts/stream` `usage_records` yazmıyor — silent billing/quota gap | Streaming handler'da final chunk sonrası `_record_usage()` çağrısı (worker'a taşıma sırasında doğal eklenir) |
| `src/server/main.py:509` | Sync `/v1/tts` event loop'u bloke ediyor | Faz B.1'de sync zaten internal proxy'e dönüşüyor → event-loop friendly olur; bu fix doğal kapanır |
| `src/db/models.py:316-321` + `:401-406` | `UsageRecord.tenant_id/api_key_id` + `JobIdempotency.tenant_id/api_key_id` FK'lerinde `ondelete` yok | Worker schema değişikliği ile birlikte `ondelete="RESTRICT"` veya `"CASCADE"` (tenant lifecycle politikası kararı) |
| `src/repos/api_key.py:82-87` | `touch_last_used()` her request'te WRITE → connection pool baskısı | Throttle: `UPDATE ... WHERE last_used_at < now() - interval '60 seconds'`; worker yoğun yük üretmeden önce |
| `src/server/main.py:308` | `GET /v1/voices/{id}` `_load_voice_or_404` ile R2 download tetikliyor | Read-only metadata path'te reference resolution skip; sadece sync `/v1/tts` ve worker pipeline'da çözülür |

---

## 5. Paralel hardening PR (worker land sonrası, kullanıcı testinden önce)

Bunlar gateway/admin surface bug'ları — worker'a değmez ama production'a açılmadan kapatılmalı:

| Modül | Bulgu | Severity |
|---|---|---|
| `src/server/admin/templates/dashboard.html:99-130` | Stored XSS: `t.slug`, `t.display_name`, `stats.*` `innerHTML`'e escape'siz interpolate | HIGH |
| `src/server/auth.py:118-167` | Pre-auth IP rate limit yok → bilinen prefix'e argon2id (64 MiB × N denemе) CPU/RAM-exhaustion DoS | HIGH |
| `src/server/admin/router.py:102-108` | Failed operator login `audit_log`'a yazılmıyor — brute force unobservable | HIGH |
| `src/server/admin/router.py:155-173` | `create_tenant` `slug`/`display_name` validation yok (`min/max_length`, pattern) | HIGH |
| `src/server/admin/router.py:78/82/85/104` | 4 farklı 401 mesajı — email exists/not-exists side channel | MED |
| `src/server/admin/router.py:73-86` | `OperatorClaims.roles` üretilir ama hiç check edilmez — her operator full admin | MED |
| `src/server/security/jwt_tokens.py:101-107` | JWT `nbf` ve `aud` claim'leri yok; clock-skew leeway 0 | MED |
| `src/server/main.py:441-451` | Malformed `X-Request-Id` silent UUID mint (D-01: 400 dönmeli) | HIGH |

Tahmini iş yükü: 4-5 saat (XSS escape + pre-auth limiter + audit gap'leri + role check).

---

## 6. Faz 1 scope (frontend hardening, ayrı sprint)

Bu kapsamda olanlar Faz B.1'i blok etmez ama premium TR domain hedefini etkiler. Faz 1 sprint'inde tek pakette çözülür (CLAUDE.md zaten 300+ golden test diyor):

- `normalize.py:64,87-93` — `_TIME_RE` "Saat 14:30" → "Saat saat..." üretiyor; leading-zero düşüyor; 00:xx desteklenmiyor
- `numbers.py + normalize.py` — sıra sayısı (1., birinci), tarih (23 Nisan, 5/3/2026), yıl (2026), telefon (0532...), IBAN yok
- `segment.py:13-15` — ASCII `...` regex'te yok (sadece U+2026 horizontal-ellipsis); newline boundary olarak tanınmıyor
- `segment.py:21-36` — `_merge_short` agresif birleştirme sentence boundary'leri yiyor
- `normalize.py:11-27` — kısaltma listesi sığ: "Doç.", "Yrd.", "Müh.", "vd.", "bkz.", "örn.", "M.Ö." vs. eksik
- `normalize.py:122` — `_CODE_MIX_LEXICON` case-mix patlıyor; "iPhone'unu" `\b` apostrof sınırı Türkçe ek için yanlış
- `normalize.py:29-40` — sembol kapsamı dar: `@`, `#`, `°`, `™`, `/` yok
- `normalize.py:126` — `½` → NFKC → `1⁄2` (slash arada) ama "bir bölü iki" üretmiyor

Önerilen Faz 1 yaklaşımı: `src/frontend/{time,date,ordinal,phone,abbreviation}.py` ayrı modüller + 80+ golden test data file (`tests/data/tr_golden_cases.yaml`).

---

## 7. Bilinmesi gereken polish (LOW, ileride)

Bu maddeler bug değil, kod kalitesi:

- `src/db/session.py:106-142` — `init_models_for_tests()` `Base.metadata.tables` mutate ediyor (destructive global)
- `src/repos/voice.py:24-29` — `list()` builtin shadow
- `migrations/env.py:80` — `asyncio.run()` import-time (pytest-alembic compat riski)
- `src/server/main.py:99-115` — iki layer engine singleton (`_engine` + `engine.py`'da `_engine_singleton`)
- `src/server/schemas.py:21-23` — `TTSStreamRequest.chunk_format` dead field
- `src/server/main.py:846-849` — `_now_iso` dead code
- `src/registry/catalog.py:175` vs `audio_io.py:19` — sample rate drift (F3 ile çözülüyor)

---

## 8. Definition of Done — Faz B.1'e gerçekten hazır

Bu PR (Pre-Faz-B.1) kapandığında:

- [x] 5 ajan audit raporu doc'lanmış
- [x] F1-F6 fix'leri uygulanmış + test'leri yeşil (F6 kullanıcı bulgusuyla eklendi)
- [x] Suite 204 → 211 (+7 yeni test) yeşil
- [x] Ruff temiz
- [ ] Decision log'a "Pre-Faz-B.1 audit hardening" satırı (bu PR commit message'ında)

Sonra Faz B.1 worker PR'ı açılabilir — `worker-process.md` spec'ine göre.

---

## 9. Tek söz

5 ajan paralel, eksik gözle bakmış, 86 bulgu çıkarmış. Erdal'ın bağımsız audit'i 6'ıncı fix'i ekledi (presigned URL binding eksikti, internal `s3://` URI leak ediyordu). 6 fix worker süreci yazılmadan önce kapatıldı: storage race + body_hash race + sample rate drift + datetime tutarlılığı + XADD failure idempotency cleanup + presigned URL binding. Diğer 20 HIGH ya worker PR'ıyla doğal kapanıyor, ya paralel hardening sprint'ine, ya Faz 1 frontend sprint'ine düşüyor. **Faz B.1 bloksuz.**
