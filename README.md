# neuro-voice

NQAI'nin Türkçe + voice-cloning + streaming TTS yığını. **VoxCPM2** (Apache 2.0, OpenBMB, 2B param) üzerine voice catalog + Türkçe text frontend + sentence-chunked streaming API.

## Şu an

**Platform v0.2 hazır** — repo çalıştırılabilir bir TTS API'sı.

- Mimari: [docs/architecture/platform-v0.2.md](docs/architecture/platform-v0.2.md)
- VoxCPM2 entegrasyon detayları: [docs/architecture/voxcpm2-integration.md](docs/architecture/voxcpm2-integration.md)
- Karar log'u: [docs/decisions/README.md](docs/decisions/README.md)
- Damıtma (NQAI ses omurgası vizyonu): [docs/research/02-distilled-findings.md](docs/research/02-distilled-findings.md)

## Hedef

12 ay içinde **Türkçe + 3-7 yaş çocuk konuşması + sürdürülebilir karakter sesi** alt-domain'inde premium kalite. VoxCPM2 base + Türkçe SFT + per-character LoRA + production-grade streaming. Genel TTS yarışına girmiyoruz; dar niche'te derinleşiyoruz.

## Quickstart — Colab (en hızlı)

[notebooks/03-platform-server-colab.ipynb](notebooks/03-platform-server-colab.ipynb)'yi Colab'da aç → T4 / A100 GPU seç → cell 1 → kernel restart → cell 3-9. ~6-8 dakika sonra `https://*.trycloudflare.com` URL'in ve iki API key'in olur. NEEKO köprü sesi referansını Drive root'una koyarsan cell 4'te otomatik enroll eder.

Tek tıklama: <https://colab.research.google.com/github/erdalgumuss/neuro-voice/blob/main/notebooks/03-platform-server-colab.ipynb>

## Quickstart — lokal (GPU varsa, ~8 GB VRAM)

```bash
git clone git@github.com:erdalgumuss/neuro-voice.git
cd neuro-voice
python -m venv .venv && source .venv/bin/activate
pip install -e .                       # üretim
pip install -e ".[dev]"                # + testler

cp .env.example .env
# .env içine en az NQAI_API_KEYS doldur:
#   python -c "import secrets; print('nqai-' + secrets.token_urlsafe(24))"
set -a; source .env; set +a

PYTHONPATH=src python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

İlk request model'i yükler (T4 ~90-120 s, A100 ~45-60 s). Daha temiz: `POST /admin/warmup`.

## API yüzeyi

| Method | Yol | Ne yapar |
|---|---|---|
| GET | `/health` | model yüklenmiş mi, voice sayısı, sürüm |
| POST | `/admin/warmup` | VoxCPM2 ağırlıklarını eager yükle |
| GET | `/v1/voices` | catalog |
| GET | `/v1/voices/{id}` | tek voice manifest |
| POST | `/v1/voices` | yeni voice enroll (multipart: `reference_audio` + form alanları) |
| DELETE | `/v1/voices/{id}` | voice + reference dosyasını sil |
| POST | `/v1/tts` | non-streaming WAV / PCM16 (48 kHz) |
| POST | `/v1/tts/stream` | sentence-chunked streaming WAV / PCM16 |

OpenAPI: server ayaktayken `GET /docs` (Swagger UI) ve `GET /openapi.json`.

```bash
KEY="nqai-..."
URL="https://<your-tunnel>.trycloudflare.com"

# voice catalog
curl -H "Authorization: Bearer $KEY" $URL/v1/voices

# yeni voice enroll (15s referans, WAV/MP3 OK — 16 kHz mono'ya resample edilir)
curl -X POST $URL/v1/voices \
  -H "Authorization: Bearer $KEY" \
  -F voice_id=ayse-warm-01 \
  -F 'display_name=Ayşe (warm)' \
  -F gender=female \
  -F 'style_tags=warm,storyteller' \
  -F reference_audio=@./ayse_ref_15s.wav

# sentezle (48 kHz WAV)
curl -X POST $URL/v1/tts \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Merhaba, ben Neeko. Bugün seninle ne oynayalım?","voice_id":"neeko-v01"}' \
  --output out.wav

# streaming (ffplay anında çalmaya başlar)
curl -X POST $URL/v1/tts/stream \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Bir varmış, bir yokmuş. Çok uzak bir ülkede küçük bir tavşan yaşarmış.","voice_id":"neeko-v01"}' \
  | ffplay -nodisp -autoexit -
```

## Testler

```bash
python -m pytest             # 55 test: frontend + API smoke + seed lock
python -m pytest tests/test_numbers.py -v
```

API smoke testleri VoxCPM2'yi stub'larla — torch dışında ağır bağımlılık istemez. Tam end-to-end için Colab notebook veya GPU'lu lokal.

## Repo disiplini

Üst-katman [`/home/alfonso/neeko-firmware/CLAUDE.md`](../CLAUDE.md) 7 disiplin kuralı + bu repo'nun [CLAUDE.md](CLAUDE.md)'sindeki ek 6 disiplin geçerli. Özet:

- Önce karar (decision log satırı), sonra kod
- Her sayı / her benchmark / her "X iyi" cümlesi link + tarih
- Eval kataloğu sabit, modeller değişir
- Premium = dar domain (TR + karakter + call-center + child-directed), genel TTS yarışı yasak
- NEEKO değil **NQAI ses omurgası** (4 ürün ortak altyapı)
- **Birincil base model: VoxCPM2** (Apache 2.0). Engine adapter pattern arkasında — gerekirse swap edilir.

## Üst katmanla ilişki

Bu repo `/home/alfonso/neeko-firmware/` workspace'inde bağımsız git deposu. `neeko_server/` (backend), `NeuroQubit_NEEKO/` (iş), `neeko-design-framework/` (tasarım) ile aynı seviyede. NEEKO + NIVA + NeuroCourse + NARO dört üründe ortak ses omurgası buradan akar.
