"""Turkish language pack.

Wraps the three lane modules (normalize / segment / numbers) behind the
LanguagePack interface so the top-level :mod:`frontend` dispatcher can
call them generically. The underlying logic is unchanged from the v0
single-language pipeline; this package is purely the language-pluggable
shape introduced in ADR-6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import normalize as _normalize
from . import numbers as _numbers
from . import segment as _segment


@dataclass(frozen=True)
class TurkishPack:
    code: str = "tr"

    def normalize_text(self, text: str, **kwargs: Any) -> str:
        # Forward keyword-only kwargs (e.g. ``pronunciation_dict``) to the
        # underlying implementation, which validates them.
        return _normalize.normalize_text(text, **kwargs)

    def segment_sentences(self, text: str) -> list[str]:
        return _segment.segment_sentences(text)

    def number_to_words(self, n: int) -> str:
        return _numbers.number_to_turkish(n)


pack = TurkishPack()
