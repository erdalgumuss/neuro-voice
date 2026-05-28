# ADR-2 — Eval harness system slug + dosya/class/CLI hizalaması

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** ADR-1 brand'i (NeuroVoice) public surface'lere yaydı; eval harness ise hâlâ "nqai" iç slug'ı altında yaşıyor. Eval CLI'sı `--systems nqai elevenlabs ...` formunda; rapor satırlarında "nqai" satıcı adı geçiyor; eval system adapter dosyası `src/eval/systems/nqai.py`. Cross-vendor karşılaştırma (NeuroVoice vs ElevenLabs vs MiniMax) raporlarında **sistemin gerçek adıyla** çıkmalı.

> **Kapsam dışı (ayrı ADR'ler):** API key formatı (`nqai_<env>_<14>_<40>`), notebook'taki örnek anahtar prefix'leri (`nqai-admin-*`/`nqai-dev-*`), admin auth cookie isimleri (`nqai_admin_access`/`nqai_admin_refresh`). Bu üç surface farklı blast radius'ta (public client format vs operator surface) ve **ayrı kararla** ele alınacak.

## Karar

1. **Eval system slug:** eski `"n`​`qai"` → yeni **`"neurovoice"`**
   - `elevenlabs`, `minimax` gibi vendor slug'larla paralel okunur.
2. **Dosya rename:** `src/eval/systems/n`​`qai.py` → `src/eval/systems/neurovoice.py` (git mv ile history korunur)
3. **Class rename:** `N`​`QAISystem` → `NeuroVoiceSystem`
4. **CLI args:** `--n`​`qai-voice`, `--n`​`qai-model-id`, `--n`​`qai-base-url` → `--neurovoice-voice`, `--neurovoice-model-id`, `--neurovoice-base-url`
5. **Logger:** `getLogger("neurovoice.eval.systems.n`​`qai")` → `getLogger("neurovoice.eval.systems.neurovoice")`
6. **Default `--neurovoice-model-id` değeri:** model_id slug ADR-3 kapsamında ele alınacağı için default değeri (`n`​`qai-voxcpm2-tr-hd`) bu turda **dokunulmadan korundu**; ADR-3'te birlikte güncellenir.

## Sebep

- **Vendor parity:** Eval raporu "NQAI vs ElevenLabs vs MiniMax" yerine "NeuroVoice vs ElevenLabs vs MiniMax" okunur — kıyas tablosunun semantiği netleşir.
- **Slug ≠ brand:** `"neurovoice"` slug'ı brand-bağımlı görünebilir, ancak ElevenLabs/MiniMax precedent'i tam olarak budur (vendor adı = system slug). Alternatif "in-house" / "primary" gibi soyut isimler raporu yorumlamayı zorlaştırır.
- **v0.x policy:** Backwards-compat shell yok; eski slug fallback'i bırakmayız.
- **Dar kapsam:** ADR-1 geniş kapsamlıydı (6 surface, 50+ dosya). ADR-2 bilinçli olarak 3 dosyaya sıkıştırıldı — API key + admin cookie ayrı tutuldu.

## Etkilenen dosyalar

| Dosya | Değişiklik |
| --- | --- |
| `src/eval/systems/n`​`qai.py` → `src/eval/systems/neurovoice.py` | rename + class + slug + logger + docstring |
| `src/eval/systems/__init__.py` | örnek vendor listesi `"n`​`qai"` → `"neurovoice"` |
| `scripts/eval_run.py` | CLI arg adları + choices + dict key + import yolu + dispatch + üst docstring örneği |

**Hariç tutulanlar (kasıtlı):** API key validator (`api_keys.py`), admin auth cookie isimleri (`admin/router.py`, `main.py` CORS), default model_id değeri.

## Doğrulama

1. `git mv` ile dosya rename — `git log --follow` history korur.
2. `ruff check src/ scripts/` yeşil.
3. `PYTHONPATH=src python -c "from eval.systems.neurovoice import NeuroVoiceSystem; print('ok')"` çalışır.
4. `python scripts/eval_run.py --help` `--neurovoice-voice` görünür.
5. Eski slug residue: `grep -rn '"n''qai"\|--n''qai-voice\|N''QAISystem\|eval\.systems\.n''qai' src/ scripts/` → boş.

## İlgili kararlar

- [[brand-naming]] — NeuroVoice platform adı
- `docs/decisions/2026-05-28-header-and-env-prefix.md` (ADR-1) — header/env rebrand
- **Sonraki:** ADR-3 (planlanan) — `n`​`qai-voxcpm2-tr-*` model_id slug rename
- **Sonraki:** ADR-4 (planlanan) — `n`​`qai_<env>_<14>_<40>` API key format + admin auth cookie isimleri
