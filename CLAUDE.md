# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`neuro-voice` — NQAI'nin Türkçe + voice-cloning + streaming TTS yığını. **VoxCPM2 (Apache 2.0, OpenBMB, 2B param)** üzerine voice catalog + Türkçe text frontend + sentence-chunked streaming API. Üst-katman `/home/alfonso/neeko-firmware/CLAUDE.md` 7 disiplin kuralı geçerlidir; aşağıdakiler bu repoya özel ek disiplinlerdir.

## Şu anki durum

**Platform v0.2 release** — repo çalıştırılabilir bir VoxCPM2 streaming TTS API'sı. 5-slot voice catalog + FastAPI HTTP/streaming + Bearer auth + sentence-chunked 48 kHz WAV streaming.

Detay:
- Mimari → [docs/architecture/platform-v0.2.md](docs/architecture/platform-v0.2.md)
- VoxCPM2 API yüzeyi + parametre tuning + LoRA hattı → [docs/architecture/voxcpm2-integration.md](docs/architecture/voxcpm2-integration.md)

| Komut | Ne yapar |
| --- | --- |
| `pip install -e ".[dev]"` | dev + test bağımlılıkları |
| `python -m pytest` | 55 test (frontend + API smoke + seed lock, VoxCPM stub'lu) |
| `PYTHONPATH=src python -m uvicorn server.main:app` | server'ı lokalde başlat (`.env` ile `NQAI_API_KEYS` zorunlu, ~8 GB VRAM) |
| `python scripts/smoke_test.py --base-url ... --api-key ...` | 10-cümle × N-voice eval, per-call RTF + WAV dump |
| `python scripts/bootstrap_voices.py --base-url ... --api-key ...` | `configs/seed_voices.yaml` üzerinden 5-slot toplu enroll |
| Colab → [notebooks/03-platform-server-colab.ipynb](notebooks/03-platform-server-colab.ipynb) | T4/A100'da server + cloudflared tunnel, ~6-8 dk |

Türkçe SFT + per-character LoRA Faz 2-3'e ertelendi — bkz. [platform-v0.2.md "Faz hattı"](docs/architecture/platform-v0.2.md) ve [voxcpm2-integration.md §10](docs/architecture/voxcpm2-integration.md).

Araştırma katmanı paralel devam ediyor:

- `docs/research/02-distilled-findings.md` — NQAI ses omurgası v0.1 sentezi (12 karar, Faz-1/2/3 yol haritası)
- `docs/decisions/README.md` — stratejik karar log'u (en yeni: 2026-05-24 VoxCPM2 birincil model + Chatterbox bırakıldı)
- `notebooks/01-voxcpm2-tr-demo.ipynb` — VoxCPM2 baseline 10 cümle (Kaggle T4)
- `notebooks/03-platform-server-colab.ipynb` — full platform deploy (Colab)

## Çalışma akışı (sıkı sırada)

1. Araştırma brief (`docs/research/00-...`) → harici LLM/research çıktısı `docs/research/01-...` olarak iner
2. Damıtma `docs/research/02-distilled-findings.md` altında kürate edilir
3. Damıtmadan karar `docs/decisions/README.md`'ye **satır olarak** eklenir (en yeni üstte; sütunlar: Tarih · Konu · Karar · Gerekçe · Etki)
4. Karardan deney: `experiments/YYYY-MM-DD-<slug>/{config.yaml, log.md, output/}`
5. Deney → `src/eval/` üzerinden ölçü → yeni karar veya iterasyon

**Önce karar, sonra kod.** Mimari/model/eval değişiklikleri decision log'a satır olmadan kodlanmaz.

## Üç-katmanlı çekirdek mimari

[docs/research/02-distilled-findings.md](docs/research/02-distilled-findings.md) bölüm 4'teki spec. v0.2'de katman 1 ve 2 minimum-viable dolduruldu; katman 3 Faz 3'te.

- `src/frontend/` → **`neeko-voice-frontend`** (Türkçe NFKC + cümle segmentasyonu + sayı/kısaltma/sembol açma + code-mix lexicon). v0.2: 32 birim test. Hedef: 300+ golden test, Zemberek + espeak-ng + geminasyon yaması + style mode tag enjekte etme.
- `src/registry/` → **`voice-adapter-registry`** (filesystem-backed manifest YAML + reference audio trim/resample to 16 kHz mono + RLock; CRUD endpoint'ler). v0.2 manifest şeması Faz 3'te `adapter.*`, `watermark.*`, `fingerprint.*`, `eval.*` alanlarıyla genişler.
- `src/server/` → **FastAPI app + VoxCPM2 engine adapter + streaming**. Engine `BaseSynthEngine` protocol — Türkçe-SFT'li checkpoint veya başka model drop-in swap için hazır.
- `src/governance/` (henüz yok) → **`voice-governance-layer`** (KVKK + AudioSeal watermark + voice fingerprint + sözleşme registry + takedown). Faz 3.

Henüz boş yerler: `src/bench/` (Faz 1: baseline karşılaştırma scriptleri), `src/finetune/` (Faz 3: VoxCPM2 LoRA `train_voxcpm_finetune.py` adapter), `src/eval/` (Faz 1: 5-katmanlı eval suite — UTMOSv2 + NISQA + Whisper-TR-WER + WavLM-SECS + TTSDS2).

## Repo-özel disiplinler

1. **Kaynaklı iddia.** Her sayı / her benchmark / her "X iyi" cümlesi link + tarih ister. "Bence iyi" yetmez: `MOS X / Elo Y / kaynak Z (link, tarih)` formatı. Memory: `feedback_evidence_over_convention`.
2. **Reproducibility.** Her deney = config + seed + commit hash + çıktı klasörü. Yeni notebook çıktısı yeni `experiments/` klasörüne iner — eskilerin üstüne yazılmaz.
3. **Eval kataloğu sabit.** Aynı test cümleleri ([data/test-sets/v0.1-mini.md](data/test-sets/v0.1-mini.md) bugün; Faz 1'de v1.0-full = 120 cümle), aynı metrikler. Modeller değişir, ölçü değişmez.
4. **Veri lisansı net.** `data/raw/` ve `data/reference-audio/` içine giren her dosya için kaynak + lisans + (varsa) voice talent kontrat ID'si manifest'te. ElevenLabs çıktısı **referans audio** modunda OK, **LoRA fine-tune training data olarak YASAK** (ToS gri alan).
5. **Premium = dar domain.** TR + karakter + call-center + child-directed kesişiminde ElevenLabs'ı geçmek; **genel TTS yarışına girmek yasak**. Ticari TTS API'lar (ElevenLabs/OpenAI/Google/Azure) "rakip" değil, sadece benchmark referansı.
6. **NEEKO değil NQAI ses omurgası.** Mimariyi multi-product baştan kur. Tek karaktere özel hardcode yok — `voice-adapter-registry`'de yeni karakter = yeni YAML, yeni kod değil.
7. **Birincil base model: VoxCPM2.** Adapter pattern (`BaseSynthEngine`) arkasında yaşıyor; alternatifler bench/eval üzerinden ölçülmeden swap yok.

## Klasör yapısı

| Yol | Ne yapar |
| --- | --- |
| `docs/research/` | Araştırma brief'leri, dış çıktılar (01-*), damıtmalar (02-*) |
| `docs/decisions/` | Stratejik karar log'u (en yeni üstte) |
| `docs/architecture/` | `platform-v0.2.md` (mimari), `voxcpm2-integration.md` (model + LoRA cheatsheet) |
| `docs/character/` | NEEKO karakter spec'i, casting brief, voice talent outreach şablonları |
| `docs/legal/` | KVKK + FSEK + voice talent rider taslakları |
| `src/frontend/` | Türkçe text frontend (NFKC + cümle segmentasyonu + sayı/kısaltma/sembol/code-mix) |
| `src/registry/` | Voice manifest YAML CRUD + reference audio I/O (16 kHz mono) |
| `src/server/` | FastAPI app, **VoxCPM2 engine adapter**, auth, streaming |
| `src/g2p/` | (boş) Faz 1: Zemberek + espeak-ng + geminasyon yaması |
| `src/bench/` | (boş) Faz 1: baseline karşılaştırma scriptleri |
| `src/finetune/` | (boş) Faz 3: VoxCPM2 LoRA pipeline (`train_voxcpm_finetune.py` wrap) |
| `src/eval/` | (boş) Faz 1: UTMOSv2 / NISQA / Whisper-TR-WER / WavLM-SECS / TTSDS2 |
| `configs/voices/` | Voice manifest YAML'ları (seed: `neeko-v01.yaml`) |
| `configs/seed_voices.yaml` | Bootstrap toplu enroll için 5-slot katalog (1 NEEKO + 2 NIVA + 2 NeuroCourse) |
| `scripts/` | `bootstrap_voices.py`, `smoke_test.py` |
| `tests/` | Frontend birim + API smoke + seed lock (55 test) |
| `data/phonemes/` | Türkçe fonetik sözlük + kural seti + NEEKO lexicon overrides |
| `data/test-sets/` | Kürate test cümleleri (versiyonlu) |
| `data/casting-prompts/` | Voice talent audition prompt pack'leri |
| `data/reference-audio/` | Onaylı referans sesler + MANIFEST (NEEKO v0.1 köprü sesi) |
| `data/raw/` | gitignore'lu — voice talent ham kayıtları + MANIFEST.md |
| `notebooks/` | Colab/Kaggle defterleri (`01-voxcpm2-tr-demo`, `03-platform-server-colab`) |
| `experiments/` | Her deney: `YYYY-MM-DD-<slug>/{config.yaml, log.md, output/}` |

## Notebook'ları çalıştırma

VoxCPM2 ~8 GB VRAM ister — lokal makine yetmez, **Colab T4/A100** veya **Kaggle T4 x2** üzerinde koşturulur. Talimatlar her notebook'un başında ve [notebooks/README.md](notebooks/README.md)'de:

1. Colab'da Runtime → Change runtime type → **GPU (T4 veya A100)**
2. `01-voxcpm2-tr-demo.ipynb` (baseline) veya `03-platform-server-colab.ipynb` (full server) aç
3. Cell 1 (install) → kernel restart → cell 3+
4. `03` notebook'unda ~6-8 dakika sonra `https://*.trycloudflare.com` public URL alırsın

Tipik hatalar: HF rate limit (5 dk bekle), CUDA OOM (A100'e geç), UTF-8 bozulması (Türkçe karakterler).

## Hassas veri ve sınırlar

- `data/raw/` gitignore'lu — voice talent ham kayıtları **commit edilmez**
- Voice talent kontratları + IP belgesi → `NeuroQubit_NEEKO/private/` (bu repo dışı, üst-katmanda)
- API anahtarları (HuggingFace, RunPod, Lambda Labs, WandB) → `.env`, asla commit edilmez
- Üst-katman karar log'unu burada tekrar tutma — TTS dışı kararlar `NeuroQubit_NEEKO/admin/decision-log.md`'de
- Henüz kaynaklanmamış model/metrik için kod yazma — önce decision satırı, sonra config, sonra kod
- **VoxCPM2 yasak kullanımlar** (OpenBMB ToS): kişi taklidi (impersonation), dolandırıcılık, dezenformasyon. AI label zorunluluğu jurisdiction'a göre.

## Üst katmanla ilişki

`/home/alfonso/neeko-firmware/` workspace'inde `neeko_server/` (backend), `NeuroQubit_NEEKO/` (iş), `neeko-design-framework/` (tasarım) ile aynı seviyede bağımsız repo. NEEKO + NIVA + NeuroCourse + NARO dört üründe ortak ses omurgası bu repodan akar; backend entegrasyonu `neeko_server/`'da yapılır, kontrat/finans/strateji `NeuroQubit_NEEKO/`'da tutulur.
