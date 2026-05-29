# Production Readiness Assessment — NeuroVoice

- **Tarih:** 2026-05-29
- **Bağlam:** Yatırımcı teknik inceleme (TDD) öncesi bağımsız iç değerlendirme. Hedef: GPU'lar hazır, worker'lar dinamik (autoscale) sayılarda koşuyor, API kendi ürünleri üstünde + dış geliştirici trafiği bekleniyor.
- **Metodoloji:** 8 paralel `tts-platform-reviewer` agent + cross-cutting `general-purpose` agent. Her agent dosya:satır kanıtıyla çalıştı. Bu doküman sentezdir; ham bulgular [Appendix A](#appendix-a--agent-raporları)'da.

> **Tek cümleyle:** Application layer'ı gerçekten iyi mühendislik. Operasyonel substrate ise neredeyse yok. Yatırımcının teknik incelemecisi alert kurallarını + ADR zincirini olumlu okuyacak, sonra `tests/` ve `.github/workflows/` ve `docs/runbooks/` klasörlerini arayacak ve hepsini boş bulacak. Bu doküman o boşluğun ne olduğunu, neden olduğunu, sırasıyla nasıl kapatılacağını söyler.

---

## Yönetici Özeti (200 kelime)

Yatırımcı incelemesi iki gözle bakar: (a) mimari ve kod kalitesi, (b) üretim disipline edici altyapı. **(a) güçlü** — gateway/worker ayrımı gerçek, Redis Streams consumer groups + XAUTOCLAIM + DLQ doğru kurulmuş, 13 ADR ile policy çerçevesi yazılı, ADR-10/11/13 voice cloning trinity'sini kurmuş, ADR-12 quality bench'i (WER+CER+UTMOSv2+SECS) doğru üçlü. **(b) boş** — `tests/`, `.github/workflows/`, `docs/runbooks/`, `docs/release/`, `docs/architecture/`, k8s manifest'leri, Helm chart, Terraform, secret store, backup runbook, on-call playbook — hiçbiri yok.

İncelemeci üç soru sorar: "Test paketinizi gösterin" (`tests/` yok, pyproject test deps unused), "Release süreciniz nedir?" (yazılı değil, yolo merge), "İlk customer bir bug bildirirse on-call ne yapar?" (request_id propagation yok, OpenTelemetry yok, Sentry yok, dashboard'daki saturation panel renaming sonrası boş data döndürüyor).

**3 var-olmaz risk:** (1) Postgres backup stratejisi olmaması — tek corruption şirketi bitirir, (2) Reference audio `file://` yolunda local disk'te yaşıyor — gateway 2 replica olur olmaz pod-A enroll ettiği voice pod-B'den 404 döner, (3) JWT key rotation + admin brute-force koruması yok — tek leaked operator cookie tüm tenant sürüncesi.

**Tahmini hazırlık:** 6-8 mühendis-haftası, 4 hafta üzerinde sequential bağımlılık var (CI → image registry → k8s → traffic). Bayram sonrası ilk hafta net olarak kritik 10 fix'i kapatmak gerekiyor.

---

## Skor Kartı — 10 Eksen

| Eksen | Durum | Özet |
|---|---|---|
| **Mimari / Kod kalitesi** | 🟢 Yeşil | ADR-9..13 + ADR-7/8'le çerçeve iyi; D-08 tenant filter disiplinli; idempotency doğru; XAUTOCLAIM/DLQ doğru |
| **API / SDK sözleşmesi** | 🟡 Sarı | Native /v1/tts/jobs round-trips yapar; ama ElevenLabs SDK düşmez (auth header + language literal), error envelope drift, hiçbir snapshot/contract test yok |
| **Database / Storage** | 🟠 Turuncu | Schema iyi; ama backup yok, R2 lifecycle yok, per-tenant prefix yok, idempotency sweeper yok, partition yok |
| **Worker / Queue / GPU** | 🟠 Turuncu | XACK matrix + DLQ + capacity heartbeat doğru; ama warmup cross-tenant okur, GPU memory budget yok, per-job timeout yok, watermark concurrent forward unsafe |
| **ML Pipeline (Mühendislik)** | 🟠 Turuncu | engine_inputs audit doğru; LoRA cache LRU; ama model registry yok, drift detection yok, eval promotion gate kapalı, supply chain unbounded |
| **Bilimsel Disiplin / Akademik Kalite** | 🟠 Turuncu | 3-eksen metric seçimi literatür-uyumlu; ama test seti 60 cümle (literatür 100-1000+), tek ses (literatür 5-50), TR-only "multilingual" platform, SOTA akademik kıyas yok, bootstrap CI yok, model card / dataset datasheet yok, ElevenLabs-türev reference circular, byte-deterministic değil, on-disk benchmark sayısı SIFIR |
| **Security / Multi-tenant** | 🟠 Turuncu | D-08 sağlam, generic 401, argon2id; ama JWT rotation yok, admin brute-force yok, API key never expires, audit-log integrity DB-layer korumasız |
| **Observability / SRE** | 🔴 Kırmızı | Metric ve alert yazılı; ama dashboard yeniden adlandırılmış metric sorguluyor (sessiz), request_id propagation yok, OpenTelemetry yok, Sentry yok |
| **DevOps / Deployment** | 🔴 Kırmızı | Dev compose + Dockerfile var; CI/CD yok, k8s/Helm/Terraform yok, secret store yok, /ready yok, image registry yok |
| **Testing / Release** | 🔴 Kırmızı | `tests/` dizini bile yok, CLAUDE.md "38 test" yalanı, migrations forward-only, canary/feature-flag yok, smoke test brand-drifted |

🟢 launch-uyumlu / 🟡 launch-öncesi 1-2 gün / 🟠 launch-öncesi 1 hafta / 🔴 launch-öncesi 2-3 hafta

> *İstisna:* "Bilimsel Disiplin / Akademik Kalite" ekseni **lansman-blocker değildir** — production launch'ı engellemez. Fundraising / kurumsal müşteri kıyas talep süreci ve scientific credibility için takip iştir. Severity tag'leri Eksen 6 başında ayrı bir trichotomy ile tanımlıdır (`[YAYIN-SEVİYESİ]` / `[ENDÜSTRİ-SEVİYESİ]` / `[v0-KABUL]`).

---

## Critical Blocker'lar — Üretim Lansmanından Önce (top 25)

Sıralama önem × ortalama düzeltme süresi.

### Altyapı / DevOps (acil)

1. **CI/CD pipeline yok** — `.github/workflows/` boş. Hiçbir commit otomatik test edilmedi, image build edilmedi. — *fix: 1 gün*
2. **k8s/Helm manifest yok** — production deploy tarifi yazılmamış. — *fix: 2-3 gün*
3. **`/ready` distinct from `/health` yok** — autoscaler cold pod'a trafik route ediyor, k8s'in kararı yanlış. ([src/server/main.py:362-379](../../src/server/main.py#L362-L379)) — *fix: 1 saat*
4. **Worker Dockerfile CUDA base'siz** — `python:3.12-slim` üzerinde torch.cuda import fail eder. ([deploy/worker.Dockerfile:3](../../deploy/worker.Dockerfile#L3)) — *fix: 30 dk*
5. **Secret store yok** — JWT key + R2 cred + DB pwd plain env. Compromise → tüm production. — *fix: 1 gün karar + 2 gün wiring (ExternalSecrets + cloud secret manager)*
6. **Postgres backup yok** — tek corruption şirketi bitirir. — *fix: 1 gün (managed Postgres + PITR seçimi)*
7. **R2 lifecycle/versioning unspecified** — `tts-outputs/` sınırsız büyür, voice-refs/ silinirse geri yok. — *fix: ½ gün*

### Mimari (acil)

8. **Reference audio `file://` local disk'te** — gateway 2 replica olunca pod-A'da enroll edilen voice pod-B'den 404 döner. ([src/server/main.py:672-683](../../src/server/main.py#L672-L683)) — *fix: 1 gün (R2 upload path)*
9. **Worker warmup cross-tenant** — `NEUROVOICE_WORKER_WARMUP_VOICES` tenant filtresiz Voice.voice_id ile sorgular. Tenant A'nın `foo` LoRA adapter'ını process-wide cache'e yükler. ([src/worker/runtime.py:148-156](../../src/worker/runtime.py#L148-L156)) — *fix: 2 saat (fully-qualified `tenant_id:voice_id` zorunlu)*
10. **Migration 0012 DROP+ADD** — bir sonraki staging DB'de populated `watermark_key_id` varsa sessiz nuke. ([migrations/.../0012_watermark_generation.py:103](../../migrations/versions/2026_05_28_0012_watermark_generation.py#L103)) — *fix: ½ gün (guard ekle)*

### Security (acil)

11. **JWT key rotation yok, family revocation TODO** — leaked refresh cookie 7 gün geçerli. ([src/server/security/jwt_tokens.py:44-65](../../src/server/security/jwt_tokens.py#L44-L65)) — *fix: 1 gün*
12. **Admin login brute-force korumasız** — argon2id 50-100 ms × keepalive = ~10 attempt/s. ([src/server/admin/router.py:110-142](../../src/server/admin/router.py#L110-L142)) — *fix: 2 saat*
13. **API key never expires** — `ApiKey.expires_at` field yok, SOC2/ISO27001 baseline ihlali. ([src/db/models.py:178-224](../../src/db/models.py#L178-L224)) — *fix: 1 saat schema + 1 gün rotation runbook*
14. **Audit log integrity DB-layer korumasız** — app role'ü UPDATE/DELETE yapabilir, hash chain yok. ([src/db/models.py:567-607](../../src/db/models.py#L567-L607)) — *fix: ½ gün (ayrı role + INSERT-only grant)*
15. **GDPR madde 15 (data export) hiç yok** — sadece silme var (ADR-11). EU SAR talepleri 30 günlük SLA. — *fix: 2 gün (operator inbox + R2 bundle)*

### Observability (acil)

16. **Dashboard saturation panel yanlış metric'i sorguluyor** — `nqai_queue_depth` rename'i sonrası `deploy/grafana/.../neurovoice.json` + `deploy/prometheus/alerts.yml` güncellenmedi. Tek en önemli saturation alert sessizce ateşlenmiyor. ([deploy/prometheus/alerts.yml:65](../../deploy/prometheus/alerts.yml#L65), [deploy/grafana/dashboards/neurovoice.json:107](../../deploy/grafana/dashboards/neurovoice.json#L107)) — *fix: 5 dk*
17. **request_id propagation yok** — gateway → Redis Streams → worker hiçbir log satırı correlate olmuyor. — *fix: 1 gün (contextvars + JSON logger)*
18. **OpenTelemetry yok / Sentry yok** — 38 logger.exception sitesi stdout'a düşer, fingerprinting yok. — *fix: 1 gün (env-gated OTLP + Sentry DSN)*

### API / SDK (lansman öncesi)

19. **ElevenLabs SDK düşmez** — gateway `xi-api-key` header okumaz, sadece `Authorization`. ([src/server/auth.py:155](../../src/server/auth.py#L155)) — *fix: 2 saat*
20. **language="tr" hard-literal** — multilingual platformda Turkish-only kalıntı, ElevenLabs SDK `en` gönderince 422. ([src/server/schemas.py:110,166](../../src/server/schemas.py#L110)) — *fix: 30 dk*
21. **Error envelope drift** — 88 `HTTPException(detail=...)` sitesi `{detail}` döndürür, ADR-9 `{error, detail}` vaadi yalan. — *fix: 1 gün (global exception_handler + error code catalog)*
22. **Sunset middleware sadece `/v1/tts`** — `/v1/tts/stream` + parity alias `/v1/text-to-speech/{voice_id}` deprecation header almıyor. ElevenLabs SDK kullanıcısı sunset cliff'i hiç görmez. ([src/server/main.py:312](../../src/server/main.py#L312)) — *fix: 30 dk*
23. **vendors/elevenlabs/openapi.yaml yok** — parity contract test koşmuyor, ADR-9 enforcement kâğıt. — *fix: 1 gün*

### Testing (lansman öncesi)

24. **`tests/` dizini hiç yok, CLAUDE.md yalan söylüyor** — "38 test, testcontainers'lı integration" iddiası yanlış. `pytest` exit 5 verir (collected 0 items). — *fix: CLAUDE.md düzelt (5 dk) + Codex test paketi (paralel)*
25. **Migrations forward-only, rollback prosedürü yok** — 13 migration'ın hepsi `NotImplementedError` döner. v0.5 bozulursa v0.4'e dönüş yolu yazılı değil. — *fix: 2 saat (rollback policy yaz)*

---

## Mimari Güçlü Yanlar — Korumamız Gereken

Acımasız olalım ama haksız da olmayalım. Aşağıdakiler **gerçekten iyi mühendislik** ve TDD sırasında öne çıkarılması gereken şeyler:

- **Gateway/worker ayrımı gerçek.** In-process inference leak yok ([src/server/main.py:133-136](../../src/server/main.py#L133-L136)). CLAUDE.md nirengi #1 koruma.
- **Idempotency disiplini.** `IdempotencyRepo.reserve_or_get()` race-fix doğru kurulu, body-hash conflict semantik şart koşuluyor. ([src/repos/idempotency.py:43-83](../../src/repos/idempotency.py#L43-L83))
- **XACK matrix.** PoisonJob → XACK + DLQ, TransientFailure → no-XACK + XAUTOCLAIM, max-retries → terminal DLQ. Doğru ayrılmış. ([src/worker/consumer.py](../../src/worker/consumer.py))
- **commit-before-final disiplini.** Worker pipeline'da `publish_final → idempotency.complete` ordering "final=True ⇒ GET returns audio_url" invariant'ı doğru veriyor.
- **D-08 tenant filter.** `VoiceRepo.get_accessible` üç dalda (owned/shared/public) tenant scope'u koruyor. Generic 404 (not 403) existence-leak'i engelliyor.
- **Reproducibility audit trail.** engine_inputs (seed, cfg, timesteps, model_id, adapter_id, hf_revision) usage_records.engine_inputs JSONB'sine yazılıyor. ([src/worker/pipeline.py:654-672](../../src/worker/pipeline.py#L654-L672))
- **Voice cloning legal posture trinity.** ADR-10 (license + consent) + ADR-11 (lifecycle + RTBF) + ADR-13 (watermark) içsel olarak tutarlı.
- **Eval bench seçimi doğru.** WER+CER (intelligibility) + UTMOSv2 (naturalness) + SECS (clone fidelity) = 2024+ literatür standardı *(metrik seçimi literatür-uyumlu; coverage + istatistiksel rigor + on-disk evidence Eksen 6'da ayrı denetlenir)*.
- **R2 download race fix.** PID+UUID `.part` + per-URI lock — concurrent worker download disiplinli.
- **Audio postprocess.** 80ms gap + 4ms cosine cross-fade (Quality hotfix A.1), LUFS/DC/peak postprocess — sentence boundary click-free.
- **Argon2id + sliding-window rate limit.** Constant-time compare, per-key + per-tenant katmanlı, generic 401 message — auth pipeline iyi şekilde.
- **Capacity-aware admission.** Worker heartbeat + `WORKER_INFLIGHT` + `QUEUE_DEPTH` → gateway backpressure decision. ([src/server/heartbeat.py](../../src/server/heartbeat.py))

Bu liste TDD sunumunda "biz neyi iyi yaptık" cevabı.

---

## Eksenler — Detaylı Bulgular

### 1. DevOps / Deployment (🔴 Kırmızı)

**Şu an ne var:** İki Dockerfile (gateway + worker), dev compose, pgbouncer overlay, Prometheus + Grafana + alert kuralları.

**Eksik / yanlış:**
- CI/CD yok (`.github/` boş)
- k8s/Helm/Terraform yok
- `/ready` yok, sadece `/health` (her zaman OK döner)
- Worker Dockerfile CUDA base'siz (`python:3.12-slim`)
- JWT/R2/DB secret'lar plain env
- Postgres backup runbook yok
- R2 lifecycle policy yok
- KEDA/HPA yok — autoscale sinyali metric'lerde var ama hiçbir consumer yok
- Image registry yok, signed image yok (`cosign`, SBOM)
- Worker `terminationGracePeriodSeconds` < uzun-form sentez süresi → SIGKILL riski
- CORS `*` boot'ta refuse etmiyor

**Lansman katastrofu senaryosu:** Tenant voice enroll eder. Gateway 1 → 2 replica scale. Pod 2 her sentez'de 404 döner çünkü reference WAV pod 1'in local disk'inde (`reference_uri=file://...`).

**Düzeltme planı:** Helm chart + KEDA ScaledObject + PDB + worker CUDA Dockerfile + ExternalSecrets + managed Postgres + R2 lifecycle Terraform — 1 mühendis × 1 hafta.

### 2. Observability / SRE (🔴 Kırmızı)

**Şu an ne var:** Latency waterfall histogram'ları (queue wait, worker pickup, first-PCM, first-audio, gateway TTFB, inference), output quality histogram'ları (RMS / silence / clipping / LUFS), heartbeat-based liveness, 11 Prometheus alert kuralı.

**Eksik / yanlış:**
- **Saturation alert + dashboard panel `nqai_queue_depth` sorguluyor** — rename'den sonra güncellenmedi. En önemli alert sessizce kapalı. **5 dk fix.**
- request_id propagation yok (gateway → queue → worker)
- OpenTelemetry yok, Sentry yok
- Plain text logging (`logging.basicConfig`), JSON yok
- `/health` her zaman OK, dependency check etmiyor
- Rate-limit Prometheus counter yok (sadece audit log'a yazılıyor)
- Cluster gauges sadece gateway scrape edilir — gateway down olunca queue_depth gauge'i kaybolur
- Metric cardinality plan 100k tenant'a dayanmaz (latency histogram × tenant × voice × 10 bucket = 720M time series)
- `auth.success` her request audit ediliyor — audit log = request log abuse. 100 RPS × 86400 sec = 8.6M row/day.
- audit_log retention/partition yok
- Per-tenant cost telemetry yok (character_total, gpu_seconds)
- DLQ depth metric'di ama operator endpoint/replay tool yok
- Runbook'lar yok (`docs/runbooks/` dizini hiç yok)
- SLO yazılı değil

**On-call verdict:** 03:00'te "API down" DM'inde on-call mühendisi `/health` (yalan), Grafana dashboard'u (boş paneller), stdout log'lar (request_id'siz, aggregator'sız). 20+ dk grep + tahmin. **Şu an localize <5 dk YAPILAMAZ.**

### 3. Database / Storage (🟠 Turuncu)

**Şu an ne var:** 13 alembic migration linear chain, voice/license/consent/lifecycle schema iyi, ON DELETE policy'leri explicit (`RESTRICT` on usage_records — finansal correctness), R2 download race fix doğru, pgBouncer transaction mode + statement_cache=0 (asyncpg uyumlu), idempotency body-hash conflict.

**Eksik / yanlış:**
- **Postgres backup strategy yok** (RPO/RTO undocumented)
- **R2 per-tenant prefix yok** — `tts-outputs/{date}/{rid}.wav`, tenant_id key'de değil. Bucket policy ile tenant izolasyonu imkansız
- **R2 versioning / lifecycle yok**
- **Idempotency sweeper yok** — `purge_expired` method var, çağıran yok. 24h TTL yalan. At 1M req/day = 1M row/day birikir
- **usage_records + audit_log partition yok** — 30M row/month projection
- **TLS enforcement yok** — `sslmode=require` boot'ta zorlanmıyor
- **Redis AOF yok** — restart'ta in-flight job'lar + result chunk'lar uçar
- **DLQ replay tool yok**
- **Migration 0012 DROP+ADD** — non-empty DB'de sessiz data loss
- **Billing index yok** — `(tenant_id, status, occurred_at)` aggregation için yok
- **Some columns freeform Text** — `usage_records.status/error_code`, `audit_log.action` — CHECK ya da FK yok

**10x traffic verdict:** İlk: idempotency table 2.4M row → indeks degrade. İkinci: usage_records autovacuum lag. Üçüncü: Redis restart → in-flight uçar. **Backup'sızlık var-olmaz risk.**

### 4. Worker / Queue / GPU (🟠 Turuncu)

**Şu an ne var:** XACK matrix doğru, capacity heartbeat, R2 download cache locked, model_id + revision pinned per worker, LoRA cache LRU eviction + Prometheus metrics.

**Eksik / yanlış:**
- **Warmup cross-tenant okuma** — fully-qualified `tenant_id:voice_id` zorunlu değil
- **Idempotency `processing` zombie** — gateway crash → row stuck → tenant new key alana kadar bloke
- **GPU OOM cold-load fatal** — `OutOfMemoryError` retry yok, sadece DLQ
- **AudioSeal + VoxCPM2 aynı CUDA device** — `LORA_CACHE_SIZE=3` artık doğru değil (AudioSeal ~1.5 GB)
- **AudioSeal asyncio.to_thread concurrent forward UNSAFE** — `WORKER_CAPACITY > 1` olunca undefined behavior
- **NEUROVOICE_WORKER_WARMUP_VOICES single global env** — autoscale'da yanlış semantik
- **DLQ operator surface yok**
- **Per-job inference timeout yok** — degenerate prompt stuck-loop → worker zombie
- **mark_terminal_failure session ordering bozuk** — error chunk önce, commit sonra
- **WORKER_MODEL_INFO shutdown'da clear edilmiyor** — rolling deploy zombi metric label'lar
- **engine_inputs_snapshot watermark_message_bits eksik** — reproducibility eksik
- **Worker readiness probe yok** — k8s warm-up sırasında trafik yönlendirir
- **Sync TTFB timeout 30s** — uzun-form sync legitimate 30+ saniye olabilir → orphan artifact

**30% worker crash recovery verdict:** Heartbeat keys 3s'de expire, capacity-aware admission tightens, in-flight PEL XAUTOCLAIM 30s'de tetiklenir, sweep 1/tick, ~3-5 dk straggler recovery. **Plus** cold-load fanout (long tail uncached) +30-60s per voice. **Toplam 4-7 dk steady-state.** DLQ otomatik drain etmez; operator endpoint yok → `redis-cli` ile manuel.

### 5. ML Pipeline / Model Lifecycle — Mühendislik (🟠 Turuncu)

> *Bu eksen ML **mühendisliği** substrate'ini (model registry, drift detection, eval-gate wiring, supply chain) denetler. Bilimsel disiplin / akademik kıyas — test seti büyüklüğü, SOTA karşılaştırma, datasheet ve reference circularity gibi yayın-grade gap'ler — Eksen 6'da ayrı ele alınmıştır.*


**Şu an ne var:** Reproducibility audit trail (engine_inputs) doğru, LoRA cache LRU + metric'ler, eval harness Whisper-WER+CER + UTMOSv2 + SECS, fine-tune ADR-8 ile CLI sarması var, voice manifest schema v2.

**Eksik / yanlış:**
- **Model registry yok** — checkpoints filesystem'de YAML manifest + R2 ad-hoc. `voices.adapter_uri` + `adapter_sha256` columns var ama download/verify path yok
- **Eval promotion gate kapalı** — `release_status='production'` `eval_metrics IS NOT NULL` zorlamıyor
- **VoxCPM2 drift detection yok** — `hf_revision` mismatch sadece WARN, refuse değil
- **Fine-tune pipeline subprocess sarmasıymış** — gerçek SGD loop upstream VoxCPM repo'da; end-to-end test imkansız
- **ML supply chain unbounded** — `audioseal>=0.2` ceiling yok, `openai-whisper>=20240930` ceiling yok, UTMOSv2 git HEAD-install, no `uv.lock`
- **Inference NOT byte-deterministic** — `torch.use_deterministic_algorithms` set edilmemiş, bf16 cast non-stable. "Reproducible" değil "auditable inputs"
- **Watermark round-trip test yok** — embed→detect bit assertion CI'da yok
- **engine_inputs adapter_uri/sha256 eksik** — iki voice aynı preset_id, farklı LoRA, audit aynı
- **A/B framework yok, canary tier yok, traffic split yok**
- **Drift re-eval cron yok**

**VoxCPM2 breaking-change verdict:** Operator'ün roll-back yolu **yok**. `NEUROVOICE_MODEL_HF_REVISION` env var tek; registry yok; canary tier `release_status` enum'unda yok; otomatik drift re-eval yok. Operator log okur, env reverse eder, worker restart eder, cache LoRA'sının invalidate olmadığına dua eder. **Repo'daki en yüksek üretim riski.**

### 6. Bilimsel Disiplin / Araştırma Kalitesi — Akademik Mercek (🟠 Turuncu)

> *Yayın-seviyesi TTS literatürü (Seed-TTS 2024, F5-TTS 2024, MaskGCT 2024, NaturalSpeech 3, VALL-E 2) standartlarına karşı iç kıyas.*

> **Açılış kayıt:** Bu bölüm v0 startup'ını 2026 yayın-grade TTS literatürü disiplinine karşı tartar. Yayın-grade olmak v0 ship için **şart değil**; ama yatırımcı ML danışmanının zihninde olan check-list bu. Honest gap surfacing TDD'de surprise eliminate eder. Eksen 5 (ML mühendisliği) ile bu eksen (bilimsel disiplin) farklı katmanlardır — Eksen 5'in gap'leri *altyapı*, bu bölümün gap'leri *kanıt ve coverage*.

**Severity scheme (engineering eksenlerinden ayrı):**

- **`[YAYIN-SEVİYESİ]`** — Peer-reviewed TTS paper submission'ı engelleyen gap. Investor ML advisor specifically check eder. Düzeltmesi araştırma-grade iş.
- **`[ENDÜSTRİ-SEVİYESİ]`** — Ciddi enterprise vendor evaluation'ı geçemeyecek gap; research preprint için kabul edilebilir.
- **`[v0-KABUL]`** — Bilinen sınırlama, yazılı policy ile v0 dışına itilmiş; honest acknowledgement.

#### Şu an ne var (akademik mercek doğru-olanlar)

Methodology açısından **doğru** kararlar:

- **3-eksen metric seçimi literatür-uyumlu.** Intelligibility (Whisper-WER + CER), Naturalness (UTMOSv2), Clone fidelity (SECS via WavLM-base-plus-sv) — 2024+ TTS paper'larında (Seed-TTS, F5-TTS, MaskGCT, NaturalSpeech 3) standart obje tive bench seti. Wrong-metric değil; under-evidence.
- **`voices.eval_metrics` JSONB schema reproducibility-shaped** (ADR-12 §4 payload). schema_version, evaluated_at, test_set, model metadata, per-metric aggregation — yayın-grade pin payload formatı.
- **Voice manifest v2 forward-shape fields** (ADR-7): `base_model_id`, `adapter`, `lexicon`, `watermark`, `eval_pin`. Schema model card path'i açık.
- **`engine_inputs` audit trail JSONB** (`usage_records.engine_inputs`): seed, cfg_value, inference_timesteps, model_id, hf_revision, preset_id per job persist edilmiş. Engineering reproducibility envelope canlı.
- **Whisper-CER metric'in agglutinative dil için kullanılması** (ADR-12 docstring) Türkçe pratik için doğru — `WhisperCERMetric` `shared_metric=` pattern'iyle model'i 2 kez yüklemiyor.

Açılış paragrafı bunu yatırımcıya söylemek için: çerçeve seçimi state-of-the-art, gap *evidence* ve *coverage*'da.

#### Yayın standardına göre eksiklikler

13 spesifik bulgu:

1. **Test seti büyüklüğü literatürün altında (60 vs 100-1000+)** — `[YAYIN-SEVİYESİ]`
   *Evidence:* `data/test-sets/v0.2-medium.md` 60 TR cümle, `v0.1-mini.md` 10. Seed-TTS / F5-TTS / NaturalSpeech 3 test setleri 200-1000+ cümle.
   *Why-matters:* 60-cümlelik örnek %95 güven aralığı ±2-3 puan WER. Tek-cümle anomalisi metrik'i sallar. Yatırımcı ML danışmanı confidence interval sorar.
   *Action:* v0.3-large test seti 300 cümle hedefi (TR + EN + 1 ek dil), Hafta 5-8 milestone'da.

2. **Tek-ses bench — multi-speaker akademik standart değil** — `[YAYIN-SEVİYESİ]`
   *Evidence:* SECS runner per-voice metric binding; sample voice tek (`tr-warm-storyteller-v0`). F5-TTS LibriTTS test'i 39 speaker, XTTS paper 50+.
   *Why-matters:* Klonlama platformuyuz iddiası tek-örneğe dayalı. Distribution shift unverified.
   *Action:* 5-voice mini-eval seti (3 kadın / 2 erkek, 3 yaş bandı), Hafta 5-7 talent recording'le birlikte.

3. **"Multilingual TTS" iddiası + TR-only eval seti — markaj çelişkisi** — `[YAYIN-SEVİYESİ]`
   *Evidence:* `pyproject.toml:8` description: "multilingual TTS API platform"; `src/server/schemas.py:110,166` `language: Literal["tr"]` (Eksen 7 cross-ref). Test setlerinde non-TR yok.
   *Why-matters:* ML danışmanı 2 dakikada "language coverage claim ≠ language coverage evidence" yakalar. Pazarlama-dili kanıtsız.
   *Action:* Ya iddia daralt ("Turkish-first, multilingual on roadmap"), ya minimum EN+TR eval seti (her dilde 100 cümle), Hafta 3.

4. **SOTA akademik kıyas (XTTS / F5-TTS / Seed-TTS) yok — sadece ElevenLabs** — `[YAYIN-SEVİYESİ]`
   *Evidence:* Bench harness `src/eval/systems/` altında `neurovoice.py` + `elevenlabs.py`. XTTS, F5-TTS, OpenVoice, Bark, Seed-TTS karşılaştırma satırları yok.
   *Why-matters:* ElevenLabs ticari baseline; akademik kıyas için açık-ağırlıklı (XTTS, F5-TTS, OpenVoice) modeller standart. "Bizim WER X" karşılaştırma sütunu olmadan boş.
   *Action:* En az F5-TTS + XTTS satırlarını test setine ekle (ikisi de açık model, RunPod'da koşar), Hafta 4.

5. **Bootstrap 95% CI yok — sadece mean + p95** — `[YAYIN-SEVİYESİ]`
   *Evidence:* `src/eval/runner.py` `_aggregate` mean + p95 raporluyor. Bootstrap CI hesaplama yok.
   *Why-matters:* 60-cümlelik set'te %95 CI ±3 puan WER. Tek-skor raporu istatistiksel olarak boş.
   *Action:* Bootstrap 1000-resample CI runner.py'a eklenir (1 gün iş), Hafta 3.

6. **MOS / MUSHRA insan panel deferral'ı yazılı değil** — `[v0-KABUL eğer yazılı, YAYIN-SEVİYESİ eğer değil]`
   *Evidence:* UTMOSv2 var (estimated MOS), human panel hiç yok. ADR'lerde de yazılı deferral'ı yok.
   *Why-matters:* UTMOSv2 ≠ MOS. Yayın-grade TTS paper insan paneli ister. v0 için gerekçe meşru (kost, 1.5h × 5-12 panelist) ama yazılı policy yok → "unilaterally skipped" görünür.
   *Action:* MOS panel deferral'ı `docs/research/eval-roadmap.md` veya yeni ADR-14'te yazılı hale getir, hedef tarih ver (örn v0.6 quality milestone).

7. **MODEL_CARD.md / datasheet-for-datasets yok** — `[ENDÜSTRİ-SEVİYESİ]`
   *Evidence:* Repo grep — sıfır model card, sıfır datasheet. Gebru et al. 2018 datasheet template, Mitchell et al. 2019 model card 2024+ standart.
   *Why-matters:* Kurumsal müşteri (özellikle EU public sector, finans, healthcare) model card şart koşar. Olmaması "scientific discipline yok" sinyali.
   *Action:* ADR-14 olarak Model Card v1 + Dataset Datasheet v1 iskeleti — Hafta 3-4. Sample voice için doldur (NULL field'lar açık deklare).

8. **Reference audio circular: ElevenLabs çıktısı sample voice baseline'ımızı tanımlıyor** — `[YAYIN-SEVİYESİ]` (kendine subsection §4)
   En kırılgan bulgu — aşağıda ayrı alt-bölüm.

9. **Inference byte-deterministic değil — "reproducible" ≠ replicable** — `[ENDÜSTRİ-SEVİYESİ]`
   *Evidence:* `src/worker/engine.py:531-541` `torch.manual_seed` best-effort, ama `torch.use_deterministic_algorithms` set edilmemiş, bf16 cast non-stable, cuDNN nondeterminism, `hf_revision="main"` default.
   *Why-matters:* `engine_inputs` audit trail "aynı input → aynı output" garantilemiyor. ML danışmanı `seed=42 cfg=2.0` ile iki kez koştuğunda bit-farklı çıktı görürse "audit theatre" der.
   *Action:* (a) `NEUROVOICE_DETERMINISTIC=1` env flag (perf tradeoff'la), (b) `hf_revision` default'u SHA-pinned değere çek, (c) replay script `scripts/replay_engine_inputs.py`. Hafta 3.

10. **`uv.lock` / `requirements.lock` yok — peer-replicable environment imkansız** — `[ENDÜSTRİ-SEVİYESİ]`
    *Evidence:* `pyproject.toml`'de range'ler var (`torch>=2.5,<2.6`), lock dosyası yok. Pyproject yorumu (`pyproject.toml:59-64`) "Production should generate a lockfile" diyor — yapılmamış.
    *Why-matters:* Akademik peer "kodu indirdim, environment kurdum, sayılar tutmadı" der. Bu en hızlı credibility loss yolu. SBOM yok.
    *Action:* `uv lock` üret + CI'da `uv sync --frozen` zorunlu. Hafta 2 (CI ile aynı sprint).

11. **`audioseal>=0.2` unbounded ceiling — published metrics yarın anlamsız** — `[ENDÜSTRİ-SEVİYESİ]`
    *Evidence:* `pyproject.toml:107` `audioseal>=0.2` (Eksen 5'te de işaretli).
    *Why-matters:* AudioSeal 0.3 release algoritma değiştirirse watermark detection skorları farklı olur. Yayınlanmış sayı eski referans kalır.
    *Action:* `audioseal>=0.2,<0.3` ceiling + lock. Hafta 0 (5 dk fix).

12. **SGD loop in-repo değil — training reproducibility audit imkansız** — `[ENDÜSTRİ-SEVİYESİ]`
    *Evidence:* `src/finetune/train.py:50` `subprocess.run(cmd, cwd=str(voxcpm_repo))` — gerçek loss / optimizer / scheduler upstream `voxcpm` repo'sundaki `train_voxcpm_finetune.py`'da. LoRA hyperparams (r=32, alpha=32, dropout=0.0) `config.py:62-71` görünür ama eğitim disiplininin geri kalanı bu repo'da değil.
    *Why-matters:* ML danışmanı "loss curve göster" deyince upstream'e gönderemeyiz. Reproducibility incomplete.
    *Action:* (a) Upstream `train_voxcpm_finetune.py` SHA pin et + vendored kopya `vendors/voxcpm-training/` ekle (read-only mirror, license check), VEYA (b) ADR'de "training loop upstream'de" yazılı kabul + upstream SHA + diff log policy. Hafta 4.

13. **On-disk benchmark çıktısı SIFIR — `experiments/` boş, REPORT.md yok, sample voice `eval_pin` NULL** — `[YAYIN-SEVİYESİ]` (en düşük cost / en yüksek credibility-restore)
    *Evidence:* `experiments/2026-05-19-voxcpm2-baseline/output/` empty. `experiments/2026-05-25-eval-harness-scaffold/` README scaffolding only. `configs/voices/tr-warm-storyteller-v0.yaml:eval_pin` NULL.
    *Why-matters:* **Bütün eval infrastructure çalışıyor ama hiç koşmamış.** ML danışmanı `eval/` repo'sunu açar, harness'ı görür, sayı arar, hiçbiri yok → "eval theatre" kanısı. En düşük cost / en yüksek credibility-restore action.
    *Action:* Talent recording bekleyemez — şu anki vendor-türev reference ile bile bir baseline REPORT.md üret (kendine işaretli "interim, vendor-derived reference, will be re-run post-talent-recording"). Hafta 1.

#### Reproducibility — mühendislik vs bilim ayrımı

Reproducibility tek bir kavram değil; 3 ayrı katman var ve repo bunlardan birinde **var**, ikisinde **yok**:

- **(a) Auditable inputs** (`engine_inputs` JSONB `usage_records.engine_inputs`): seed, cfg, timesteps, model_id, hf_revision, preset_id per job persist ediliyor. ✅ **VAR.** Mühendislik audit trail tamam — bu yeterli "ne koştuğumuzu yazıyoruz" garantisi için.
- **(b) Byte-deterministic replay** (aynı input → aynı PCM): bf16 cast, cuDNN nondeterminism, `torch.use_deterministic_algorithms` set değil. ❌ **YOK.** Aynı seed iki ayrı çalıştırmada perceptually identical ama byte-farklı çıktı verir.
- **(c) Peer-replicable environment** (third party'nin aynı sonucu üretebilmesi): `uv.lock` yok, `hf_revision="main"` default, SGD loop upstream'de. ❌ **YOK.** Akademik peer review yapamaz.

Akademik mercek **üçünün hepsini** ister. Bizde sadece (a) var. Dürüst dil: "auditable inputs" diyebiliriz, "reproducible inference" diyemeyiz.

#### Reference audio circularity riski

**En kırılgan bulgu.** `data/reference-audio/MANIFEST.md`'de açıkça yazılı: sample voice `tr-warm-storyteller-v0`'ın referans audio'su **ElevenLabs `eleven_multilingual_v2` modelinin çıktısı** (NEEKO_V0.1_TR_NeutralGuide preset, Speed 0.86 / Stability 50 / Similarity Boost 75 / Style 0 / Speaker Boost active). Manifest gri-bölge ToS uyarısı içeriyor: "No LoRA fine-tune data generation from this file; reference ONLY".

Sonuçları akademik mercek altında:

1. **Hedef ses = ElevenLabs sesi** → SECS skorumuz "ElevenLabs'a ne kadar yakınız"ı ölçer; "doğal insan sesine ne kadar yakınız"ı değil. Metrik mantıksal olarak circular: vendor X'in çıktısını hedef alıyoruz, vendor X'i baseline olarak karşılaştırıyoruz.
2. **ElevenLabs ToS gri bölge** — TDD'de "bu reference audio ToS-compliant mı?" sorulur. Manifest'te yazılı uyarı var ama hukuki posture netleşmedi.
3. **Talent recording (Hafta 5-7 deferred) gelmeden bench yayınlanmamalı** — herhangi bir public sample release ya da arXiv tech report tabanını çürütür.

**Öneri:** Talent recording ASAP. O zamana dek (a) interim REPORT.md üretilirse "vendor-derived reference, interim, will be re-run post-talent-recording" disclaimer'ı şart, (b) public release yok, (c) yatırımcı görüşmesinde proaktif söylenir (gizlersek incelemeci bulur ve büyütür; sahiplersek tarihçe + roadmap olur).

#### Akademik kıyas verdict

Yatırımcı ML danışmanı 1 saat sonra şu notu yazar: *"Methodologically sound — 3-axis metric choice matches 2024+ TTS publication standard, eval_pin payload schema is reproducibility-aware, manifest v2 is forward-thinking. But evidence layer is empty: test set under-sized (60 vs ~200+), single voice, monolingual under multilingual branding, no SOTA academic baseline, no bootstrap CI, no model card, no on-disk numbers, and the sample voice reference audio is vendor-generated (circular eval risk). Framework right, coverage missing. Six-week sprint to credible scientific posture; eight to publication-grade."*



**Şu an ne var:** Native async happy path (POST /v1/tts/jobs → GET /v1/tts/jobs/{id}) tamamen wired, ADR-11 lifecycle gate, idempotency body-hash, ElevenLabs URL alias'ları, Sunset header /v1/tts'de, openapi-policy.md + vendor-parity.md + versioning.md docs.

**Eksik / yanlış:**
- **ElevenLabs SDK DÜŞMEZ** — `xi-api-key` header okunmuyor, sadece `Authorization`. Vaadin yalan
- **language="tr" hard-literal** — multilingual platformda Turkish-only sızıntı. SDK `en` → 422. *Çapraz: Eksen 6 §"Eksiklikler" item 3 — aynı bug akademik mercekte "multilingual claim ≠ multilingual eval" markaj çelişkisi olarak da işaretlendi.*
- **vendors/elevenlabs/openapi.yaml pinned değil** — contract test kâğıt
- **`tests/snapshots/openapi.json` yok** — ADR-9 drift control fiction
- **Error envelope drift** — 88 HTTPException sitesi `{detail}` döner, ADR-9 `{error, detail}` vaadi yalan. Pydantic SDK auto-cast `AttributeError`
- **`X-RateLimit-*` headers yok** — sadece `Retry-After` 429'da. SDK pacing yapamaz
- **Pagination offset-based + tüm dataset Python'a load** — keyset cursor değil. Catalog büyürse degrade
- **WebSocket close codes documented değil**
- **WebSocket idle timeout 20s heartbeat'siz** — Cloudflare/ALB proxy 60-100s'de drop. Reconnect doc'u yok
- **Sunset middleware sadece `/v1/tts` exact match** — `/stream` + parity alias coverage yok
- **`GET /v1/usage` yok** — tenant character count sorgulayamaz
- **Monthly quota yok** — sadece per-minute rate limit
- **Cost surface yok** — billing visibility tenant tarafında sıfır
- **CORS `PATCH` allow_methods'da yok** — PATCH /v1/voices/{id} cross-origin fail
- **`docs/api/errors.md`, `idempotency.md`, `authentication.md`, `rate-limits.md`, `websocket.md` yok**

**1-saat external developer verdict:** curl smoke 10 dk'da yeşil. ElevenLabs Python SDK 15. dakikada duvar — `xi-api-key` 401. Manuel header override sonrası `language="en"` → 422. `/openapi.json` `ErrorResponse` deklare ediyor ama wire `{detail}` döner → spec yalan. 45. dakikada source code okur. 60. dakikada "are you actually multilingual?" issue açar — en kötü drift sinyali. **İlk friction: 15. dk.**

### 8. Security + Multi-tenant + Compliance (🟠 Turuncu)

**Şu an ne var:** D-08 tenant filter disiplinli, argon2id constant-time, prefix-indexed lookup, sliding-window rate limit, generic 401, admin cookie httponly+secure+samesite=strict, idempotency tenant_id invariant, R2 race fix, VoiceRepo accessibility 404-not-403, generic auth error.

**Eksik / yanlış (CRITICAL):**
- **logger NameError cascade endpoint'inde** ([admin/router.py:1123](../../src/server/admin/router.py#L1123)) — bu doküman yazılırken düzeltildi
- **Admin login brute-force korumasız**
- **JWT key rotation yok, `kid` claim yok**
- **Refresh token family unimplemented** — docstring vaat, kod TODO
- **API keys never expires**
- **Audit log integrity DB-layer yok** — app role UPDATE/DELETE yapabilir, hash chain yok
- **GDPR madde 15 (data export) yok**
- **R2 presigned 1h TTL leak-vector** — output audio + evidence URI'ler
- **`evidence_uri` raw string from tenant** — SSRF + storage poisoning
- **Voice cloning consent fraud mechanically unbounded** — sadece tickbox
- **`uuid.uuid4()` sentinel** 8 admin site'ta — future-refactor footgun
- **`VoiceConsentRecordRepo` not tenant-scoped** — repo contract'ı zorlamıyor
- **R2 secrets plain env, no Vault**
- **Security headers yok** (HSTS, CSP, X-Frame-Options, nosniff, Referrer-Policy)
- **Admin no IP allowlist, no 2FA**
- **CSRF token Form POST endpoint'lerde yok**
- **ProxyHeadersMiddleware yok** — `request.client.host` = LB IP
- **JWT HS256 (HMAC), `aud` claim yok**
- **`voice_access` grant'lar expiration'sız**
- **Dependency vulnerability scanning yok** (`pip-audit`, dependabot)
- **PII classification document yok**

**Free-trial researcher verdict:**
- (a) **Başka tenant voice okuma** — düşük risk. D-08 sağlam, SQL injection surface yok.
- (b) **Audit log okuma** — surface'te yok. SQL erişimi olursa rows mutable, ama bugün ulaşamaz.
- (c) **DoS** — kısmî. Per-tenant rate-limit 600/min, ama per-IP yok. R2 cache + voice count yok, birkaç bin voice enroll edilebilir.
- (d) **PII error message leak** — düşük. Generic 401, exception type only, no API key plaintext.

**Net:** 3 critical (logger NameError DÜZELTİLDİ + brute-force + JWT rotation) + GDPR madde 15 gap. Multi-tenant izolasyon disiplini sağlam.

### 9. Testing / Release Engineering (🔴 Kırmızı)

**Şu an ne var:** `pyproject.toml` dev deps (testcontainers, pytest-asyncio, fakeredis, moto) declared, `scripts/smoke_test.py` + `scripts/load_bench.py` + `scripts/latency_bench.py` yazılmış ve çalışıyor, ruff configured.

**Eksik / yanlış:**
- **`tests/` dizini YOK** — CLAUDE.md "38 test, testcontainers'lı integration" iddiası **yanlış** (b537311 surgical reset baseline drop'larında silindi, CLAUDE.md güncellenmedi)
- **`pytest` exit 5 — collected 0 items**
- **`.github/workflows/` YOK**
- **Mypy unconfigured**
- **Pre-commit hook yok**
- **`tests/snapshots/openapi.json` YOK** — ADR-9 drift control fiction
- **Vendor parity contract test YOK**
- **All 13 migrations forward-only** — rollback prosedürü yok
- **Canary / blue-green / feature flag yok**
- **Smoke test (`smoke_test.py`) NEEKO-era Turkish child-directed sentences** — drift sızıntısı operator-facing
- **`data/reference-audio/neeko-v0.1-reference.mp3`** — ADR-7 brand-neutral rename eksik
- **`data/test-sets/v0.1-mini.md`** — "Neeko TTS Mini Test Seti" header
- **`docs/runbooks/`, `docs/release/`, `docs/architecture/`, `CHANGELOG.md`, `CONTRIBUTING.md`** — code'da REFERENCED, hiçbiri disk'te yok
- **Biggest untested-in-production surface:** ADR-13 watermark applier (+348 LOC), ADR-12 SECS (+265 LOC), admin/router.py +911 LOC son 36 saatte, migration 0013 (API key prefix rewrite)

**Investor TDD verdict:**
> İncelemeci laptop'u açar. `tests/` yok. `.github/` yok. `pytest` exit 5. ADR-9 CI snapshot iddiası — gerçek workflow yok. Migrations forward-only, rollback doc yok. Son 36 saatte 13 ADR landed — sıfır otomatik test ile. Canonical smoke "Neeko TTS Mini Test Seti." En güçlü artifact `load_bench.py` — structurally honest, tek dilli ve on-demand. **Önündeki şey:** iyi-mimari kod tabanı + test pyramid yok + CI gate yok + rollback prosedürü yok + canary yok + operator-facing smoke'da canlı drift. CLAUDE.md memory `feedback_codex_writes_tests` boşluğu intentional açıklıyor — ama "tests are being written" ≠ "tests exist." **Production kod review geçer; release-engineering review düşer.**

---

## Var-Olmaz Riskler (Existential)

3 tane gerçek var-olmaz risk:

1. **Tek Postgres corruption şirketi bitirir.** Backup stratejisi dokümante değil, restore drill yapılmamış. Managed Postgres + PITR seçimi 1 günlük karar.
2. **Reference audio local `file://`** — gateway horizontal scale imkansız, üstelik RTBF purge'unde dosya disk'te kalır. ADR-11 KVKK/GDPR vaadi başarısız.
3. **VoxCPM2 breaking change** — roll-back yolu yok. Model registry yok. Canary yok. Drift re-eval yok. Upstream maintainer'ın karar verme hızı = bizim üretim'imizin kararlılık'ı.

*Bilimsel disiplin notu:* Akademik kıyas eksiklikleri (Eksen 6) var-olmaz risk **değildir** — lansmanı blok etmez. Ama Series-A pitch deck'ine "production-quality ML" cümlesini koymadan önce kapatılmalı; aksi halde ML danışmanı sayfa açar, vacuum bulur, trust loss olur.

---

## Action Plan — Sıralı, Hafta Bazlı

### Hafta 0 (acil — 1-2 gün)

Hemen düzeltilebilir:

- [ ] `nqai_queue_depth` rename'i `alerts.yml` + `grafana/.../neurovoice.json`'da güncelle (5 dk) → en kritik alert aktif olur
- [x] `logger` NameError cascade endpoint'inde düzelt — **bu audit sırasında düzeltildi**
- [ ] CLAUDE.md "tests/ — 38 test" iddiasını yalan-noktasından düzelt
- [ ] `xi-api-key` header support `authenticate_bearer`'da ekle (2 saat) → ElevenLabs SDK düşer
- [ ] `language: Literal["tr"]` literal'ı multilingual yap (30 dk)
- [ ] `/ready` endpoint ekle DB + Redis + WORKER_COUNT check'li (1 saat)
- [ ] Worker Dockerfile NVIDIA CUDA base'e geçir (30 dk)
- [ ] Sunset middleware `/v1/tts/stream` + parity alias coverage (30 dk)
- [ ] **(Eksen 6)** `audioseal>=0.2,<0.3` ceiling — pyproject.toml (5 dk)
- [ ] **(Eksen 6)** `NEUROVOICE_MODEL_HF_REVISION` default'unu SHA-pinned değere çek; warning yerine refuse-on-`main` (10 dk)

### Hafta 1 (devam — gerçek mühendislik)

- [ ] Reference audio R2 upload path — `file://` → `s3://` migration (1 gün) → gateway horizontal scale
- [ ] Worker warmup fully-qualified `tenant_id:voice_id` (2 saat)
- [ ] Idempotency `processing` zombie sweeper (4 saat)
- [ ] Migration 0012 DROP+ADD guard (½ gün)
- [ ] Global HTTPException → ErrorResponse handler (1 gün) → ADR-9 contract gerçek olur
- [ ] Request ID propagation + JSON logger (1 gün)
- [ ] Postgres backup karar + managed servis seçimi (1 gün)
- [ ] R2 lifecycle Terraform + versioning enable (½ gün)
- [ ] Per-tenant R2 prefix migration (1 gün)
- [ ] **(Eksen 6)** Mevcut harness'ı v0.2-medium test setinde koş; interim REPORT.md üret (vendor-derived reference disclaimer'ı ile, only-internal işareti) (1 gün) → `experiments/` boşluğunu kapatır

### Hafta 2 (üretim çıkışı)

- [ ] CI workflow (`ruff` + `mypy` + `pytest` + image build + push) (1 gün)
- [ ] Helm chart + KEDA ScaledObject + PDB (2-3 gün)
- [ ] ExternalSecrets + cloud secret manager (1 gün)
- [ ] JWT key rotation + family revocation (1 gün)
- [ ] Admin login brute-force (2 saat)
- [ ] API key expires_at + rotation runbook (1 gün)
- [ ] Audit log INSERT-only role + hash chain (½ gün)
- [ ] Security headers middleware (2 saat)
- [ ] OpenTelemetry + Sentry env-gated wire (1 gün)
- [ ] `docs/runbooks/` — ilk 5 alert için runbook (1 gün)
- [ ] **(Eksen 6)** `uv lock` üret + CI'da `uv sync --frozen` zorunlu (½ gün) → peer-replicable environment

### Hafta 3 (lansman polish)

- [ ] GDPR madde 15 data export endpoint (2 gün)
- [ ] `vendors/elevenlabs/openapi.yaml` pin + contract test (1 gün)
- [ ] `tests/snapshots/openapi.json` (1 gün)
- [ ] Smoke test brand-neutral multi-lang payload (1 gün)
- [ ] Migration rollback policy doc (4 saat)
- [ ] DLQ operator endpoint + replay tool (1 gün)
- [ ] Per-job inference timeout (4 saat)
- [ ] Eval promotion gate (1 saat — ama önce mevcut voice'ları bulk pin gerek)
- [ ] Model registry karar (canary tier, registry table) (2 gün)
- [ ] `docs/api/errors.md`, `idempotency.md`, `authentication.md`, `rate-limits.md`, `websocket.md` (2 gün)
- [ ] **(Eksen 6)** Bootstrap 95% CI runner.py'a ekle — 1000-resample bootstrap her metric için (1 gün)
- [ ] **(Eksen 6)** `scripts/replay_engine_inputs.py` — `usage_records.engine_inputs` JSONB'den alıp aynı koşullarda replay; `NEUROVOICE_DETERMINISTIC=1` flag'i (1 gün)
- [ ] **(Eksen 6)** ADR-14 — Model Card v1 + Dataset Datasheet v1 template (Mitchell et al. 2019 + Gebru et al. 2018 standardı), sample voice için doldur (NULL alanlar açık deklare) (1 gün)

### Hafta 4 (test paketi paralel — Codex)

- [ ] Codex test paketi: ADR-9 snapshot, ADR-10 enrollment, ADR-11 cascade + lifecycle gate, ADR-12 eval pin, ADR-13 watermark round-trip
- [ ] ElevenLabs parity contract test
- [ ] Migration test (alembic upgrade head clean DB)
- [ ] **(Eksen 6)** F5-TTS + XTTS karşılaştırma satırları eval harness'ına (`src/eval/systems/f5tts.py`, `xtts.py`) — RunPod'da open-weight koşar; v0.2-medium üzerine bench (2 gün)

**Toplam: 4 hafta × 1 mühendis-haftası işi** + Codex paralel test paketi + Eksen 6 ek 4.5 gün (mevcut roadmap slack'i içinde, paralel koşar).

### Hafta 5-8 — Bilimsel Olgunluk milestone (lansman sonrası)

**Frame:** Bu milestone **launch-blocker değil, fundraising-supporting**. Production traffic çoktan aktif; bu sprint Series-A pitch deck'ine "production-quality ML" cümlesini destekleyecek bilimsel kanıt katmanını üretir.

- [ ] Talent recording → gerçek LoRA training (ADR-8 wiring + dataset card doldurma + consent recording yazılı kayıt)
- [ ] v0.3-large test seti: TR + EN (her dilde 100 cümle) + 1 ek dil (Arapça veya Endonezce öneri); 300+ cümle toplam
- [ ] Multi-voice eval: 5 voice (3 kadın / 2 erkek, 3 yaş bandı), her biri kendi reference audio'su ile
- [ ] F5-TTS + XTTS + OpenVoice + (opsiyonel) Seed-TTS karşılaştırma satırları (open-weight modeller, RunPod sustained)
- [ ] MOS panel: 5-12 native speaker (TR + EN), 1.5h commitment, MUSHRA-lite Gradio UI; her sistem × her voice × 12 cümle örnekleme
- [ ] Public sample release (HuggingFace Space) — talent-consent + ToS-clean reference ile
- [ ] arXiv-style internal tech report: methodology + dataset + model card + tüm bench tabloları + bootstrap CI'leri
- [ ] AudioSeal robustness test: MP3 16/32/64 kbps re-encode, Opus 24/48 kbps, additive noise, low-pass 8 kHz; detection rate her durumda raporla
- [ ] Bias surface audit: eval set gender/accent/age dağılımı + per-cohort WER/SECS report
- [ ] Eval-vs-training set contamination check policy yazılı

Toplam Eksen 6 yatırımı: **3-4 hafta** (lansman sonrası, talent recording'in availabilirligine bağımlı).

---

## Investor TDD Posture Önerisi

**Ne anlatılır (güçlü):**
- ADR-9..13 chain — voice cloning legal posture trinity tamam
- Application-layer mühendislik kalitesi (D-08, idempotency, XACK matrix)
- 3-axis eval suite (WER+CER, UTMOSv2, SECS) — 2024+ literatür standardı
- ElevenLabs parity yüzeyi (URL/method) — geliştirici göçüne hazır
- Reproducibility audit trail (engine_inputs JSONB)

**Ne kabul edilir (zayıf):**
- Test paketi henüz yok (Codex paralel yazıyor)
- `.github/workflows/` boş — release pipeline yapılmadı (next 2 hafta)
- Operasyonel runbook'lar henüz yazılmadı (next 2 hafta)
- Model registry roadmap'te (next sprint)
- Backup stratejisi seçimi devam ediyor (managed Postgres aday)

**Ne YALAN olarak söylenmemeli:**
- "ElevenLabs SDK drop-in" — bugün düşmüyor (`xi-api-key` + language literal)
- "OpenAPI snapshot CI'da enforce" — workflow yok
- "Tests ile gated" — `pytest` exit 5 verir
- "Multi-region ready" — single-region only
- "GDPR-compliant" — madde 15 (right to access) yok
- "Auto-scaling production" — KEDA wire edilmedi

**Önemli:** İncelemeci `git log` okuyacak. 5 ADR + hotfix son 36 saatte landed. Bu ya muazzam disipline işaret eder (positive), ya kontrolsüz refactor (negative). Sunum metni: **"ADR'ler legal posture + product surface'ı sözleşmeye almaya odaklandı; operational substrate roadmap'in next milestone'unda."**

### Bilimsel iddialar üzerine ne söylenir, ne söylenmez

(Bu blok ML danışmanına yönelik — operasyonel disiplinden ayrı bir mercek.)

**Söyle (güçlü):**
- "Metric seçimimiz (WER+CER+UTMOSv2+SECS) Seed-TTS / F5-TTS / MaskGCT 2024+ paper'larıyla aynı eksenler."
- "Reproducibility audit trail (`engine_inputs` JSONB) ALPHA'dan beri canlı; replay tool inşa edilebilir."
- "Voice manifest v2 schema (ADR-7) model card / dataset card forward-shape — sample voice'ta NULL ama altyapı yerinde."

**Kabul et (proaktif söyle):**
- Test seti şu anda 60 cümle, talent recording sonrası 300+ hedef (Hafta 5-8 milestone'da yazılı).
- Mevcut reference audio vendor-türev — talent recording Hafta 5-7'de.
- MOS panel v0.6 quality milestone'ında (deferral ADR-14'te yazılı olacak).
- Multi-speaker / multi-lingual eval roadmap'te.
- On-disk benchmark sayısı henüz yok — interim REPORT.md Hafta 1'de (vendor-derived reference disclaimer'ı ile).

**YALAN olarak söylenmemeli (kritik):**
- ❌ "Production-grade eval" — production-grade eval'ın **niceliği** bizde yok (60 cümle, tek voice).
- ❌ "Multilingual TTS" — test setimiz TR-only. Ya iddia daralt ("Turkish-first, multilingual roadmap'te"), ya eval genişlet.
- ❌ "Reproducible inference" — auditable inputs evet, byte-deterministic hayır. Dürüst dil: "auditable inputs".
- ❌ "SOTA quality" — 2024 SOTA kıyas satırı (XTTS, F5-TTS, Seed-TTS) yok, iddia destekli değil.
- ❌ "Bias-tested" — bias surface analysis (gender/accent/age coverage) yapılmadı.
- ❌ "ElevenLabs ile karşılaştırılabilir" — vendor reference circular (sample voice ElevenLabs çıktısı), henüz açık-piyasada karşılaştırma değil iç ölçüm.

**Tek landmine:** TDD "show me your benchmark report" deyince `experiments/` (boş) **gösterme** — *"interim report Hafta 1'de, talent recording sonrası nihai bench"* cevabıyla "vacuum"'u "quality bar"a çevir. "Henüz koşmadık" demek "henüz değer üretmiyoruz" gibi okunur; "post-talent-recording bench publishing scheduled" demek "olgunluk kapısı koyduk" gibi okunur. Aynı gerçek, farklı framing.

---

## Appendix A — Agent Raporları

8 agent çalıştı, hepsi `tts-platform-reviewer` veya `general-purpose`. Her birinden ham bulgu çıktısı:

1. **DevOps + Deployment** — `aed0c00cd0a873f12`
2. **Observability + SRE** — `ad4ce2a1dbc5df8f8`
3. **Database + Storage + Migrations** — `a23481b67715e2bbc`
4. **Worker + Queue + GPU** — `a1641f8c7dd344b1d`
5. **ML Pipeline + Model Lifecycle** — `abf6353188c7f56d2`
6. **API + SDK + Integration** — `a048ac6b1ed8a13d5`
7. **Testing + Release Engineering** — `a4a89b2ffd2652094`
8. **Security + Compliance + Multi-tenant** — `af22dc512b98b2680`

(Agent ID'leri Claude Code session içinde resume edilebilir; ham JSON çıktıları conversation history'sinde.)

---

## Appendix B — Drift Surface (CLAUDE.md ile uyumsuz)

Bu doküman okunduktan sonra CLAUDE.md'yi de güncelle:

1. `CLAUDE.md` "Tutulacak kemikler" tablosunda `tests/ — 38 test, testcontainers'lı integration` **yanlış** — `tests/` boş, surgical reset'te silindi
2. `docs/architecture/data-model.md §3` referansı migration'larda — dosya yok
3. ADR-9 `tests/snapshots/openapi.json` snapshot — dosya yok
4. ADR-9 `vendors/elevenlabs/openapi.yaml` — dosya yok
5. `docs/runbooks/database-pool.md` referansı kod'da — dosya yok
6. `docs/api/changelog.md` referansı versioning.md'de — dosya yok
7. `developers.neurovoice.<tld>` openapi-policy.md'de — brand domain TBD
8. `nqai_queue_depth` alerts + grafana dashboard — rename eksik
9. `data/reference-audio/neeko-v0.1-reference.mp3` + `data/test-sets/v0.1-mini.md` headers + `data/casting-prompts/v0.1.md` — NEEKO drift
10. `notebooks/01-voxcpm2-tr-demo.ipynb` + `notebooks/05-...` — NEEKO sentences
11. `docs/research/01e-atlas-area7-eval.md` LEGACY işaretli ama literatür referansları (UTMOS ~4.0, XTTS SECS 0.6423, F5-TTS UTMOS ~4.1, Whisper-large-v3 TR WER ~14%) hâlâ tek-resmi internal kaynak. NeuroVoice-side numbers hiç eklenmedi. CLAUDE.md ya resmiyete al (yeni `docs/research/eval-roadmap.md`'e dönüştür), ya legacy işareti pekiştir.
12. `experiments/` klasörü "eval territory" iddiasının canlı kanıtı olmalı — şu an boş (`2026-05-19-voxcpm2-baseline/output/` boş; `2026-05-25-eval-harness-scaffold/` README scaffolding only). CLAUDE.md veya pyproject.toml "production-shape eval harness" ifadesi varsa yarı-yalan: *"eval harness production-shape, scoresheet henüz boş, interim REPORT.md Hafta 1'de"* dürüst dil.

---

*Bu doküman 2026-05-29 tarihinde yatırımcı TDD öncesi iç değerlendirme amacıyla hazırlanmıştır. Bulgular o tarihteki commit `57da405` baseline'ı üzerine kuruludur. Eksen 6 (Bilimsel Disiplin / Akademik Kalite) aynı gün eklendi (2 paralel Explore agent + 1 Plan agent ile mapping; eval + training/reproducibility territory'sinin tam haritası tarayıcı bulgu olarak ekte). Action plan'daki süreler tek mühendis-paralel-Codex-test-paketi varsayımıyladır.*
