# ADR-8 — LoRA fine-tune pipeline'ını notebook'tan `src/finetune/`'a taşı

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** VoxCPM2 LoRA fine-tuning end-to-end pipeline'ı `notebooks/04-voxcpm2-lora-finetune-colab.ipynb` (41 hücre, ~4000 satır) içinde yaşıyordu. Notebook **gerçek operatör akışıydı** (Drive mount, Deepgram API, training loop, inference test, artifact export). Bu durum birkaç sorun yaratıyor:
  1. Pipeline non-Colab'ta çalışmaz — local dev'de veya CI'de re-run edilemez.
  2. Adımlar birbirine bağlı global state üzerinden konuşuyor (drive paths, VOICE_ID, ckpt dirs) — bir hücreyi atlayınca sonraki kırılır.
  3. Modüler test imkânsız; her küçük değişiklik notebook re-run gerektirir.
  4. Output cells eski iç-ürün isimleri (`neeko-proto-v0`) ve eski yol prefix'leriyle dolu — drift sızıntısı için verimli yüzey.
  5. CLAUDE.md nirengi 9 voice'u `(base_model_id, adapter_id, lexicon_id, frontend_pack_id, eval_pin)` tuple'ı olarak tanımlıyor. Adapter ID'yi notebook'tan üretmek "manifest tabanlı pipeline"a uymaz.

## Karar

LoRA fine-tune pipeline'ı **`src/finetune/` first-class Python paketi** olarak yeniden organize edildi; notebook 04 `notebooks/archive/` altına taşındı (referans olarak korunur ama "operatör koşar" değil).

### Paket yapısı

```
src/finetune/
├── __init__.py        # pipeline genel bakış docstring'i
├── project.py         # ProjectLayout — tüm yol konvansiyonları tek noktada
├── transcribe.py      # ffmpeg + Deepgram → per-utterance clip + raw manifest
├── manifest.py        # raw manifest validate + train/val/test split (ref_audio mixing)
├── config.py          # LoRA training config builder + VRAM-aware preset
├── train.py           # VoxCPM training script'ine subprocess wrapper
├── inference.py       # checkpoint inference eval (Türkçe default prompt'lar)
└── export.py          # run_metadata.json + zip outputs/checkpoint
```

