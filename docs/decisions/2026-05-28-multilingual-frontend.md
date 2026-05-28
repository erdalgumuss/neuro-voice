# ADR-6 — Multilingual text frontend (pluggable language pack)

- **Tarih:** 2026-05-28
- **Durum:** Kabul edildi
- **Bağlam:** `src/frontend/` v0'da Türkçe'ye bağlı (`normalize.py`, `numbers.py`, `segment.py` — tümü TR-spesifik lexicon/normalize/abbreviations içeriyor). Repo'nun yeni kimliği (NeuroVoice multilingual TTS API) ikinci dilin (Lehçe / Vietnamca / Farsça / Endonezce) eklenebilirliğini mimaride hazır tutmayı gerektiriyor. **Ancak v0.x boyunca sadece Türkçe lang_pack çalışıyor** — bu ADR davranışsal değişiklik değil, **şekil değişikliğidir**.

## Karar

`src/frontend/` "language-pluggable" mimariye geçer:

```
src/frontend/
├── __init__.py              # public API: normalize_text(text, lang="tr"), ...
├── protocol.py              # LanguagePack Protocol
└── lang_packs/
    ├── __init__.py          # namespace marker
    └── tr/                  # ← mevcut normalize/numbers/segment buraya
        ├── __init__.py      # pack: TurkishPack singleton
        ├── normalize.py
        ├── numbers.py
        └── segment.py
```

**Yeni dosyalar:**
- `src/frontend/protocol.py` — `LanguagePack` Protocol (`@runtime_checkable`); 3 method: `normalize_text(text, **kwargs)`, `segment_sentences(text)`, `number_to_words(n)`.
- `src/frontend/lang_packs/__init__.py` — namespace marker.
- `src/frontend/lang_packs/tr/__init__.py` — `TurkishPack` (`@dataclass(frozen=True)`) + `pack = TurkishPack()` singleton; mevcut modülleri (`._normalize`, `._numbers`, `._segment`) protokole bağlar.

**Rewrite edilen:**
- `src/frontend/__init__.py` — `get_pack(lang)` dispatcher (lazy `importlib.import_module`) + module-level wrapper'lar (`normalize_text(text, lang="tr", **kwargs)`, vs.).

**Taşınan:**
- `git mv src/frontend/{normalize,numbers,segment}.py → src/frontend/lang_packs/tr/` (3 dosya; içerik **bit-perfect** korundu, sadece klasör derinliği bir arttı).

**Silinen:**
- `src/g2p/` — boş klasör (içeride hiç `.py` yoktu). Gelecek G2P işi ya bir lang_pack altında ya da gerçek ihtiyaç doğduğunda ayrı bir modül.

## Public API kontratı

```python
from frontend import normalize_text, segment_sentences, number_to_words, get_pack
```

- `normalize_text(text: str, lang: str = "tr", **kwargs) -> str` — kwargs lang-spesifik (TR'de `pronunciation_dict` desteği korunur).
- `segment_sentences(text: str, lang: str = "tr") -> list[str]`.
- `number_to_words(n: int, lang: str = "tr") -> str` — eski `number_to_turkish` adı internal; public API brand-free.
- `get_pack(lang: str = "tr") -> LanguagePack` — pack object'i direkt almak için (test stub veya alternatif çağrılar).

Bilinmeyen `lang` → `ValueError` (404 değil 400 semantiği — yeni lang_pack klasörü oluşturma yönergesi error mesajında).

## Sebep

- **TR-first execution, multi-lang shape:** Konumlanma değişmedi (TR-only sahip), mimari hazır.
- **Zero-functional-change risk profili:** Mevcut TR davranışı bit-perfect aynı; sadece import path'i bir derinlik kazandı. Public API isim/parametre uyumlu.
- **Convention-over-config:** Yeni dil eklemek = klasör eklemek (`lang_packs/<iso>/` + `pack` export). Central registry yok, "Add language X" PR'ı tek-dosya.
- **Premature shared-module yok:** İkinci pack gelene kadar `common.py` veya base sınıf yazmayız; ortak NFKC/whitespace ne kadar gerçek tekrarlanırsa o zaman çıkarılır.

## Etkilenen dosyalar

| Dosya | Değişiklik |
| --- | --- |
| `src/frontend/{normalize,numbers,segment}.py` | `git mv → src/frontend/lang_packs/tr/` (içerik aynı) |
| `src/frontend/__init__.py` | rewrite: dispatch + module-level wrappers |
| `src/frontend/protocol.py` | **yeni** — LanguagePack Protocol |
| `src/frontend/lang_packs/__init__.py` | **yeni** — namespace marker |
| `src/frontend/lang_packs/tr/__init__.py` | **yeni** — pack singleton |
| `src/g2p/` | **silindi** — boş klasör |
| `src/worker/engine.py:34` | import yolu DEĞIŞMEDİ (`from frontend import normalize_text, segment_sentences` aynı çalışıyor) |

## Risk

- **Düşük.** Tek dış import sitesi `worker/engine.py:34`; public API isim + parametre kontratı korunur.
- Davranış değişmediği için test gerek **yok** (zaten tests/ parkta — Codex sonra yazar).
- Doğrulama: ruff + module import smoke + bir TR örnek girdisinin normalize/segment çıktısının değişmediğinin elle kontrolü.

## Alternatifler (reddedildi)

| Seçenek | Niye reddedildi |
| --- | --- |
| Klasör adı `langs/` | `lang_packs/` daha okunabilir; "pack" = "exportable unit" semantiği taşır |
| Entry-point ile lang register | Convention-based discovery daha basit; setuptools entry_point yapısı bu ölçek için overhead |
| `LanguagePack` ABC (Protocol değil) | Protocol nominal type checking olmadan duck-typing'e izin verir; basit testlerde stub oluşturmak kolay |
| Common base module şimdi çıkar | Premature factoring; ikinci pack gelene kadar speculative |
| `src/g2p/`'i tut | Boş klasör tutmanın gerekçesi yok; ihtiyaç doğunca real bir modül açılır |

## İlgili

- [[project-framing]] — multilingual base + TR-first execution
- [[brand-naming]] — NeuroVoice
- CLAUDE.md nirengi noktası 8 — multi-language base; LoRA dil-özgül; text frontend pluggable language pack
