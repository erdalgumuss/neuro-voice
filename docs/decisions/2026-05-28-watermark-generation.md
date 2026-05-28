# ADR-13 — Watermark generation (AudioSeal)

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** ADR-10 voice cloning'in **hukuki ön yüzünü** kurdu (license_kind + consent). ADR-11 **runtime kapısını** kurdu (lifecycle gate, deletion). Üçlünün eksik üçüncü ayağı **kriptografik kanıt** — "bu ses NeuroVoice platformunda üretildi, bu key/voice ile imzalandı" iddiası. AI-voice suistimal (deepfake, izinsiz klon, voice phishing) vakası geldiğinde mahkeme önünde kanıt zincirinin yegane kapısı.

  Mevcut durum:
  - `voices.watermark_key_id: Text NULLABLE` ADR-7 forward-shape kolon olarak hazır, **runtime tarafı boş** — sentez çıktısı imzasız
  - `src/worker/engine.py` PCM chunk'larını üretiyor ([engine.py:556](../../src/worker/engine.py#L556)), sonra encode ediyor — watermark uygulama noktası yok
  - Forensics detection endpoint yok
  - Key management yok (rotation, allocation, audit trail)

  İlk B2B müşterisi voice cloning kullanıma açtığında, ilk suistimal raporunda (örn. "müşterimizin kurum sesi izinsiz reklamda kullanılmış") elimizde kanıt aracı yok.

## Karar

**Library: AudioSeal (Meta, MIT, PyPI).** Tek seçilebilir aday — AI-generated speech için özel tasarlandı, SOTA detection rate (>99%), perceptually transparent, compression/resampling/noise'a karşı dayanıklı. 2024-12-12 v0.2 streaming desteği eklendi (bizim chunked synth path'ine doğal uyar).

Alternatifler reddedildi:
- **WavMark** (klasik, daha hafif) — yaşlı pattern, lossy encoding altında %85 detection (AudioSeal ~%99)
- **Custom LSB / spread-spectrum** — kolay yıkılır, içerik-koruma odaklı tasarımdır TTS imza için değil
- **Steganography toolkits** (e.g. WavSteg) — robustness yetersiz

### 1. Şema — küçük + key management

**`voices` kolonları:**

```sql
-- voices.watermark_key_id Text NULLABLE  (mevcut, ADR-7) → UUID FK
ALTER TABLE voices DROP COLUMN watermark_key_id;
ALTER TABLE voices ADD COLUMN watermark_key_id UUID
    REFERENCES watermark_keys(id) ON DELETE SET NULL;
-- yeni:
ALTER TABLE voices ADD COLUMN watermark_enabled BOOLEAN NOT NULL DEFAULT TRUE;
```

v0.x'te production row yok → DROP+ADD temiz; mevcut TEXT veriler zaten NULL.

**`watermark_keys` (yeni tablo, operator-managed):**

```sql
CREATE TABLE watermark_keys (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_bits    INTEGER NOT NULL,        -- 0..65535 (16-bit AudioSeal payload)
  label           TEXT NOT NULL,            -- operator-readable, e.g. "tenant-acme-q2"
  allocated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  retired_at      TIMESTAMPTZ,
  retired_reason  TEXT,
  notes           TEXT,
  created_by_operator_id UUID
                  REFERENCES operators(id) ON DELETE SET NULL,
  CONSTRAINT ck_watermark_keys_message_bits_range
    CHECK (message_bits BETWEEN 0 AND 65535),
  CONSTRAINT ck_watermark_keys_label_length
    CHECK (char_length(label) BETWEEN 1 AND 200)
);

-- Active 16-bit slot uniqueness; retired keys can co-exist with the same
-- bits if needed for historical detection (we keep both rows).
CREATE UNIQUE INDEX ix_watermark_keys_message_bits_active
    ON watermark_keys (message_bits)
    WHERE retired_at IS NULL;
```

**16-bit (=65536 unique slots) gerekçesi:**
AudioSeal default payload boyutu. Daha geniş (32/64-bit) varyantlar var ama detection robustness düşüyor. v0'da 65k slot yeterli — operator allocation politikası ile (per-tenant / per-voice-cluster / per-jurisdiction) genişletilebilir. Tahmin: ilk 100k voice'a kadar slot yeniden kullanımı gerekmiyor.

### 2. Key allocation politikası — operator-driven, esnek

