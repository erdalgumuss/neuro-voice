# ADR-4 — API key formatı + admin auth cookie isim hizalaması

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** ADR-1 brand'i (NeuroVoice) public surface'lere yaydı; HTTP header'lar `X-NV-*` prefix'inde. İki kalan brand-tutarsızlık var:
  1. **Production API key formatı:** `n`​`qai_<env>_<14>_<40>` — her client'ın authorization header'ında geçen kontrat.
  2. **Admin auth cookie:** `n`​`qai_admin_access` / `n`​`qai_admin_refresh` — admin SPA login flow'unda set edilen JWT cookie isimleri.
- Bu surface'ler ADR-1'den **bilinçli olarak parka çekilmişti** çünkü her ikisi de production-client-impact taşıyor (her API client + admin SPA kırılır). Şimdi tek turda halledilir.

## Karar

### 1. API key formatı

Eski: `n`​`qai_<env>_<14>_<40>` → Yeni: **`nv_<env>_<14>_<40>`**

- `nv_` prefix'i `X-NV-*` header convention'ı ile uyumlu.
- Eski `n`​`qai_` ile karşılaştırıldığında format genişliği aynı (4 → 3 char, 1 char kısaldı).
- Validator regex'leri, generator, parser, docstring'leri tek seferde güncellenir.

### 2. Admin auth cookie

Eski: `n`​`qai_admin_access` / `n`​`qai_admin_refresh` → Yeni: **`nv_admin_access` / `nv_admin_refresh`**

- `ACCESS_COOKIE` / `REFRESH_COOKIE` sabitleri (`src/server/admin/router.py`)
- FastAPI Cookie() type annotation parametre adları (URL'de görünmez ama header'da geçer)
- CORS expose-headers ile ilgili comment'ler

### 3. Notebook 03 örnek anahtarları

Eski format dash-separated (`n`​`qai-admin-*`, `n`​`qai-dev-*`) zaten production validator regex'iyle uyuşmuyordu — bunlar `NEUROVOICE_API_KEYS` literal-allowlist üzerinden çalışan fixture key'ler. Yeni format'la hizala:

Eski: `n`​`qai-admin-` / `n`​`qai-dev-` → Yeni: **`nv-admin-` / `nv-dev-`**

(Dash-separated literal allowlist tokenları kalır; canonical underscore-format ile karıştırılmaz.)

## Sebep

- **Brand tutarlılığı:** `X-NV-*` header + `NEUROVOICE_*` env + `nv_<env>_*` API key + `nv_admin_*` cookie — tüm auth surface aynı brand prefix'ini paylaşıyor.
- **Vendor precedent:** Stripe `sk_live_*`, OpenAI `sk-*`, Anthropic `sk-ant-*` — kısa brand prefix endüstri normu. `nv_` bu ailede.
- **Geri-uyum yok:** v0.x policy. Mevcut keys revoke; yeni keys generate edilir. Admin SPA login flow rebuild gerekli (cookie name değişti = mevcut sessions invalid).

## Etkilenen dosyalar (~5)

| Dosya | Değişiklik |
| --- | --- |
| `src/server/security/api_keys.py` | regex (KEY_PREFIX_REGEX, KEY_FULL_REGEX), generator, parse_api_key, ParsedApiKey docstring, module docstring |
| `src/server/admin/router.py` | ACCESS_COOKIE, REFRESH_COOKIE, FastAPI Cookie() parametre adları (`n`​`qai_admin_access` × 4 occurrence) |
| `src/server/main.py` | CORS comment referansı |
| `scripts/latency_bench.py`, `scripts/load_bench.py` | docstring örneği `n`​`qai_dev_...` → `nv_dev_...` |
| `notebooks/03-platform-server-colab.ipynb` | example key prefix `n`​`qai-admin-` / `n`​`qai-dev-` → `nv-admin-` / `nv-dev-` |

## Doğrulama

1. `ruff check` yeşil.
2. `from server.security.api_keys import generate_api_key; full, prefix, h = generate_api_key("dev"); assert full.startswith("nv_dev_")`.
3. `parse_api_key(full).prefix.startswith("nv_dev_")`.
4. `from server.admin.router import ACCESS_COOKIE, REFRESH_COOKIE; assert ACCESS_COOKIE == "nv_admin_access"`.
5. Residue: `grep -rn 'n''qai_(prod|staging|dev|admin)' src/ scripts/` → boş (docs/decisions/ hariç).

## Operator etkisi

- **Production:** mevcut API key'ler artık parse edilmez. Tüm keys revoke + yeni keys generate. Müşterilere proaktif duyuru gerek (v0.x'te yoksa N/A).
- **Admin SPA:** Mevcut admin oturumları geçersiz (cookie name değişti). Tüm operator'lar yeniden login olmalı.

## İlgili

- ADR-1: header + env prefix (`X-NV-*` / `NEUROVOICE_*`)
- ADR-2: eval system slug
- ADR-3: model_id slug
