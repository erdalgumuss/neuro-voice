"""Turkish-aware sentence segmentation.

Naive split on [.!?] would shatter abbreviations and apostrophe-suffixed words
("Ankara'da"). v0 strategy: split on sentence punctuation followed by
whitespace + uppercase-or-digit start, and merge chunks that are too short
(< MIN_CHARS) into the next chunk to avoid generating 1-word segments.
"""

from __future__ import annotations

import re

_SENTENCE_END_RE = re.compile(
    r"(?<=[.!?…])\s+(?=[A-ZÇĞİÖŞÜ0-9\"'\(])",
    re.UNICODE,
)
MIN_CHARS_PER_SEGMENT = 12
MAX_CHARS_PER_SEGMENT = 240


# Research finding B.2 (2026-05-25) — protect periods inside known
# Turkish title / historical-era / state abbreviations so the splitter
# does not break mid-name ("Dr. Ayşe" was splitting into "Dr." +
# "Ayşe..." because the splitter saw period+whitespace+uppercase and
# could not tell the dot was part of the title). The placeholder is a
# control character (U+0001) so it cannot appear in any normalised
# input and round-trips back to a real "." after splitting.
_ABBREVIATION_PERIOD_PLACEHOLDER = ""

# Patterns matched as whole-word followed by a period. Each pattern is
# compiled with `re.UNICODE` so Turkish letters in `M.Ö.` / `T.C.` are
# treated correctly. Order doesn't matter; the substitution is
# non-overlapping per-pattern.
_PROTECTED_ABBREVS: tuple[str, ...] = (
    # Honourifics / titles
    "Dr", "Doç", "Prof", "Av", "Sn", "Hz", "Sn",
    # Address-shorthand
    "No", "Cad", "Sok", "Mah", "Apt", "Bld", "Blv",
)

# Multi-period abbreviations (M.Ö., M.S., T.C., K.K.T.C.) — each
# internal period gets replaced. Longest first so K.K.T.C. wins over
# T.C. inside a substring match.
_PROTECTED_MULTI_PERIOD: tuple[str, ...] = (
    "K.K.T.C", "M.Ö", "M.S", "T.C",
)

_ABBREV_RE = re.compile(
    r"\b(?:" + "|".join(_PROTECTED_ABBREVS) + r")\.",
    re.UNICODE,
)


def _protect_abbreviations(text: str) -> str:
    """Swap `.` inside known abbreviations for the placeholder so the
    sentence regex does not consider them sentence terminators."""
    # Multi-period abbreviations first — each `.` in the matched span
    # becomes the placeholder.
    for abbrev in _PROTECTED_MULTI_PERIOD:
        # Match the abbreviation followed by an optional trailing dot
        # (`M.Ö.` vs `M.Ö 500` — defensive against missing trailing).
        pattern = re.compile(
            r"\b" + re.escape(abbrev) + r"\.?",
            re.UNICODE,
        )
        text = pattern.sub(
            lambda m: m.group(0).replace(".", _ABBREVIATION_PERIOD_PLACEHOLDER),
            text,
        )
    # Single-period abbreviations.
    text = _ABBREV_RE.sub(
        lambda m: m.group(0).replace(".", _ABBREVIATION_PERIOD_PLACEHOLDER),
        text,
    )
    return text


def _restore_abbreviations(segment: str) -> str:
    return segment.replace(_ABBREVIATION_PERIOD_PLACEHOLDER, ".")


def _merge_short(segments: list[str]) -> list[str]:
    merged: list[str] = []
    buf = ""
    for seg in segments:
        candidate = (buf + " " + seg).strip() if buf else seg
        if len(candidate) < MIN_CHARS_PER_SEGMENT:
            buf = candidate
            continue
        merged.append(candidate)
        buf = ""
    if buf:
        if merged:
            merged[-1] = (merged[-1] + " " + buf).strip()
        else:
            merged.append(buf)
    return merged


def _split_long(segment: str) -> list[str]:
    if len(segment) <= MAX_CHARS_PER_SEGMENT:
        return [segment]
    parts = re.split(r"(?<=[,;:])\s+", segment)
    if len(parts) == 1:
        # last resort — hard split on whitespace boundary near the midpoint
        mid = len(segment) // 2
        ws = segment.rfind(" ", 0, mid + 40)
        if ws < MIN_CHARS_PER_SEGMENT:
            return [segment]
        return [segment[:ws].strip(), segment[ws:].strip()]
    out: list[str] = []
    buf = ""
    for part in parts:
        candidate = (buf + " " + part).strip() if buf else part
        if len(candidate) > MAX_CHARS_PER_SEGMENT and buf:
            out.append(buf)
            buf = part
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out


def segment_sentences(text: str) -> list[str]:
    """Split normalized Turkish text into TTS-friendly chunks.

    Output invariants: each chunk is non-empty, trimmed, between
    MIN_CHARS_PER_SEGMENT and roughly MAX_CHARS_PER_SEGMENT characters
    (the upper bound is best-effort — extremely long unpunctuated input
    is split on whitespace).
    """
    if not text or not text.strip():
        return []
    # B.2 — protect abbreviation periods so `Dr. Ayşe` / `M.Ö. 500` /
    # `T.C. Sağlık Bakanlığı` stay as one chunk through the splitter.
    protected = _protect_abbreviations(text.strip())
    raw = _SENTENCE_END_RE.split(protected)
    raw = [r.strip() for r in raw if r.strip()]
    merged = _merge_short(raw)
    expanded: list[str] = []
    for seg in merged:
        expanded.extend(_split_long(seg))
    # Restore the real `.` characters in every emitted segment.
    return [_restore_abbreviations(seg) for seg in expanded]
