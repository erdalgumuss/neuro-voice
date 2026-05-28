# ADR-7 — Voice manifest schema v2 + sample voice rename

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** Voice catalog manifest'i v1 şemasında (sadece `voice_id`, `display_name`, `language`, `gender`, `style_tags`, `reference_audio`, `reference_seconds`, `source`, `license`, `created_at`, `created_by` + opsiyonel `adapter`/`engine_params`). CLAUDE.md nirengi 9 voice'u `(base_model_id, adapter_id, lexicon_id, frontend_pack_id, eval_pin)` tuple'ı olarak tanımlıyor — şemada bu alanlar yok. Ayrıca bundled sample voice'lar (`neeko-v01`, NIVA/NeuroCourse placeholder slot'ları) eski iç-ürün isimleriyle dolu; brand-neutral örnek voice'a indirgenmesi lazım.

## Karar

### 1. Manifest schema v2

`Voice` dataclass'ına forward-shape opsiyonel alanlar eklendi (`src/registry/catalog.py`):

| Alan | Tip | Anlam |
| --- | --- | --- |
| `schema_version` | `int` (zorunlu, sabit `2`) | Manifest sürüm pininin formal kaydı; v0.x'te tek geçerli değer |
| `base_model_id` | `str \| None` | Voice'un pinlendiği engine baseline (örn. `voxcpm2-tr-hd`) — request-time default override için |
| `adapter` | `dict \| None` (mevcut) | LoRA / adapter referansı; URI + SHA256 + tip |
| `engine_params` | `dict \| None` (mevcut) | Per-voice inference knobs (cfg, timesteps) |
| `lexicon` | `dict \| None` | Per-voice pronunciation overlay; lang_pack lexicon'unun üstüne biner |
| `watermark` | `dict \| None` | Sessiz audio watermark konfigürasyonu (`key_id`, vs.) |
| `eval_pin` | `dict \| None` | Voice'u sertifikalandıran eval baseline snapshot'ı (`test_set`, `metrics`, `evaluated_at`) |

Validator: `_normalize_manifest_dict` `schema_version` field'ını **zorunlu** kabul ediyor. Eksikse `ManifestSchemaError` raise eder. `Voice.__post_init__` farklı bir versiyonsa raise eder. v0.x'te tek desteklenen değer `2`.

### 2. Sample voice rename (brand-neutral, multi-lang slug)

| Eski | Yeni |
| --- | --- |
| `configs/voices/n`​`eeko-v01.yaml` | `configs/voices/tr-warm-storyteller-v0.yaml` |
| voice_id `n`​`eeko-v01` | `tr-warm-storyteller-v0` |
| display_name `"N`​`EEKO v0.1 (köprü)"` | `"Turkish Warm Storyteller v0 (example)"` |
| style_tags `[warm, child-directed, storyteller, androgynous]` | `[warm, narrative, storyteller]` |
| source `elevenlabs` | `example` |
| license `internal-bridge` | `example` |
| reference_audio `n`​`eeko-v0.1-reference.mp3` | `tr-warm-storyteller-v0-reference.mp3` |

**Yeni slug konvansiyonu:** `<lang>-<style>[-<variant>]-v<n>`. Lang ISO 639-1, style birden çok kelime için `-` ile bağlanır, `v<n>` major sürüm.

### 3. seed_voices.yaml sadeleştirme

Eski: 5-slot launch catalog (1 NEEKO + 2 NIVA + 2 NeuroCourse) — hepsi `placeholder` source + `internal-bridge`/`internal-placeholder` license, eski iç-ürün isimleri.

Yeni: tek `tr-warm-storyteller-v0` example voice. License vocabulary: `example`, `synthetic`, `user-owned`, `talent-contract:<id>` (bu ADR'de tip taksonomi değişmiyor, sadece sample değerler).

### 4. data/ rename

`data/neeko-test-ses/` → `data/examples/tr-narration-samples/`. 21 git-tracked `.txt` dosya + 10 git-ignore'lu `.mp3` dosya `git mv` ile taşındı; içerik bit-perfect korundu.

### 5. Reference site updates

8 dosya updated: `scripts/{bootstrap_voices,eval_run,latency_bench,load_bench,smoke_test}.py`, `notebooks/{03,04,05}-*.ipynb`. 20 slug + 9 reference-audio filename + 5 doc-phrase substitution. `.env.example` `NEUROVOICE_WORKER_WARMUP_VOICES` default güncellendi.

## Sebep

- **Forward-shape, no premature serialization:** v2 fields opsiyonel — kullanılmadıklarında NULL, future ADR'ler (LoRA training, eval pin, watermark service) bu fields'ı doldurur. Şimdi tasarlanır, sonra populate edilir.
- **Schema versioning sharp boundary:** `schema_version` zorunlu = future v3 bump'ı sıkı yapabiliriz (eski manifestler explicit error verir, silent breakage yok).
- **Brand-neutral sample:** Bundled example voice eski NEEKO branding'i taşıyamaz; getting-started experience NeuroVoice kimliğinde olmalı.
- **CLAUDE.md nirengi 9 hizalama:** Voice manifest = `(base_model_id, adapter, lexicon, watermark, eval_pin)` tuple'ı; bu ADR şemayı bu hedefe taşıyor.

## Etkilenen dosyalar

| Dosya | Değişiklik |
| --- | --- |
| `src/registry/catalog.py` | Voice dataclass v2 fields + ManifestSchemaError + schema_version validator |
| `configs/voices/n`​`eeko-v01.yaml` → `configs/voices/tr-warm-storyteller-v0.yaml` | git mv + v2 content rewrite |
| `configs/seed_voices.yaml` | rewrite — 5-slot → 1 example |
| `data/n`​`eeko-test-ses/` → `data/examples/tr-narration-samples/` | git mv (21 tracked .txt + 10 .mp3 binaries) |
| `.env.example` | `NEUROVOICE_WORKER_WARMUP_VOICES` default slug rename |
| `scripts/{bootstrap_voices,eval_run,latency_bench,load_bench,smoke_test}.py` | docstring + default slug references |
| `notebooks/{03,05}-*.ipynb` | slug references + smoke text |
| `notebooks/04-*.ipynb` | LoRA training notebook; ADR-8 scope'unda yeniden tasarlanacak |

## Risk

- **Operator etki:** Mevcut deployment'ta `voice_id=neeko-v01` enroll edilmiş tenant'lar olabilir; manifest dosyası rename'i kataloğu yeniden init eder (DB row ayrı). v0.x'te production yok → düşük risk.
- **Bootstrap workflow:** `scripts/bootstrap_voices.py` reference audio mp3'ünü `data/reference-audio/tr-warm-storyteller-v0-reference.mp3` bekler. Operator manuel olarak rename veya yeni binary yerleştirmeli.
- **Backward-compat:** Yok. v1 manifest dosyaları artık load edilmez (`ManifestSchemaError`).

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| `schema_version` opsiyonel (default 2) | Silent acceptance yanıltıcı; future v3 bump'ı tehlikeli |
| v2 fields'i şimdi populate et (örn. default `base_model_id: voxcpm2-tr-hd`) | Premature commitment; voice-engine binding ayrı bir karar |
| 5-slot seed_voices.yaml'ı koru | NIVA/NeuroCourse placeholder voice'ları eski iç-ürün isimleri; brand inconsistency |
| Sample voice'ı tamamen sil (sadece DB enrollment) | Getting-started experience boş katalog → kötü first-boot UX |
| `data/n`​`eeko-test-ses/`'i sil | TR narration örnekleri language pack development'a referans değer taşır (uyanis, gece-korkusu, sevinç, vs. çeşitli ton/duygu çıktıları) |

## Doğrulama

1. `ruff check src/ scripts/` yeşil.
2. `from registry.catalog import Voice, ManifestSchemaError; v = Voice(...)` round-trips.
3. Yeni manifest load: `VoiceRegistry(...).list_voices()` → `tr-warm-storyteller-v0` döner.
4. Eski v1 manifest reddi: `schema_version` field'ı silinmiş manifest → `ManifestSchemaError`.
5. Yanlış schema_version: `Voice(schema_version=99, ...)` → `ManifestSchemaError`.
6. Residue: `grep -rn 'neeko-v01\|neeko-test-ses' src/ scripts/ configs/` → boş.

## İlgili

- [[brand-naming]] — NeuroVoice + brand-neutral slug
- CLAUDE.md nirengi 9 — voice manifest tuple
- **Sonraki:** ADR-8 — LoRA training kodunu notebook'tan kod tabanına; bu ADR'de bırakılan `adapter`/`base_model_id`/`eval_pin` fields'ı orada gerçek değerlerle dolar.