v0'da otomatik allocation YOK. Operator endpoint ile yeni key oluşturur, voice'a atar. Common patterns (operator karar verir):

| Pattern | Allocation | Use case |
|---|---|---|
| Per-voice key | Her voice'a unique 16-bit | Maksimum forensics ayrımı (65k voice tavanı) |
| Per-tenant key | Tenant'ın tüm voice'ları aynı key | Tenant bazlı suistimal taraması; voice ayrımı yok |
| Per-jurisdiction key | EU/TR/US bazlı key gruplaması | Bölge bazlı yasal soruşturma |

Default: operator endpoint label ile yeni key allocate eder, voice'a manuel atar. v1+'de auto-allocation eklenebilir.

### 3. Worker integration — `src/audio/watermark.py` wrapper

Yeni modül `src/audio/watermark.py` — AudioSeal'i sarmalar:

```python
class WatermarkApplier:
    def __init__(self, model_name="audioseal_wm_16bits"): ...
    def watermark_pcm(self, pcm_int16: bytes, sample_rate: int,
                      message_bits: int) -> bytes: ...

class WatermarkDetector:
    def __init__(self, model_name="audioseal_detector_16bits"): ...
    def detect(self, pcm_int16: bytes, sample_rate: int
              ) -> WatermarkDetectionResult: ...
```

Lazy load + thread-safe + graceful degrade pattern (ADR-12 metric modülleriyle aynı disipline):
- `import audioseal` fail → log + skip + Prometheus counter (`watermark_skip_total{reason="lib_missing"}`)
- model load fail → aynı

Worker `engine.synthesize_stream()` ([src/worker/engine.py:488-556](../../src/worker/engine.py)) içinde her chunk publish edilmeden önce:

```python
if voice.watermark_enabled and voice.watermark_key_id is not None:
    key = lookup_key(voice.watermark_key_id)  # cached
    pcm_int16 = applier.watermark_pcm(pcm_int16, sample_rate, key.message_bits)
publish_chunk(pcm_int16, ...)
```

Key lookup worker boot'unda + her synthesize başında cache'lenir; per-chunk DB query yok.

### 4. Strict mode vs graceful degrade

Default: **graceful degrade**. Library yoksa veya key allocate edilmemişse:
- Voice sentez yapar (watermarksız)
- Prometheus counter: `watermark_skip_total{reason="..."}`
- Audit log: `action='voice.synthesize.unwatermarked'` warning level

Strict mode (env var `NEUROVOICE_WATERMARK_REQUIRED=true`):
- `watermark_enabled=True` + key/lib eksik → **503 Service Unavailable**
- Voice sentez yapmaz; operator setup'ı tamamlayana kadar bloke

Strict mode production'da varsayılan değil — license_kind bazlı zorunluluk daha güvenli (aşağıda).

### 5. License-kind bazlı zorunluluk

Watermark policy hukuki olarak `license_kind` ile bağlı:

