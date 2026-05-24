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
