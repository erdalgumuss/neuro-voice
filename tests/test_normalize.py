"""Smoke tests for the Turkish text normalization pipeline."""

from __future__ import annotations

import pytest

from frontend.normalize import normalize_text


@pytest.mark.parametrize("raw,expected_substr", [
    ("Yedi artı üç kaç eder?", "yedi artı üç"),
    ("23 elma var", "yirmi üç elma var"),
    ("Saat 14:30'da geleceğim.", "saat on dört otuz"),
    ("Dr. Ayşe geldi.", "doktor ayşe geldi"),
    ("TBMM açıldı.", "türkiye büyük millet meclisi açıldı"),
    ("Bluetooth'u açmayı unutma.", "blutut"),
    ("Annenin iPhone'unu yerine bırak.", "aypın"),
    ("%50 indirim var.", "yüzde elli indirim"),
    ("Fiyat 1.234.567 TL", "bir milyon iki yüz otuz dört bin beş yüz altmış yedi"),
])
def test_normalize_contains(raw: str, expected_substr: str) -> None:
    normalized = normalize_text(raw).lower()
    assert expected_substr.lower() in normalized, f"got: {normalized}"


def test_idempotent_on_normalized_input() -> None:
    once = normalize_text("Bir varmış, bir yokmuş.")
    twice = normalize_text(once)
    assert once == twice


def test_empty_input() -> None:
    assert normalize_text("") == ""
    assert normalize_text("   ") == ""


# --------------------------------------------------------------------------- #
# Faz B.5 Dalga 2.6 — per-request pronunciation overrides
# --------------------------------------------------------------------------- #
def test_pronunciation_dict_overrides_global_lexicon() -> None:
    """User override fires BEFORE the built-in code-mix lexicon —
    tenant can shadow the global pronunciation for one request."""
    raw = "iPhone yeni geldi"
    default = normalize_text(raw).lower()
    assert "aypın" in default

    overridden = normalize_text(
        raw, pronunciation_dict={"iPhone": "ai-fon"}
    ).lower()
    assert "ai-fon" in overridden
    assert "aypın" not in overridden


def test_pronunciation_dict_case_insensitive() -> None:
    """Whole-word case-insensitive — `\\b` boundaries handle punctuation."""
    out = normalize_text(
        "NQAI projesi başladı, nqai harika!",
        pronunciation_dict={"NQAI": "en-ku-a-ay"},
    )
    # Both occurrences (NQAI + nqai) replaced.
    assert out.count("en-ku-a-ay") == 2


def test_pronunciation_dict_none_is_noop() -> None:
    """No dict → behaviour identical to the legacy single-arg call."""
    raw = "Bluetooth'u açmayı unutma."
    assert normalize_text(raw) == normalize_text(raw, pronunciation_dict=None)
    assert normalize_text(raw) == normalize_text(raw, pronunciation_dict={})


# --------------------------------------------------------------------------- #
# A.10 — strip invisible Unicode marks (ZWJ / ZWNJ / RTL / BOM) BEFORE
# NFKC so messaging-app paste pollution does not cause unreproducible
# TTS glitches.
# --------------------------------------------------------------------------- #
def test_zwj_inside_brand_name_is_stripped() -> None:
    """`Goo<ZWJ>gle'a` → the ZWJ is dropped so the code-mix lexicon's
    `Google` entry matches and produces the same phonetic output as
    a clean paste."""
    raw_zwj = "Goo‍gle'a"
    raw_clean = "Google'a"
    assert normalize_text(raw_zwj) == normalize_text(raw_clean)


def test_zwnj_is_stripped() -> None:
    raw = "selam‌kardes"
    assert "‌" not in normalize_text(raw)


def test_bidi_wrap_is_stripped() -> None:
    """Left-to-right embedding + pop-directional-formatting around a
    word must not survive into the output."""
    raw = "‪selam‬"
    assert normalize_text(raw) == "selam"


def test_zero_width_no_break_space_is_stripped() -> None:
    """ZWNBSP (also used as BOM) is dropped."""
    raw = "﻿merhaba"
    assert normalize_text(raw) == "merhaba"
