"""Multilingual text frontend — language pack dispatch.

Public API:

    normalize_text(text, lang="tr", **kwargs) -> str
    segment_sentences(text, lang="tr") -> list[str]
    number_to_words(n, lang="tr") -> str
    get_pack(lang="tr") -> LanguagePack

Today only the Turkish pack ships. Adding another language is a
directory-add under ``src/frontend/lang_packs/<iso>/`` that exports
``pack: LanguagePack`` — no central registration. See
``docs/decisions/2026-05-28-multilingual-frontend.md`` for the shape
rationale.
"""

from __future__ import annotations

import importlib
from typing import Any

from .protocol import LanguagePack

_DEFAULT_LANG = "tr"


def get_pack(lang: str = _DEFAULT_LANG) -> LanguagePack:
    """Resolve the LanguagePack for ``lang``. Raises on unknown codes."""
    try:
        module = importlib.import_module(f"frontend.lang_packs.{lang}")
    except ModuleNotFoundError as e:
        raise ValueError(
            f"unknown language pack {lang!r}; "
            f"create src/frontend/lang_packs/{lang}/ exporting `pack` to add"
        ) from e
    pack = getattr(module, "pack", None)
    if pack is None:
        raise ValueError(
            f"frontend.lang_packs.{lang} does not export `pack: LanguagePack`"
        )
    return pack


def normalize_text(text: str, lang: str = _DEFAULT_LANG, **kwargs: Any) -> str:
    return get_pack(lang).normalize_text(text, **kwargs)


def segment_sentences(text: str, lang: str = _DEFAULT_LANG) -> list[str]:
    return get_pack(lang).segment_sentences(text)


def number_to_words(n: int, lang: str = _DEFAULT_LANG) -> str:
    return get_pack(lang).number_to_words(n)


__all__ = [
    "LanguagePack",
    "get_pack",
    "normalize_text",
    "number_to_words",
    "segment_sentences",
]
