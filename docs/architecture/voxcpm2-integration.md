# VoxCPM2 Integration — API Cheatsheet + Tuning

**Karar tarihi:** 2026-05-24
**Birincil base model:** [`openbmb/VoxCPM2`](https://huggingface.co/openbmb/VoxCPM2)
**Lisans:** Apache-2.0 (ticari kullanım serbest)
**Status:** v0.2 platform üzerine bu dosya kanonik referans

Bu belge engine yazarken / LoRA eğitirken / parametre tunelarken bakılacak yerdir. Tek doğru: VoxCPM2 GitHub + HF model kartı. Burası **bizim** entegrasyon kararlarımız + tuning notları.

---

## 1. Model özeti

| Boyut | Değer | Yorum |
|---|---|---|
| **Mimari** | Tokenizer-free Diffusion Autoregressive (MiniCPM-4 backbone + flow matching) | LoRA hattı standart HF stack ile çalışıyor — büyük avantaj |
| **Parametre** | 2B (bfloat16) | ~4 GB ağırlık dosyası |
| **VRAM** | ~8 GB | T4 (16 GB), L4, A100, H100 hepsi rahat |
| **Sample rate (output)** | **48 kHz** (AudioVAE V2) | Studio-grade; downstream'de 24 kHz / 16 kHz'e downsample edilebilir |
| **Sample rate (reference input)** | 16 kHz mono WAV önerilir | Registry trim/resample'ı buna göre yapılıyor (`NQAI_REF_SR=16000`) |
| **Max sequence length** | 8192 token | Uzun-form 5 dk'yı rahat aşar; bizim cümle segmentasyonu yine doğru karar (prosody) |
| **Diller** | 30 dil (Türkçe dahil), 9 Çince diyalekti | Dil tag'i YOK — text'ten otomatik |
| **Eğitim verisi** | 2M+ saat çok-dilli konuşma | Türkçe internal WER iddiası %1.65 |
| **RTF** | RTX 4090 ~0.30 (standart), ~0.13 (Nano-vLLM) | T4'te tahmini ~0.8-1.2, A100 ~0.4-0.6 |
| **Lisans** | Apache-2.0 | Tam ticari, atribüsyon yeterli |

---

## 2. Install + load

```bash
pip install voxcpm    # Python ≥3.10 <3.13, PyTorch ≥2.5, CUDA ≥12
```

```python
from voxcpm import VoxCPM

model = VoxCPM.from_pretrained(
    "openbmb/VoxCPM2",
    load_denoiser=False,    # opsiyonel post-processing denoiser; v0.2'de KAPALI
)

# Çıkış sample rate'i inner model'in attribute'unda
sr = model.tts_model.sample_rate    # 48000
```

İlk yüklemede ~3-5 GB indirme (HuggingFace cache `~/.cache/huggingface/hub/`).

---

## 3. `generate()` parametreleri (full)

```python
wav: np.ndarray = model.generate(
    text: str,                                # zorunlu — sentezlenecek metin

    # Voice cloning (üç mod)
    reference_wav_path: str | None = None,    # **bizim kullandığımız** — basit zero-shot clone
    prompt_wav_path: str | None = None,       # "ultimate cloning" için ek prompt audio
    prompt_text: str | None = None,           # prompt audio'nun transcript'i

    # Üretim kalitesi / hız trade-off
    cfg_value: float = 2.0,                   # 1.5-3.0 makul, classifier-free guidance gücü
    inference_timesteps: int = 10,            # 6-20 makul, diffusion adım sayısı

    # Pre/post processing
    normalize: bool = True,                   # **bizim KAPATTIĞIMIZ** — kendi TN'imiz var
    denoise: bool = True,                     # output denoiser; v0.2'de KAPALI (load_denoiser=False)

    # Bad-case retry
    retry_badcase: bool = True,
    retry_badcase_max_times: int = 3,
    retry_badcase_ratio_threshold: float = 6.0,
)
```

### Bizim default'larımız (engine.py)

| Param | NQAI default | Neden |
|---|---|---|
| `reference_wav_path` | voice manifest → reference_audio (registry resolver) | Voice catalog her isteğe doğru ses dosyasını gönderir |
| `cfg_value` | **2.0** | OpenBMB önerisi; "premium" hissi için 2.5 dener, doğallık için 1.5 |
| `inference_timesteps` | **10** | Kalite/hız tatlı noktası; latency için 6, kalite için 16 |
| `normalize` | **False** | `src/frontend/` Türkçe TN katmanımız (sayı, kısaltma, kod-karışımı, apostrof) daha güvenli; üst üste binmemek için kapatıyoruz |
| `denoise` | **False** | Loader'da `load_denoiser=False`; production'da output zaten temiz, ekstra compute istemiyor |
| `retry_badcase` | **True** | Kendiliğinden retry, +1-3 saniye worst case |

Bu varsayılanlar `src/server/engine.py:VoxCPM2Engine.__init__` üzerinden override edilebilir; ileride per-voice manifest'e `engine_params` alanı eklenecek (Faz 1).

---

## 4. Voice cloning modları

### 4.1 Basic Cloning (bizim kullandığımız)

```python
wav = model.generate(
    text="Merhaba, ben Neeko.",
    reference_wav_path="data/reference-audio/neeko-v0.1-reference.mp3",
)
```

Tek 10-15 saniyelik referans yeterli. Model timbre + stil + accent'i taklit eder. **NQAI v0.2'nin tüm voice cataloğu bu modu kullanıyor.**

### 4.2 Voice Design (referanssız, parantezli prompt)

```python
wav = model.generate(
    text="(A young woman, gentle and sweet voice)"
         "Hoş geldiniz, ben sizin Türkçe asistanınızım."
)
```

Karakter referans audio'su olmadan, doğal-dil tarifle ses yaratır. **Variability yüksek** — aynı prompt'tan farklı çağrılarda farklı ses çıkabilir. Bizim için kullanım: prototip / demo / placeholder seslerin doğmasında.

### 4.3 Ultimate Cloning (üç-parça)

```python
wav = model.generate(
    text="...",
    reference_wav_path="ref.wav",       # timbre kaynağı
    prompt_wav_path="prompt.wav",       # bonus continuation prompt
    prompt_text="prompt audio'nun transcript'i",
)
```

Karakter sesi nuansını en üst seviye korumak için. **Voice talent kayıt sonrası Faz 4'te denenmeli** — özellikle long-form (5 dk+) drift kontrolü için faydalı olabilir.

### 4.4 Stil yönlendirmeli (Controllable Cloning)

```python
wav = model.generate(
    text="(whispering, slow)Şşş, uyumadan önce sana bir hikaye anlatayım.",
    reference_wav_path="neeko-v0.1-reference.mp3",
)
```

Referans audio + parantezli stil tag birleşimi. NEEKO 5 mod (storytelling/lesson/play/sleep/Q&A) için bu yaklaşım test edilecek — şu an `src/frontend/` style tag inject etmiyor, **Faz 1 frontend olgunlaşmasında** style mode tag'leri eklenir.

---

## 5. Streaming

VoxCPM2 yerleşik chunk-bazlı streaming API'sine sahip:

```python
import numpy as np

chunks = []
for chunk in model.generate_streaming(
    text="...",
    reference_wav_path="...",
    cfg_value=2.0,
    inference_timesteps=10,
):
    chunks.append(chunk)            # numpy array
wav = np.concatenate(chunks)
```

### Bizim streaming politikamız (v0.2)

`src/server/engine.py:VoxCPM2Engine.synthesize_stream` şu anda **cümle-bazlı** yield ediyor:

1. `normalize_text()` → Türkçe TN
2. `segment_sentences()` → cümlelere böl
3. Her cümle için `model.generate()` (non-streaming) çağır
4. PCM int16 chunk olarak HTTP'ye akıt + 200 ms inter-sentence silence

**Neden cümle-bazlı, VoxCPM2'nin kendi streaming'i değil:**

- Kullanıcı UI için "cümle bittiğinde caption güncelle" gibi semantic boundary lazım
- Cümle başına prosody coherence — modelin tek bir uzun ifadede stil drift'i azaltılır
- 200 ms silence inject TR child-directed prozodi için doğal (Mod 4 sleep özellikle)

**v0.3 yol haritası:** model'in kendi `generate_streaming()`'ini cümle-içi sub-chunk'lar için kullanmak — ilk byte latency'yi yarıya indirir, özellikle uzun cümlelerde.

---

## 6. Türkçe kalite ve test setleri

**Resmi iddia:** Internal benchmark Türkçe ASR-WER %1.65 (OpenBMB raporu, doğrulanmamış).

**Bizim taban ölçümümüz (gerekli):** Henüz yok. Faz 1 ilk işi:
- 120-cümlelik `v1.0-full` test seti üzerinde Whisper-large-v3-turbo-turkish ile WER
- UTMOSv2 (naturalness)
- WavLM-SECS (same-character / cross-character)
- Side-by-side panel: NEEKO referans audio vs VoxCPM2 zero-shot clone

Sonuçlar `experiments/<tarih>-voxcpm2-baseline-eval/metadata.json` ve `decision-log` satırı.

### Bilinmeyenler (Faz 1'de kapanacak)

1. Türkçe içinde **çocuk-yönelimli prozodi** (CDS) ne kadar destekleniyor — sample yapılmadı
2. Sayı / kısaltma / kod-karışımı **normalize=False** ile bizim TN'imizden gelen text'i ne kadar temiz okuyor
3. 5 dk uzun-form'da **drift davranışı** (Chatterbox'ta sorunluydu)
4. `cfg_value` ve `inference_timesteps` Türkçe için optimal nerede

