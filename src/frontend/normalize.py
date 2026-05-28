"""Lightweight Turkish text normalization for v0 of the platform."""

from __future__ import annotations

import re
import unicodedata

from .numbers import decimal_to_turkish, number_to_turkish

# Research finding A.10 (2026-05-25) — strip Unicode "default ignorable"
# code points that messaging apps + chat surfaces sprinkle into pasted
# text. NFKC preserves them; VoxCPM2 is tokeniser-free so an invisible
# ZWJ inside a brand name produces an unreproducible pause or glitch
# that no operator can diagnose by ear. Standard remedy across Coqui /
# Piper / espeak-ng frontends: drop these before normalisation.
_INVISIBLE_MARKS_RE = re.compile(
    # ZWSP (200B), ZWNJ (200C), ZWJ (200D), LRM (200E), RLM (200F),
    # LRE..PDF..RLO (202A-202E), WJ (2060), BOM/ZWNBSP (FEFF).
    "[​-‏‪-‮⁠﻿]"
)

# Order matters: longer keys first to avoid prefix collisions.
_ABBREVIATIONS: list[tuple[str, str]] = [
    ("TBMM", "Türkiye Büyük Millet Meclisi"),
    ("KKTC", "Kuzey Kıbrıs Türk Cumhuriyeti"),
    ("THY",  "Türk Hava Yolları"),
    ("vb.",  "ve benzeri"),
    ("vs.",  "vesaire"),
    ("Prof.", "profesör"),
    ("Doç.",  "doçent"),
    ("Dr.",   "doktor"),
    ("Av.",   "avukat"),
    ("Bn.",   "bayan"),
    ("Sn.",   "sayın"),
    ("Mah.",  "mahalle"),
    ("Sok.",  "sokak"),
    ("Cad.",  "cadde"),
    ("No.",   "numara"),
]

_SYMBOL_MAP = {
    "%": "yüzde",
    "₺": "lira",
    "€": "euro",
    "$": "dolar",
    "£": "sterlin",
    "&": "ve",
    "+": "artı",
    "=": "eşittir",
    "×": "çarpı",
    "÷": "bölü",
}

# Code-mix lexicon: foreign tokens → Turkish phonetic spelling.
_CODE_MIX_LEXICON: dict[str, str] = {
    "iPhone": "aypın",
    "iphone": "aypın",
    "Android": "androyd",
    "android": "androyd",
    "Bluetooth": "blutut",
    "bluetooth": "blutut",
    "WiFi": "vayfay",
    "wifi": "vayfay",
    "Google": "gugıl",
    "google": "gugıl",
    "Apple": "epıl",
    "apple": "epıl",
    "Microsoft": "maykrosoft",
    "YouTube": "yutub",
    "youtube": "yutub",
}

_DECIMAL_RE = re.compile(r"(?<!\w)-?\d+[.,]\d+(?!\w)")
_INTEGER_RE = re.compile(r"(?<!\w)-?\d{1,15}(?!\w)")
_THOUSAND_GROUPED_RE = re.compile(r"(?<!\w)-?\d{1,3}(?:\.\d{3})+(?:,\d+)?(?!\w)")
_TIME_RE = re.compile(r"(?<!\w)([01]?\d|2[0-3]):([0-5]\d)(?!\w)")
_WHITESPACE_RE = re.compile(r"[ \t ]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _expand_abbreviations(text: str) -> str:
    for abbr, full in _ABBREVIATIONS:
        text = re.sub(r"(?<!\w)" + re.escape(abbr), full, text)
    return text


def _expand_symbols(text: str) -> str:
    for sym, word in _SYMBOL_MAP.items():
        text = text.replace(sym, f" {word} ")
    return text


def _expand_code_mix(text: str) -> str:
    for token, pron in _CODE_MIX_LEXICON.items():
        text = re.sub(rf"\b{re.escape(token)}\b", pron, text)
    return text


def _expand_user_pronunciation(text: str, mapping: dict[str, str]) -> str:
    """per-request pronunciation override.

    Applied BEFORE the built-in code-mix lexicon so a tenant can shadow
    "iPhone" → vendor-specific spelling, override a brand the global
    lexicon hasn't learned yet, or fix a one-off mispronunciation
    surfaced from QA without a global lexicon change. Case-insensitive
    whole-word match; punctuation around the token is preserved by the
    `\\b` boundaries.
    """
    if not mapping:
        return text
    for token, pron in mapping.items():
        if not token:
            continue
        text = re.sub(
            rf"\b{re.escape(token)}\b",
            pron,
            text,
            flags=re.IGNORECASE,
        )
    return text


def _expand_time(match: re.Match[str]) -> str:
    hh, mm = match.group(1), match.group(2)
    h_words = number_to_turkish(int(hh))
    m_int = int(mm)
    if m_int == 0:
        return f"saat {h_words}"
    return f"saat {h_words} {number_to_turkish(m_int)}"


def _expand_thousand_grouped(match: re.Match[str]) -> str:
    raw = match.group(0)
    sign = ""
    if raw.startswith("-"):
        sign = "eksi "
        raw = raw[1:]
    if "," in raw:
        int_part, frac_part = raw.split(",", 1)
        int_clean = int_part.replace(".", "")
        return f"{sign}{decimal_to_turkish(f'{int_clean}.{frac_part}')}"
    return f"{sign}{number_to_turkish(int(raw.replace('.', '')))}"


def _expand_decimal(match: re.Match[str]) -> str:
    return decimal_to_turkish(match.group(0))


def _expand_integer(match: re.Match[str]) -> str:
    raw = match.group(0)
    sign = ""
    if raw.startswith("-"):
        sign = "eksi "
        raw = raw[1:]
    return f"{sign}{number_to_turkish(int(raw))}"


def normalize_text(
    text: str,
    *,
    pronunciation_dict: dict[str, str] | None = None,
) -> str:
    """Run the v0 normalization pipeline. Idempotent for normalized input.

    `pronunciation_dict` is an optional per-request
    override map applied BEFORE the built-in code-mix lexicon, so the
    caller's override always wins for a given token.
    """
    if not text:
        return ""
    # A.10 — drop default-ignorable invisibles BEFORE NFKC. Done first
    # so a `Goo<ZWJ>gle` paste collapses to `Google` and the lexicon
    # lookup downstream actually fires.
    text = _INVISIBLE_MARKS_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _expand_abbreviations(text)
    if pronunciation_dict:
        text = _expand_user_pronunciation(text, pronunciation_dict)
    text = _expand_code_mix(text)
    text = _TIME_RE.sub(_expand_time, text)
    text = _THOUSAND_GROUPED_RE.sub(_expand_thousand_grouped, text)
    text = _DECIMAL_RE.sub(_expand_decimal, text)
    text = _INTEGER_RE.sub(_expand_integer, text)
    text = _expand_symbols(text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()