Her modül **bağımsız çağırılabilir**; `ProjectLayout` ortak path kontratını taşır. Operatör arası state YAML/JSON dosyalarında (raw_manifest.jsonl, splits/*.jsonl, conf/voxcpm2_lora.yaml, run_metadata.json) yaşar — Python global'ları yok.

### CLI sürücüsü

`scripts/finetune.py` 7 subcommand sunar (`transcribe`, `validate-manifest`, `split-manifest`, `write-config`, `train`, `infer`, `export`). Her komut idempotent + bağımsız resume edilebilir:

```bash
NEUROVOICE_DEEPGRAM_API_KEY=sk_live_... \
  python scripts/finetune.py transcribe \
    --root /finetune --voice tr-warm-storyteller-v1

python scripts/finetune.py validate-manifest --voice tr-warm-storyteller-v1
python scripts/finetune.py split-manifest    --voice tr-warm-storyteller-v1
python scripts/finetune.py write-config      --voice tr-warm-storyteller-v1 \
  --model-dir /models/VoxCPM2 --vram-gb 24

python scripts/finetune.py train  --voice tr-warm-storyteller-v1 \
  --voxcpm-repo /opt/VoxCPM

python scripts/finetune.py infer  --voice tr-warm-storyteller-v1 \
  --model-dir /models/VoxCPM2

python scripts/finetune.py export --voice tr-warm-storyteller-v1 \
  --gpu L4 --vram-gb 24
```

### Notebook akıbeti

`notebooks/04-voxcpm2-lora-finetune-colab.ipynb` → `notebooks/archive/04-voxcpm2-lora-finetune-colab.ipynb`. **Silinmedi**; tarihsel referans + Colab-spesifik setup adımları (drive mount, getpass) için canlı tutuldu. Yeni operatör akışı `scripts/finetune.py` CLI'sini kullanır.

## Sebep

- **Re-run edilebilirlik:** CI'de, local dev'de, başka bir bulut sağlayıcıda — Colab'a bağımlılık yok. Sadece `transcribe` subcommand'ı external API (Deepgram); diğerleri offline.
- **Idempotency + resume:** Her subcommand'in girdisi disk üzerinde durur, çıktısı disk üzerine yazılır. Yarıda kesilen pipeline tek subcommand re-run'ı ile devam eder.
- **Test edilebilirlik:** `ProjectLayout`, VRAM preset seçimi, step budget, split logic — hepsi pure function; Codex bunlara birim test yazabilir (`old_tests/`'tekiler kapsamda değil).
- **CLAUDE.md nirengi 9 hizalama:** Adapter eğitiminin standart bir runner'ı oldu; çıktı `voice_id-lora-latest.zip` artifact'i ile ADR-7'deki Voice manifest `adapter` field'ına bağlanabilir.
- **VRAM-aware default'lar:** L4 / L40S / A100 ayrımına göre `batch_size`, `grad_accum_steps`, `max_batch_tokens` preset'leri. Operatör manuel tuning yapmaz; override için hâlâ CLI flag açık.
- **Brand-neutral:** Eski "neeko-proto-v0" hard-coded'i kalktı; voice_id CLI parametre olarak gelir.

## Etkilenen dosyalar

| Dosya | Değişiklik |
| --- | --- |
| `src/finetune/{__init__,project,transcribe,manifest,config,train,inference,export}.py` | **Yeni** — 8 dosya, pipeline paketi |
| `scripts/finetune.py` | **Yeni** — 7-subcommand CLI |
| `pyproject.toml` | `[tool.setuptools.packages.find]` `finetune*` (+`eval*` da eklendi) |
| `notebooks/04-voxcpm2-lora-finetune-colab.ipynb` | `git mv → notebooks/archive/` |

## Bilinçli kapsam dışı (gelecek ADR'leri)

- **Training loop'un kendisini içselleştirme.** VoxCPM upstream `scripts/train_voxcpm_finetune.py` script'ine subprocess ile gidiyoruz. Loop'u içeri taşımak `accelerate`/`argbind` dist deps'i platform'a sokar; gateway/worker süreçleri için sapmadır. Training-host'lar zaten VoxCPM repo'sunu yüklü tutar.
- **Tenant-aware project storage.** Şu an `ProjectLayout` filesystem-local; çoklu tenant fine-tune isteklerini izole etmek için R2/postgres backed storage gerekir. Production multi-tenant fine-tune servisi ayrı ADR konusu.
- **Eval pin entegrasyonu.** `infer` subcommand WAVs üretiyor; bunları `voices.eval_metrics` JSONB sütununa (ADR-7) yazan adım henüz yok — eval ekibinin metric scoring kontratı netleştiğinde.
- **Watermark generation.** `voices.watermark_key_id` field'ı v2 manifest'te var; gerçek watermark imzalayan adım fine-tune değil, **runtime synthesis** sorumluluğu — başka bir ADR.

## Doğrulama

- `ruff check src/finetune/ scripts/finetune.py` → yeşil.
- 8/8 finetune modülü import smoke (pure modules + lazy-import voxcpm/requests).
- CLI `--help` çıktısı tüm 7 subcommand'i listeliyor.
- Behavior smoke: VRAM preset bounds (16→small, 24→medium, 48→large), step budget bounds (15→300, 45→500, 90→800, 180→1000), `split_manifest` 20-record dataset → train=16 val=2 test=2 (tutarlı seed).
- `build_lora_config(train_minutes=45, vram_gb=24)` → batch_size=4, max_steps=500 (medium preset, "30-60 min" lane).

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| Notebook'u olduğu gibi tut, sadece archive et | Pipeline tekrar-koşulamaz state'te kalır; first-class Python paketi olmadan modüler test/extend imkânsız |
| Training loop'u da içselleştir | `accelerate`/argbind/dist deps'i platform'a sokmak yanlış sınır; training-host'lar VoxCPM repo'sunu zaten taşır |
| Pipeline'ı Airflow / Prefect DAG'ı yap | Tek-bir-makine fine-tune için aşırı; CLI subcommand zinciri yeterli. Multi-tenant + queue gelirse o zaman düşün |
| Notebook 04'ü sil | Colab-spesifik setup adımları (drive mount, getpass) hâlâ değerli referans; archive lossless tutar |

## İlgili

- ADR-6 — lang_packs/ — pluggable language pack frontend
- ADR-7 — voice manifest schema v2 — `adapter` / `eval_pin` field'larını dolduran bu pipeline
- CLAUDE.md nirengi 9 — voice = `(base_model_id, adapter, lexicon, watermark, eval_pin)` tuple; fine-tune adapter'ı üretir
- Notebook arşivi: `notebooks/archive/04-voxcpm2-lora-finetune-colab.ipynb`
