# Neeko-Voice Notebooks

Kaggle / Colab / lokal üzerinde koşturulan keşif notebook'ları. Her notebook **tek bir amaca odaklı**, çıktıları `experiments/<tarih>-<slug>/` altına gider.

## Mevcut notebook'lar

| Dosya | Amaç | Donanım | Süre |
| --- | --- | --- | --- |
| [`00-chatterbox-tr-demo.ipynb`](00-chatterbox-tr-demo.ipynb) | İlk Türkçe demo, Chatterbox Multilingual (MIT) ile 10 cümle sentezleme | Kaggle T4 x2 (16 GB VRAM) | ~10 dk |
| [`01-voxcpm2-tr-demo.ipynb`](01-voxcpm2-tr-demo.ipynb) | Aynı 10 cümle, VoxCPM2 (Apache 2.0, 2B param) ile — Chatterbox'a paralel ikinci aday | Kaggle T4 x2 (16 GB VRAM) | ~15 dk |

Sıradakiler (henüz yazılmadı):
- `02-cosyvoice2-tr-demo.ipynb` — CosyVoice 2 (TR yok, fine-tune öncesi baseline)
- `03-elevenlabs-reference.ipynb` — ElevenLabs API ile referans çıktıları (karşılaştırma)
- `10-side-by-side-eval.ipynb` — yan yana dinleme paneli (Gradio)

## Kaggle'da çalıştırma talimatı (`00-chatterbox-tr-demo`)

1. **Yeni notebook aç** (Kaggle'da yapmışsın zaten).
2. **Sağ panel → Settings → Accelerator → `GPU T4 x2`** seç. (Bu kritik — yoksa CPU'da 30+ dakika sürer.)
3. **Dosyayı yükle:**
   - Yöntem A: File → Import Notebook → bu dosyayı (`00-chatterbox-tr-demo.ipynb`) yükle.
   - Yöntem B: Hücreleri tek tek kopyala-yapıştır.
4. **Run All** (üst menüde "Run All" veya Ctrl+F9).
5. Bekle — ilk çalıştırmada model indirme ~3-6 dakika sürer, sonra 10 cümle ~2-3 dakika.
6. **Sağ panel → Output → `neeko-demo-v0.1.zip`** indir.
7. Bilgisayarında aç, .wav dosyalarını dinle.

## Çıktıyı nereye koymalı

İndirdiğin `neeko-demo-v0.1.zip`'i şuraya çıkar:

```
experiments/2026-05-19-chatterbox-baseline/output/
    01_oyun.wav
    02_uyku.wav
    ...
    10_hikaye.wav
    metadata.json
```

Sonra `experiments/2026-05-19-chatterbox-baseline/log.md` aç ve her cümle için ilk izlenimini yaz. Atlas damıtmayı bu çıktılarla birlikte kapatacak.

## Hata olursa

- **`ModuleNotFoundError: chatterbox`**: pip install hücresini tekrar çalıştır, sonra kernel restart et.
- **CUDA out of memory**: Settings → Accelerator → GPU P100 dene (Kaggle bazen T4'leri zorlar), veya `device="cpu"` ile çalıştır (yavaş ama çalışır).
- **Model download hata verdi (HF rate limit)**: 5 dakika bekle, hücreyi yeniden çalıştır.
- **Türkçe karakterler bozuk (ş, ğ, ı)**: kaynak metin doğru kodlamada mı kontrol et (UTF-8); notebook bu konuda zaten doğru.
