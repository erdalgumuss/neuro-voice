"""LanguagePack interface for the text frontend.

Each language under ``src/frontend/lang_packs/<iso>/`` ships a module that
exports a ``pack: LanguagePack`` singleton implementing this Protocol.
The top-level :mod:`frontend` module dispatches to the right pack by
ISO 639-1 code at call time.

Adding a new language is a directory-add, not a refactor:

1. ``mkdir src/frontend/lang_packs/<iso>``
2. Implement ``normalize_text`` / ``segment_sentences`` / ``number_to_words``
3. Export ``pack`` in the package's ``__init__.py``
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LanguagePack(Protocol):
    """The contract every language pack must satisfy."""

    code: str
    """ISO 639-1 lowercase language code (``"tr"``, ``"pl"``, ...)."""

    def normalize_text(self, text: str, **kwargs: Any) -> str:
        """Verbalize + clean raw input for the synthesis engine.

        Language-specific extras (per-request pronunciation overrides,
        lexicon nudges, ...) flow in via ``**kwargs`` so the public
        :func:`frontend.normalize_text` signature stays stable as new
        language packs add per-pack switches.
        """
        ...

    def segment_sentences(self, text: str) -> list[str]:
        """Split normalized text into engine-friendly sentence chunks."""
        ...

    def number_to_words(self, n: int) -> str:
        """Render an integer as the language's spoken-form word string."""
        ...