| license_kind | watermark_enabled | Default |
|---|---|---|
| `example` | İsteğe bağlı | TRUE (test voice de imzalı, demo'da kullanım izi) |
| `synthetic` | İsteğe bağlı | TRUE |
| `user-owned` | İsteğe bağlı | TRUE |
| `talent-contract` | **Zorunlu** | TRUE (admin disable retmez) |
| `public-figure` | **Zorunlu** | TRUE |
| `partner-licensed` | **Zorunlu** | TRUE |

Son üç kategoride `POST /admin/voices/{id}/watermark/disable` 422 döner — license-kind hukuki çerçeve bunu engeller. Operator önce license_kind değiştirmek zorunda (audit log + rationale ile).

### 6. Detection endpoint — operator-only

```
POST /admin/forensics/detect-watermark
```

Body: multipart audio upload.
Response:
```json
{
  "watermark_probability": 0.98,
  "message_bits": 12345,
  "matched_key_id": "uuid-...",
  "matched_key_label": "tenant-acme-q2",
  "matched_voice_ids": ["vx_..."],
  "detected_at": "2026-05-28T...",
  "detail": {"sample_rate_used": 16000, "duration_seconds": 3.2}
}
```

Operator-only (JWT cookie). v0'da public verifier YOK — key allocation pattern'ini gizli tutuyoruz; suistimal raporları operator destek kanalından geçer. v1+'de rate-limited public verifier eklenebilir.

Forensics audit log entry her detection'da düşer (`action='forensics.detect'`, operator+timestamp+result).

### 7. Streaming vs full-clip watermarking

AudioSeal 0.2+ streaming destekli — her chunk bağımsız imzalanır. Sentez path'imiz chunked (sentence-bazlı, 2-15s tipik). Her chunk'a 16-bit message embed edilir; detection chunk-bazlı çalışır (kısa klip 1s+ yeterli AudioSeal'e).

Bu mimari avantajları:
- Streaming TTFB'ye etki minimal (~10-30ms per chunk)
- Audio crop edilirse (örn. ilk 2 sentence) hala detection işliyor — her sentence kendi imzasını taşır
- Chunk-level localization: hangi cümle imzalı, hangisi orijinal (re-edit detection)

### 8. Public surface — şimdilik gizli

`VoicePublic`'te `watermark_enabled` / `watermark_key_id` **EXPOSE EDİLMEZ**. Sebep:
- Tenant'ın kendi voice'una baktığında "watermark_key_id" gözükmesi attacker reconnaissance kolaylaştırır
- Suistimal vakasında ne ile karşılaşılacağı bilinmesin

Yalnızca tenant `watermark_enabled` durumunu öğrenir (yes/no flag); key_id operator-only. Detection sonuçları yalnızca admin endpoint'ten.

Bu opaque tarafta — gerekirse ileride `watermarked: true/false` boolean expose edilir; key_id asla.

## Yeni / değişen dosyalar

| Dosya | Tip | Rol |
| --- | --- | --- |
| `docs/decisions/2026-05-28-watermark-generation.md` | yeni | bu ADR |
| `migrations/versions/2026_05_28_0012_watermark_generation.py` | yeni | migration |
| `src/db/models.py` | update | `WatermarkKey` modeli + Voice `watermark_enabled` kolonu + `watermark_key_id` UUID FK |
| `src/repos/watermark.py` | yeni | `WatermarkKeyRepo` (operator-scoped) |
| `src/repos/__init__.py` | update | export |
| `src/audio/watermark.py` | yeni | AudioSeal wrapper — applier + detector, graceful degrade |
| `src/worker/engine.py` | update | `synthesize_stream` watermark application per chunk |
| `src/server/admin/router.py` | update | 6 endpoint: watermark-keys CRUD + voice toggle + forensics detect |
| `src/server/schemas.py` | update | `WatermarkKeyPublic` + `WatermarkDetectionResult` + request body schemas |
| `src/server/main.py` | minor | (no public expose; sadece import gerekirse) |
| `pyproject.toml` | update | `[project.optional-dependencies] watermark = ["audioseal>=0.2"]` |
| `CLAUDE.md` | update | ADR-13 row; ertelenmiş #6 düşer |

Follow-up:

- **Public verifier** (rate-limited, opsiyonel anti-deepfake API endpoint) — v1+
- **Auto-allocation** — operator istek üzerine değil, voice enroll'unda otomatik key allocate — v1+
- **Per-jurisdiction key gruplaması** — multi-region ADR'i ile beraber
- **Watermark detection benchmark** — kendi test set'imizde detection rate ölçümü, ADR-12 metric suite'a eklenir
- **Strict mode default**'a almak — operator'lar kendi setup'ı tamamladıktan sonra ayrı bir karar
- **Watermark key rotation policy** — periyodik rotation (örn. quarterly) ve eski key arşivlemesi
- **Test paketi** (Codex)

## Sebep

- **AudioSeal tek seçilebilir aday:** AI-generated speech için tasarlanmış, MIT lisanslı, streaming desteği var (v0.2), detection rate SOTA. WavMark/custom alternatifler robustness'da geride.
- **16-bit payload yeterli v0:** 65k slot operator allocation politikası ile uzun süre yeterli; daha geniş payload detection robustness'ı düşürür.
- **Graceful degrade default:** Production'da AudioSeal kurulmamış olabilir veya model yüklenirken hata çıkabilir; sentez patlaması yerine watermarksız çıkıp counter'a bakmak güvenli.
- **License-kind bağlı zorunluluk:** Talent/public-figure/partner voice'ları HUKUKİ olarak imzalanmalı; operator UI kazara disable etmesin diye 422 ile engel.
- **Detection operator-only v0:** Key allocation pattern'i gizli kalır; suistimal raporları geleneksel destek/legal kanalından geçer.
- **No public expose:** Tenant'ın UI'sında `watermark_key_id` gözükmesi reconnaissance kolaylaştırır; gizli kalır.
- **Worker entegrasyon chunk-bazlı:** Streaming TTFB'ye etki minimal; her chunk bağımsız detection edebilir (audio crop edilse de iz kalır).
- **DROP+ADD migration safe:** v0.x'te production row yok; tip dönüşümü (TEXT→UUID) için en temiz yol.

## Risk

- **AudioSeal model boyut:** ~150 MB generator + ~50 MB detector; worker boot'unda ek hafıza. Mitigasyon: lazy load, voice tarafından kullanılmıyorsa init etme.
- **Watermark detection rate düşüşü extreme compression altında:** 16 kbps MP3 + bandlimit gibi extreme codec'lerde detection rate %85'lere düşebilir. Mitigasyon: telephony/VoIP codec'lerinin TTFB metric'inde detection rate ayrı tracking + dökümante.
- **AudioSeal sample rate sınırlı (16k/24k/44.1k/48k):** Bizim sentez output'unu (genelde 24k) destekler; ama edge case sample rate'ler için fallback gerek.
- **Adversarial removal:** Determined attacker AudioSeal'i kaldırabilir (paper'da bu zayıflık var). Mitigasyon: watermark **kanıtın bir parçası**, tek kanıt değil; usage_records + audit_log birlikte ele alınır.
- **Strict mode default değil:** Üretimde operator unutursa watermarksız ses çıkabilir; counter alarm + audit log warning takipte. Mitigasyon: Grafana alert + operator runbook.
- **Detection endpoint operator-only v0:** Public reporting yolunu zorlaştırır; suistimal raporu support pipeline'ından geçmeli.
- **Watermark key compromise:** Eğer attacker bir key'in 16-bit pattern'ini öğrenirse (örn. eski sentez örneği topladıysa), o key'in ürettiği imzaları "taklit" edebilir. Mitigasyon: periodic rotation (follow-up ADR).
- **Chunk-bazlı watermark + sentence boundary:** İlk chunk'ın baş kısmı 1s'ten kısaysa (örn. çok kısa cümle), detection güvenilirliği düşer. Mitigasyon: minimum chunk uzunluğu policy (worker config).

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| WavMark | Lossy codec altında detection rate %85 (AudioSeal %99); modern AI audio için tasarlanmamış |
| Custom LSB watermark | Trivial olarak kaldırılır; legal proof değer yok |
| Audio fingerprinting (Shazam-style) | Watermark değil; aynı klip'i ararken yararlı, üretim noktasını kanıtlamaz |
| Streaming-time olmayan post-process watermarking | TTFB'ye etki; chunked path'imize sığmaz |
| 32-bit AudioSeal payload | Detection robustness düşer; 65k slot v0 için yeterli |
| Public detection endpoint v0'da | Reconnaissance riski; suistimal raporu support kanalından geçer |
| Stored secret key (private/public crypto signature) | AudioSeal imza şekli farklı — pre-trained model + 16-bit message; PKI gerekmez |
| Otomatik key allocation enroll'da | Operator kontrolü gerek; key pattern (per-tenant/voice/region) v0'da insan kararı |
| Watermark required mode default | Operator setup'ı atlarsa tüm sentez bloke; graceful degrade + alert daha güvenli |
| Strict + license_kind kombinasyon eski ki yokluk | License-kind bağlı zorunluluk daha cerrahi |

## İlgili

- ADR-7 — voice manifest schema v2 (`watermark_key_id` forward-shape; bu ADR runtime fill)
- ADR-10 — license + consent (license_kind bazlı watermark zorunluluğu buradan beslenir)
- ADR-11 — lifecycle + RTBF (frozen/purged voice'larda watermark gereksiz — synthesis gate zaten engeller)
- ADR-12 — eval pin (watermark detection rate ileride 4. metric olarak eklenebilir)
- CLAUDE.md ertelenmiş #6 — watermark (bu ADR ile karara bağlandı)
- [AudioSeal (Meta)](https://github.com/facebookresearch/audioseal) — MIT
- [AudioSeal paper](https://arxiv.org/pdf/2401.17264) — "Proactive Detection of Voice Cloning with Localized Watermarking"
- [audioseal on PyPI](https://pypi.org/project/audioseal/)
