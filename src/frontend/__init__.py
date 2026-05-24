"""Turkish text frontend v0 — sentence segmentation + lightweight normalization.

Scope of v0:
    * Unicode NFKC + whitespace squeeze
    * Sentence segmentation respecting Turkish apostrophe + abbreviations
    * Number-to-Turkish words (Tr. cardinals + ordinals, up to 10^12)
    * Common abbreviation expansion (Dr., Av., Prof., vb., Bn., TBMM, AB)
    * Symbol expansion (%, ₺, €, $, +, =, -, /, ×)
    * Code-mix lexicon (iPhone, Bluetooth, vb.) — extensible JSON

Out of scope of v0 (will be filled by Faz-1 hafta 1-2 work):
    * Phoneme-level G2P (modern multilingual models handle Turkish orthography)
    * Morphological analysis (Zemberek)
    * Style tag injection
"""

from .normalize import normalize_text
from .numbers import number_to_turkish
from .segment import segment_sentences

__all__ = ["normalize_text", "segment_sentences", "number_to_turkish"]
