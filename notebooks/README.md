# Neeko-Voice Notebooks

Kaggle / Colab / lokal üzerinde koşturulan keşif + deployment notebook'ları. Her notebook **tek bir amaca odaklı**, çıktıları `experiments/<tarih>-<slug>/` altına gider.

## Aktif notebook'lar

| Dosya | Amaç | Donanım | Süre |
| --- | --- | --- | --- |
| [`01-voxcpm2-tr-demo.ipynb`](01-voxcpm2-tr-demo.ipynb) | Türkçe baseline — VoxCPM2 (Apache 2.0, 2B param, 30 dil) ile 10 cümle sentezleme | Kaggle T4 x2 (16 GB VRAM) | ~15 dk |
| [`03-platform-server-colab.ipynb`](03-platform-server-colab.ipynb) | NQAI Voice API server'ı Colab'da kaldır + cloudflared tunnel ile public URL | Colab T4 / A100 | ~6-8 dk cold start |

## Planlanan

- `04-eval-suite-colab.ipynb` — Whisper-TR-WER + UTMOSv2 + WavLM-SECS, voice × test set → JSON + WandB log
- `05-lora-finetune-colab.ipynb` — VoxCPM2 LoRA fine-tune (per-character), `scripts/train_voxcpm_finetune.py` üzerinden
- `06-bench-side-by-side.ipynb` — Gradio panel, A/B dinleme jürisi için

## Çıktıyı nereye koymalı

Notebook'tan indirilen zip'leri `experiments/<tarih>-<slug>/output/` altına çıkarın. Her experiment klasörü = config + log + output üçlüsü ([CLAUDE.md](../CLAUDE.md) reproducibility disiplini).

## Hata olursa

- **`ModuleNotFoundError: voxcpm`** → pip install hücresini tekrar koştur, sonra kernel restart.
- **CUDA out of memory** → VoxCPM2 ~8 GB ister; T4 (16 GB) rahat, ama eğer paylaşımlı session ise Settings → Accelerator → daha büyük GPU dene veya küçük batch.
- **HuggingFace rate limit** → 5 dk bekle, model download cache'i `~/.cache/huggingface/`.
- **Türkçe karakterler bozuk (ş, ğ, ı)** → kaynak metin UTF-8 mi kontrol et.
