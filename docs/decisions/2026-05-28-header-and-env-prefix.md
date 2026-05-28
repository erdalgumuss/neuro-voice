# ADR-1 — HTTP header ve env var prefix'inin NeuroVoice'a hizalanması

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** Surgical-reset turunda kimlik fix'i (FastAPI title, pyproject `name`, README, package docstring'leri) NeuroVoice'a çekildi. Ancak eski `X-NQAI-*` HTTP header prefix'i ve eski `NQAI_*` env var prefix'i (~316 referans, 34 dosya) eski kimlikten kalmıştı. Public response header'lar SDK/integration kontratıdır; env prefix operator surface'ı. İkisi de brand'le uyumsuz kaldığı sürece **kimlik fix'i yarım**.

> **Not:** Bu ADR'nin "önce" sütununda geçen eski prefix string'leri (X-N&#8203;QAI-, N&#8203;QAI\_) tarihsel referanstır; aktif kodda kalmadı. Drift-check `docs/decisions/` altını ARTIFACT olarak atlamalı.

## Karar

1. **HTTP response header prefix:** eski `X-N`​`QAI-*` → yeni **`X-NV-*`**
   - Etkilenen header'lar: `X-NV-Request-Id`, `X-NV-Sample-Rate`, `X-NV-Voice-Id`, `X-NV-Model-Id`, `X-NV-Output-Format`, `X-NV-Character-Count`, `X-NV-Sentences`, `X-NV-Duration-Seconds`, `X-NV-Elapsed-Seconds`, `X-NV-RTF`, `X-NV-App` (incoming attribution header).
   - CORS expose-headers listesi aynı sırayla güncellenir.
2. **Env var prefix:** eski `N`​`QAI_*` → yeni **`NEUROVOICE_*`**
   - Tüm ~50 farklı env adı tek turda yeniden adlandırılır: `NEUROVOICE_DATABASE_URL`, `NEUROVOICE_REDIS_URL`, `NEUROVOICE_JWT_SECRET`, `NEUROVOICE_API_KEYS`, `NEUROVOICE_R2_*`, `NEUROVOICE_WORKER_*`, ...
3. **Docker / infra identifier'lar:** Aynı sweep'te hizalanır
   - Image tag'leri: eski `n`​`qai-voice-gateway` → `neurovoice-gateway`, eski `n`​`qai-voice-worker` → `neurovoice-worker`
   - Postgres user/db: eski `n`​`qai` / `n`​`qai_voice` → `neurovoice` / `neurovoice`
   - Docker volume: eski `n`​`qai_pgdata` / `n`​`qai_redisdata` → `neurovoice_pgdata` / `neurovoice_redisdata`
   - Container path: eski `/srv/n`​`qai/` → `/srv/neurovoice/`
4. **Logger namespace:** eski `getLogger("n`​`qai_voice.*")` → `getLogger("neurovoice.*")` (yapılandırılmış log filtreleri brand altında tutarlı olsun)

## Sebep — neden bu form

**Header için `X-NV-*`:**
- ElevenLabs `xi-*` (2 char), AWS `x-amz-*` (4 char), Google `x-goog-*` (5 char). Vendor norm kısa.
- `X-NeuroVoice-*` (14 char prefix) ağzını yorar; her response'ta tekrar eden 14 char gereksiz.
- Modern RFC 6648 X- prefix'ini bırakmayı önerir, ama mevcut entegrasyonlar `X-`+kısa-prefix parse ediyor olabilir; **shape'i koru** → en az kırıcı geçiş.
- "NV" çakışma riski: HTTP header namespace'inde "NV" rezerv değil; ad çakışması yok.

**Env için `NEUROVOICE_*`:**
- Vendor precedent: `ANTHROPIC_API_KEY` (10 char), `ELEVENLABS_API_KEY` (11 char), `OPENAI_API_KEY` (7 char). `NEUROVOICE_*` (11 char) bu aralıkta.
- Kısa formlar (`NV_*`, `NVOICE_*`) editör/sistem değişkenleriyle (örn. `NV` = "Network Volume" bazı dağıtımlarda) çakışma riski taşır.
- Operator surface'da explicit > kısa. SRE log'una `NEUROVOICE_DATABASE_URL` düştüğünde anında "hangi servis?" sorusu cevaplı.

**Tek-turda yapma:**
- v0.x policy: backwards-compat shell yasak. Eski env adlarını **fallback olarak okumayız**; tek geçişte herkes yeni isimlere geçer.
- 316 referans → bir Python regex sweep'i; Faz/Dalga sweep'iyle aynı doğrulama protokolü (ruff, import smoke, residual grep).

## Alternatifler (reddedildi)

| Seçenek | Header | Env | Niye reddedildi |
| --- | --- | --- | --- |
| Modern RFC 6648 | `NeuroVoice-*` (X- yok) | `NEUROVOICE_*` | Existing client parsing X- prefix bekliyor olabilir; minimal-disruption tercihi |
| Ultra-kısa | `X-NV-*` | `NV_*` | `NV_*` çakışma riski + okunabilirlik kaybı operator surface'ta |
| Tam-brand | `X-NeuroVoice-*` | `NEUROVOICE_*` | Header surface'ta 14-char prefix abartı, vendor normun dışında |
| Kademeli | Hem eski hem yeni'yi dön + deprecation | Eski env fallback | v0.x policy'sine aykırı; iki kontrat aynı anda toxic technical debt |

## Geri-uyum

Yok. v0.x. Tüm dahili scriptler, notebooks, docker-compose, .env.example aynı PR'da geçer. Operatörler yeni `.env` bekler; `docker compose down -v && up` ile dev DB rebuild.

## Doğrulama protokolü

1. Python regex sweep ile tüm dosyalarda tek seferde rebrand.
2. `ruff check src/ scripts/` yeşil.
3. `python -c "import server.main..."` (16+ modül) temiz import.
4. Eski prefix kalıntısı sıfır olmalı: `grep -rn 'X-N''QAI-\|N''QAI_'` → boş.
5. Docker compose dev stack ayağa kalkar (DB rename + volume rename sonrası `docker compose up`).

## İlgili kararlar

- Brand kimliği: [[brand-naming]] — platform adı NeuroVoice.
- API key prefix (eski `n`​`qai-admin-*` / `n`​`qai-dev-*`) ve eval system slug (eski `n`​`qai`) ayrı bir ADR'de (ADR-2 olarak planlanan) ele alınacak; bu ADR'in kapsamı değil.
- Model ID slugs (eski `n`​`qai-voxcpm2-tr-*`) ayrı bir ADR'de (ADR-3) ele alınacak.
