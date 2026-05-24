# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`neeko-voice`, Neeko'nun (ve genişletilmiş çerçevede NQAI'nin) Türkçe + çocuk-yönelimli TTS yığınını uçtan uca yöneten bağımsız git deposudur. Üst-katman `/home/alfonso/neeko-firmware/CLAUDE.md` 7 disiplin kuralı geçerlidir; aşağıdakiler bu repoya özel ek disiplinlerdir.

## Şu anki durum

**Platform v0.1 release** — repo çalıştırılabilir bir TTS API'sı. Chatterbox Multilingual üstüne 5-slot voice catalog + FastAPI HTTP/streaming + Bearer auth + sentence-chunked WAV streaming. Detay: [docs/architecture/platform-v0.1.md](docs/architecture/platform-v0.1.md).

| Komut | Ne yapar |
| --- | --- |
| `pip install -e ".[dev]"` | dev + test bağımlılıkları |
| `PYTHONPATH=src python -m pytest` | 52 test (frontend + API smoke, model stub'lu) |
| `PYTHONPATH=src python -m uvicorn server.main:app` | server'ı lokalde başlat (`.env` ile `NQAI_API_KEYS` zorunlu) |
| `python scripts/smoke_test.py --base-url ... --api-key ...` | 10-cümle × N-voice eval, per-call RTF + WAV dump |
| `python scripts/bootstrap_voices.py --base-url ... --api-key ...` | `configs/seed_voices.yaml` üzerinden 5-slot toplu enroll |
| Colab → [notebooks/03-platform-server-colab.ipynb](notebooks/03-platform-server-colab.ipynb) | T4/A100'da server + cloudflared tunnel, ~6-8 dk |

Türkçe SFT + per-speaker LoRA Faz-2'ye ertelendi — bkz. [docs/architecture/platform-v0.1.md](docs/architecture/platform-v0.1.md) "Faz-2'ye giden yol".

Araştırma katmanı paralel devam ediyor:

- `docs/research/02-distilled-findings.md` — NQAI ses omurgası v0.1 sentezi (12 karar, Faz-1/2/3 yol haritası)
- `docs/decisions/README.md` — stratejik karar log'u (en yeni: 2026-05-24 platform v0.1)
- `notebooks/00..02-*.ipynb` — Chatterbox + VoxCPM2 baseline + voice clone demo'ları

## Çalışma akışı (sıkı sırada)

1. Araştırma brief (`docs/research/00-...`) → harici LLM/research çıktısı `docs/research/01-...` olarak iner
2. Damıtma `docs/research/02-distilled-findings.md` altında kürate edilir
3. Damıtmadan karar `docs/decisions/README.md`'ye **satır olarak** eklenir (en yeni üstte; sütunlar: Tarih · Konu · Karar · Gerekçe · Etki)
4. Karardan deney: `experiments/YYYY-MM-DD-<slug>/{config.yaml, log.md, output/}`
5. Deney → `src/eval/` üzerinden ölçü → yeni karar veya iterasyon

**Önce karar, sonra kod.** Mimari/model/eval değişiklikleri decision log'a satır olmadan kodlanmaz.

## Üç-katmanlı çekirdek mimari

[docs/research/02-distilled-findings.md](docs/research/02-distilled-findings.md) bölüm 4'teki spec. v0.1'de katman 1 ve 2 minimum-viable dolduruldu; katman 3 Faz-2'de.

- `src/frontend/` → **`neeko-voice-frontend`** (Türkçe NFKC + cümle segmentasyonu + sayı/kısaltma/sembol açma + code-mix lexicon). v0.1: 32 birim test. Hedef: 300+ golden test, Zemberek + espeak-ng + geminasyon yaması.
- `src/registry/` → **`voice-adapter-registry`** (filesystem-backed manifest YAML + reference audio trim/resample + RLock; CRUD endpoint'ler). v0.1 manifest şeması Faz-2'de `adapter.*`, `watermark.*`, `fingerprint.*`, `eval.*` alanlarıyla genişler.
- `src/server/` → **FastAPI app + Chatterbox engine adapter + streaming**. Engine `BaseSynthEngine` protocol — VoxCPM2 drop-in swap için hazır.
- `src/governance/` (henüz yok) → **`voice-governance-layer`** (KVKK + AudioSeal watermark + voice fingerprint + sözleşme registry + takedown). Faz-2.

Henüz boş yerler: `src/bench/` baseline karşılaştırma scriptleri, `src/finetune/` LoRA (rank 16 / alpha 32 / q,k,v,o_proj / 2000-3000 step), `src/eval/` 5 katmanlı eval suite (UTMOSv2 + NISQA + Whisper-TR-WER + WavLM-SECS + TTSDS2).

## Repo-özel disiplinler

1. **Kaynaklı iddia.** Her sayı / her benchmark / her "X iyi" cümlesi link + tarih ister. "Bence iyi" yetmez: `MOS X / Elo Y / kaynak Z (link, tarih)` formatı. Memory: `feedback_evidence_over_convention`.
2. **Reproducibility.** Her deney = config + seed + commit hash + çıktı klasörü. Yeni notebook çıktısı yeni `experiments/` klasörüne iner — eskilerin üstüne yazılmaz.
3. **Eval kataloğu sabit.** Aynı test cümleleri ([data/test-sets/v0.1-mini.md](data/test-sets/v0.1-mini.md) bugün; Faz-1 hafta 2-3'te v1.0-full = 120 cümle), aynı metrikler. Modeller değişir, ölçü değişmez.
4. **Veri lisansı net.** `data/raw/` ve `data/reference-audio/` içine giren her dosya için kaynak + lisans + (varsa) voice talent kontrat ID'si manifest'te. ElevenLabs çıktısı **referans audio** modunda OK, **LoRA fine-tune training data olarak YASAK** (ToS gri alan — [MANIFEST](data/reference-audio/MANIFEST.md) bunu açıkça belgeler).
5. **Premium = dar domain.** TR + karakter + call-center + child-directed kesişiminde ElevenLabs'ı geçmek; **genel TTS yarışına girmek yasak**. Ticari TTS API'lar (ElevenLabs/OpenAI/Google/Azure) "rakip" değil, sadece benchmark referansı.
6. **NEEKO değil NQAI ses omurgası.** Mimariyi multi-product baştan kur. Tek karaktere özel hardcode yok — `voice-adapter-registry`'de yeni karakter = yeni YAML, yeni kod değil.

## Klasör yapısı

| Yol | Ne yapar |
| --- | --- |
| `docs/research/` | Araştırma brief'leri, dış çıktılar (01-*), damıtmalar (02-*) |
| `docs/decisions/` | Stratejik karar log'u (en yeni üstte) |
| `docs/architecture/` | Pipeline, model stack, eval, latency budget belgeleri (damıtma sonrası dolacak) |
| `docs/character/` | NEEKO karakter spec'i ([neeko-v1-spec.md](docs/character/neeko-v1-spec.md)), casting brief, voice talent outreach şablonları |
| `docs/legal/` | KVKK + FSEK + voice talent rider taslakları |
| `src/frontend/` | Türkçe text frontend (NFKC + cümle segmentasyonu + sayı/kısaltma/sembol/code-mix) |
| `src/registry/` | Voice manifest YAML CRUD + reference audio I/O |
| `src/server/` | FastAPI app, Chatterbox engine adapter, auth, streaming |
| `src/g2p/` | (boş) Faz-2'de Zemberek + espeak-ng + geminasyon yaması burada |
| `src/bench/` | (boş) Baseline model karşılaştırma scriptleri |
| `src/finetune/` | (boş) LoRA + adaptation scriptleri |
| `src/eval/` | (boş) UTMOSv2 / NISQA / Whisper-TR-WER / WavLM-SECS / TTSDS2 |
| `configs/voices/` | Voice manifest YAML'ları (seed: `neeko-v01.yaml`) |
| `configs/seed_voices.yaml` | Bootstrap toplu enroll için 5-slot katalog |
| `scripts/` | `bootstrap_voices.py`, `smoke_test.py` |
| `tests/` | Frontend birim + API smoke (52 test) |
| `data/phonemes/` | Türkçe fonetik sözlük + kural seti + NEEKO lexicon overrides |
| `data/test-sets/` | Kürate test cümleleri (versiyonlu) |
| `data/casting-prompts/` | Voice talent audition prompt pack'leri |
| `data/reference-audio/` | Onaylı referans sesler + MANIFEST (NEEKO v0.1 ElevenLabs köprü sesi burada) |
| `data/raw/` | gitignore'lu — voice talent ham kayıtları + MANIFEST.md |
| `notebooks/` | Kaggle / Colab keşif defterleri (her biri tek amaca odaklı) |
| `experiments/` | Her deney: `YYYY-MM-DD-<slug>/{config.yaml, log.md, output/}` |

## Notebook'ları çalıştırma

Notebook'lar lokal yerine **Kaggle T4 x2** (16 GB VRAM) veya Google Colab üzerinde koşturulur — lokal makine düşük VRAM. Talimatlar her notebook'un başında ve [notebooks/README.md](notebooks/README.md)'de:

1. Kaggle'da yeni notebook → Settings → Accelerator → `GPU T4 x2` (kritik, yoksa CPU'da 30+ dk)
2. `00-chatterbox-tr-demo.ipynb` veya `01-voxcpm2-tr-demo.ipynb` import et → Run All
3. İlk run ~10-15 dk (model indirme + 10 cümle sentezleme)
4. Çıktı zip'i indir → `experiments/<tarih>-<model>/output/` altına çıkar → `log.md` aç ve izlenimleri yaz

Tipik hatalar: HF rate limit (5 dk bekle), CUDA OOM (P100'e geç veya CPU fallback), UTF-8 bozulması (Türkçe karakterler).

## Hassas veri ve sınırlar

- `data/raw/` gitignore'lu — voice talent ham kayıtları **commit edilmez**
- Voice talent kontratları + IP belgesi → `NeuroQubit_NEEKO/private/` (bu repo dışı, üst-katmanda)
- API anahtarları (HuggingFace, RunPod, Lambda Labs, WandB, ElevenLabs) → `.env`, asla commit edilmez
- Üst-katman karar log'unu burada tekrar tutma — TTS dışı kararlar `NeuroQubit_NEEKO/admin/decision-log.md`'de
- Henüz kaynaklanmamış model/metrik için kod yazma — önce decision satırı, sonra config, sonra kod

## Üst katmanla ilişki

`/home/alfonso/neeko-firmware/` workspace'inde `neeko_server/` (backend), `NeuroQubit_NEEKO/` (iş), `neeko-design-framework/` (tasarım) ile aynı seviyede bağımsız repo. NEEKO + NIVA + NeuroCourse + NARO dört üründe ortak ses omurgası bu repodan akar; backend entegrasyonu `neeko_server/`'da yapılır, kontrat/finans/strateji `NeuroQubit_NEEKO/`'da tutulur.