---

## 7. LoRA fine-tune hattı

VoxCPM2 resmi olarak LoRA fine-tune destekliyor:

```bash
# Repo'da:
python scripts/train_voxcpm_finetune.py \
    --config_path conf/voxcpm_v2/voxcpm_finetune_lora.yaml

# Veya WebUI:
python lora_ft_webui.py
```

**Bizim Faz 3'te yapacağımız:**

1. NQAI'nin Türkçe corpus'unu (CommonVoice TR + audiobook + voice talent kayıtları) toplama
2. MFA forced alignment + 16 kHz mono normalize
3. `conf/voxcpm_v2/voxcpm_finetune_lora.yaml`'i NQAI config'ine adapte etme
4. RunPod A100 × 2-4 gün training
5. Per-character LoRA → `voice-adapter-registry`'de `adapter.uri` alanı dolar
6. Engine'de adapter hot-load (`base_model + per_request_adapter`)

**Şu an yapılmadı.** `src/finetune/` boş — Faz 3 gelince doldurulur.

---

## 8. Kısıtlamalar (OpenBMB resmi)

- **Yasak kullanımlar:** kişi taklidi (impersonation), dolandırıcılık, dezenformasyon
- **AI-generated content label zorunluluğu** (jurisdiction'a göre)
- **Çok uzun veya yüksek-ifadeli** input'larda zaman zaman dengesizlik
- **Dil başına kalite varyasyonu** (Türkçe 30 dilden biri, en güçlü değil — SFT ile çözmemiz gereken yer)

---

## 9. Bizim engine.py'deki swap noktaları

`src/server/engine.py`'i Faz 3'te şöyle genişleteceğiz:

```python
class VoxCPM2Engine:
    def __init__(self, ..., adapter_uri: str | None = None):
        self._adapter_uri = adapter_uri

    def _load(self):
        from voxcpm import VoxCPM
        self._model = VoxCPM.from_pretrained(self._model_id, ...)
        if self._adapter_uri:
            self._load_lora_adapter(self._adapter_uri)  # PEFT entegrasyonu

    def _generate_one(self, text, reference_path):
        # Faz 3: per-voice manifest'ten cfg/steps overridelarını al
        params = self._voice_engine_params(voice_id)
        return self._model.generate(
            text=text,
            reference_wav_path=str(reference_path),
            cfg_value=params.get("cfg_value", self._cfg_value),
            inference_timesteps=params.get("steps", self._inference_timesteps),
            ...
        )
```

API yüzeyi (`/v1/tts`, `/v1/voices`) **değişmez**.

---

## 10. Faz hattındaki yeri

| Faz | VoxCPM2 ile yapılan |
|---|---|
| 0 (bitti) | Platform iskeleti — Chatterbox'tan VoxCPM2'ye geçildi |
| 1 (bu hafta) | Türkçe baseline eval (WER + UTMOS + SECS), cfg/steps sweep, frontend ↔ normalize:False uyumu doğrulanır |
| 2 (hafta 2-4) | Türkçe SFT — VoxCPM2 base + NQAI Türkçe corpus, RunPod A100 |
| 3 (hafta 3-6) | Voice talent kayıt + per-character LoRA, manifest `adapter.*` doldur, engine adapter hot-load |
| 4 (hafta 5-7) | Multi-process serving + Nano-vLLM accelerated path (RTF 0.13 hedefi) |
| 5 (hafta 7-8) | Production gate, observability, governance |
