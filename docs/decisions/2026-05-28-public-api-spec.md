# ADR-9 — Public API spec strategy

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** Repo'da 14 public route + 1 WebSocket + admin surface canlı ([src/server/main.py:281-1761](../../src/server/main.py)). FastAPI'nin otomatik ürettiği `/openapi.json` ve Swagger UI çalışıyor. Boşluklar:
  - **Drift sessiz:** Pydantic modelinde yapılan küçük bir değişiklik public spec'i değiştiriyor, PR review'unda görünmüyor. CI snapshot yok.
  - **Vendor-parity URL'leri var, body sözleşmesi yazılı değil:** `/v1/voices/add`, `/v1/text-to-speech/{voice_id}`, `/v1/text-to-speech/{voice_id}/stream`, `WS /v1/text-to-speech/{voice_id}/stream-input` ElevenLabs alias'ları kodda mevcut — ama hangi alanların gerçekten map edildiği, hangilerinin yutulduğu, ElevenLabs SDK'sının gerçekten çalışıp çalışmadığı **hiçbir yerde tanımlı değil**.
  - **Versioning policy yok:** `/v1/` prefix var, breaking change kuralı yazılı değil.
  - **Sunset disiplini izole:** Sadece `/v1/tts` için RFC 8594 header'ları stamp'liyoruz ([main.py:232-244](../../src/server/main.py#L232-L244)). Global lifecycle policy yok.

  Hedef "1 → 100K eş zamanlı kullanıcı" public developer SDK + entegratör güveni gerektirir. Sözleşmesiz API üzerine SaaS satılmaz.

## Karar

Hibrit strateji — **iki yüzey iki ayrı sözleşme disiplinine** tabi tutulur.

### 1. Native yüzey — kanun biz

Rutalar: `GET /health`, `GET /v1/models`, `GET /v1/voices`, `GET /v1/voices/{id}`, `POST /v1/voices`, `PATCH /v1/voices/{id}`, `DELETE /v1/voices/{id}`, `POST /v1/tts/jobs`, `GET /v1/tts/jobs/{job_id}`.

- **Source of truth:** FastAPI auto-generated `/openapi.json`. Pydantic modeller kanun; el-yazısı YAML yok.
- **CI snapshot:** `tests/snapshots/openapi.json` PR diff'i — spec değişikliği review'sız merge olamaz. (Snapshot dosyası Codex test paketinin parçası; bu ADR yalnızca disiplini bağlar.)
- **Ortak error model:** Tek `ErrorResponse` Pydantic modeli tüm native route'ların `responses=` dict'inde standart status code listesine bağlanır (401/403/404/409/422/429/500/503). Mevcut `{401, 404}` global tanımı genişletilir.
- **Tag taksonomisi:** `meta`, `voices`, `synthesis`, `synthesis:parity`. `admin` route'ları `include_in_schema=False` ile public spec'ten gizlenir.
- **Static doc publish hedefi:** [Scalar](https://github.com/scalar/scalar). Snapshot `/openapi.json` input; static site CI'da regenere edilen artifact. Seçim gerekçesi `docs/api/openapi-policy.md` "Publishing" bölümünde (özet: Stripe/Linear/Anthropic tonuna estetik uyum + interactive playground + OpenAPI 3.1). ReDoc one-config-line fallback olarak kalır.

### 2. Parity yüzeyi — kanun ElevenLabs

Rutalar: `POST /v1/voices/add`, `POST /v1/text-to-speech/{voice_id}`, `POST /v1/text-to-speech/{voice_id}/stream`, `WS /v1/text-to-speech/{voice_id}/stream-input`.

- **Source of truth:** ElevenLabs'in **kendi yayınladığı OpenAPI spec'i**. `vendors/elevenlabs/openapi.yaml` altında pin'lenir; version, indirme tarihi ve upstream URL `vendors/elevenlabs/README.md`'de tutulur.
- **Contract test:** `tests/contract/test_elevenlabs_parity.py` (Codex yazar) — Schemathesis veya muadili vendor YAML'dan fuzz isteği üretir, server'a atar, schema-uyumsuz response → CI fail.
- **Native extension yasak:** VoxCPM2-özgül alanlar (`lexicon_id`, `adapter_id`, `language_pack`, `seed`, `cfg`, `timesteps`, `eval_pin`) parity route'larda **kabul edilmez**. Pydantic request modeli `extra="forbid"`. Bu alanları kullanmak isteyen entegratör native yola geçer.
- **Auth dual-name:** `xi-api-key` (ElevenLabs konvansiyonu) + `X-NV-API-Key` (native, ADR-1) ikisi de geçerli; geçiş kolaylığı.
- **Hedef:** ElevenLabs Python ve TypeScript SDK'ları parity yüzeyinde drop-in çalışır. Body parity hedeflenir, **davranış (kalite/latency/voice ID) parity hedeflenmez**.

### 3. Versioning + lifecycle

- v0.x boyunca **anytime breaking** (yazılı kontrat; entegratör bilerek girer).
- v1.0 sonrası semver. Major bump = RFC 8594 Sunset + Deprecation header + min 90 gün deprecation.
- Vendor breaking-change upstream parity route'unu non-elective sunset'e zorlayabilir; vendor'ın kendi deprecation süresi bizim 90 günden kısaysa biz de daha kısa süre uygulayabiliriz.
- Detay: `docs/api/versioning.md`.

### 4. Kapsam dışı (bu ADR'de bağlanmayan)

- **MiniMax parity** — v0'da yok; karar değişirse ayrı ADR.
- **OpenAI TTS, PlayHT parity** — keza.
- **SDK generation (kendi SDK'mız)** — pyproject yayın disiplini oturmadı; v0.x churn'unda compat liability yaratır. Ayrı ADR.
- **`POST /v1/voices/{voice_id}/with-timestamps`, speech-to-speech, voice-generation, dubbing** (ElevenLabs'in desteklediği ama bizde olmayan endpoint'ler) — parity scope dışı.

## Yeni / değişen dosyalar

| Dosya | Tip | Rol |
| --- | --- | --- |
| `docs/decisions/2026-05-28-public-api-spec.md` | yeni | bu ADR |
| `docs/api/openapi-policy.md` | yeni | native spec disiplini (EN) |
| `docs/api/vendor-parity.md` | yeni | ElevenLabs parity scope + native-extension yasağı (EN) |
| `docs/api/versioning.md` | yeni | semver + Sunset policy (EN) |
| `vendors/elevenlabs/README.md` | yeni | spec pinning + refresh procedure (EN) |
| `CLAUDE.md` | güncelle | ADR tablosuna ADR-9; ertelenmiş karar #1 düş |

Follow-up (bu ADR ile **bağlanmayan**, ayrı kaydedilen iş):

- `vendors/elevenlabs/openapi.yaml` — ElevenLabs spec pin'i (manual indirme, WebFetch kapalı).
- `tests/snapshots/openapi.json` — CI snapshot dosyası (Codex test paketi).
- `tests/contract/test_elevenlabs_parity.py` — contract test (Codex).
- Native route'larda ortak `ErrorResponse` model wire-up (kod refactor).
- `src/server/main.py` `VERSION = "0.4.0"` → `importlib.metadata.version("neurovoice")` (canonical pyproject — `0.2.0`'a hizalanır veya pyproject bump'lanır).
- `include_in_schema=False` admin route'lara stamp'lenir.
- Scalar bundle generation + publish CI job (snapshot → static HTML → `developers.neurovoice.<tld>`).

## Sebep

- **A (FastAPI auto-OpenAPI) tek başına yetersiz, çünkü parity'de drift'i yakalamaz.** A'nın gücü "kod = spec, sessiz drift yok" — ama parity için uyman gereken spec **dışarıda**. Pydantic modelini ElevenLabs'inkinden ayrı bir şekilde değiştirirsen FastAPI mutlu, SDK kırık, CI sessiz.
- **B (contract-first, el-yazısı openapi.yaml) her yerde overkill.** Native yüzeyimiz için çift-defter; v0.x churn'unda sürekli senkron iş. FastAPI ile pratik fayda yok.
- **Vendor spec elimizde, yazmaya gerek yok.** ElevenLabs OpenAPI'sini repo'ya çekip **test fixture** olarak kullanmak, "vendor-parity.md el yazısı matrisi" tutmaktan daha sert disiplin — doc unutulur, test patlar.
- **Native vs parity ayrımı brand bağımsızlığı.** Parity route'lara native extension sızdırırsak ElevenLabs roadmap'i bizim native gelişimimizi kısıtlar. Ayrı tutarak ElevenLabs'ten yarın çıkacak özelliklere zorla yetişme baskısı yok.
- **v0.x "anytime breaking" yazılı kontrat ucuz disiplin.** Entegratör beklentisi doğru kalibre edilir; ilk kırılmada itibar borcu birikmez.

## Risk

- **Body parity'nin "drop-in SDK" iddiası iddialı.** İlk contract test koşusunda kaç uyumsuzluk çıkacağı belirsiz. Plan: parity yüzeyi `docs/api/vendor-parity.md`'deki **subset** olarak tanımlı; çalışmayan endpoint'ler "Not implemented" satırına düşer. ADR-revize gerekmez.
- **ElevenLabs spec değişiklik hızı.** Quarterly refresh disiplini; vendor breaking-change non-elective sunset tetikleyebilir (versioning policy buna izin verir).
- **Pyproject ↔ runtime version drift'i mevcut** (pyproject `0.2.0`, `main.py:116` `0.4.0`). Bu ADR canonical olarak pyproject'i belirler; `importlib.metadata.version("neurovoice")` wire-up follow-up code change.
- **Contract test infrastructure'ı henüz yok.** Codex test paketi geldiğinde Schemathesis bağımlılığı + sample auth path + voice fixture'ı gerek. Bu ADR sadece kapısını açar.

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| Sadece A (FastAPI auto-OpenAPI) + el-yazısı `vendor-parity.md` matrisi | Doc unutulur, body parity sessizce kırılır; "drop-in SDK" iddiası testle desteklenmezse müşteri ilişkisinde bedeli olur |
| Sadece B (contract-first, elle openapi.yaml) | v0.x churn'unda çift-defter; FastAPI ile pratik fayda yok |
| ElevenLabs spec'i native yüzeye de uygulamak (klon olmak) | VoxCPM2 + LoRA özgül alanlar (lexicon, adapter, eval pin) ElevenLabs body'sine sığmaz; vendor roadmap'ine bağlanmak strateji ile çelişir |
| MiniMax'i v0'da paralel ekle | Scope patlaması; ElevenLabs parity'sini stabilize etmeden ikinci vendor erken |
| SDK generation'ı bu ADR'de bağla | pyproject yayın disiplini oturmadı; v0.x churn'unda erken SDK compat liability — ayrı karar |
| Vendor spec'i pin'lemeden, "her zaman güncel ElevenLabs" hedefle | Reproducibility yok; bizim sürümümüz onların staging'ine bağlı |
| `extra="allow"` parity request modellerinde | Native alanlar parity'ye sızar; "parity route ne yapıyor" sorusu belirsizleşir |

## İlgili

- [[project-framing]] — uluslararası TTS API SaaS, ElevenLabs/MiniMax referans
- [[brand-naming]] — NeuroVoice
- CLAUDE.md ertelenmiş karar #1 — public API spec'i (bu ADR ile karara bağlandı)
- ADR-1 — `X-NV-*` header prefix → parity yüzeyinde `xi-api-key` dual-name buradan gelir
- ADR-7 — voice manifest schema v2 → native voice surface'in iç yapısı
- ADR-8 — LoRA fine-tune pipeline → fine-tune jobs API'si v0'da var (`/v1/finetune-jobs`), ileride ayrı ADR (ertelenmiş karar #7)
