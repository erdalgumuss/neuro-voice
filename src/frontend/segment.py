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
    raw = _SENTENCE_END_RE.split(text.strip())
    raw = [r.strip() for r in raw if r.strip()]
    merged = _merge_short(raw)
    expanded: list[str] = []
    for seg in merged:
        expanded.extend(_split_long(seg))
    return expanded
