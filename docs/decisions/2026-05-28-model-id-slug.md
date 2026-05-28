# ADR-3 — `model_id` preset slug'larından brand prefix'i kaldır

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** `server.models` registry'sinde iki preset tripleti var: `n`​`qai-voxcpm2-tr-{turbo,hd,character}` (Türkçe-tuned) ve `voxcpm2-{fast,standard,studio}` (generic). İlk üçlü eski brand prefix'i taşıyor. Engine slug'ı (`model_id` request kontratı) **brand-agnostic** olmalı — request kontratının ürün adıyla evrimleşmesi gerekmez. ElevenLabs/MiniMax precedent: `eleven_multilingual_v2`, `speech-01`, `tts-1` — ürün/şirket adı yok, sadece motor + sürüm.

## Karar

Eski → yeni:

| Eski | Yeni |
| --- | --- |
| `n`​`qai-voxcpm2-tr-turbo` | `voxcpm2-tr-turbo` |
| `n`​`qai-voxcpm2-tr-hd` | `voxcpm2-tr-hd` |
| `n`​`qai-voxcpm2-tr-character` | `voxcpm2-tr-character` |

Mevcut diğer triplet (`voxcpm2-fast`, `voxcpm2-standard`, `voxcpm2-studio`) zaten brand-free; aynı convention.

## Sebep

- **Brand-engine ayrımı:** `model_id` engine + dil + preset'i tarif eder; ürün markası ortogonal.
- **Vendor parity:** ElevenLabs/MiniMax/OpenAI tümünde model_id brand-free.
- **`tr` qualifier yeterli:** Türkçe-tuned olduğu `tr-` segmenti ile zaten ayırt edilir; brand prefix'i artıklık.
- **v0.x policy:** Backwards-compat shell yok.

## Etkilenen dosyalar

| Dosya | Değişiklik |
| --- | --- |
| `src/server/models.py` | 3 ModelPreset `model_id` field'ı |
| `src/eval/systems/neurovoice.py` | default `model_id` field |
| `scripts/eval_run.py` | `--neurovoice-model-id` default + help text |
| `notebooks/05-current-architecture-colab-smoke.ipynb` | smoke örneği payload |

**~7 substitution.**

## Doğrulama

1. `ruff check` yeşil.
2. `from server.models import resolve_model; resolve_model("voxcpm2-tr-hd")` döner.
3. `from server.models import DEFAULT_MODEL; assert DEFAULT_MODEL.model_id == "voxcpm2-tr-hd"`.
4. Residue: `grep -rn 'n''qai-voxcpm2-tr-'` → boş (docs/decisions/ hariç).

## İlgili

- [[brand-naming]] — NeuroVoice
- ADR-1: header + env prefix
- ADR-2: eval system slug
