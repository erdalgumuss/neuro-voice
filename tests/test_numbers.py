"""Golden-test set for the Turkish number-to-words converter."""

from __future__ import annotations

import pytest

from frontend.numbers import decimal_to_turkish, number_to_turkish


@pytest.mark.parametrize("n,expected", [
    (0, "sıfır"),
    (1, "bir"),
    (5, "beş"),
    (10, "on"),
    (11, "on bir"),
    (20, "yirmi"),
    (21, "yirmi bir"),
    (99, "doksan dokuz"),
    (100, "yüz"),
    (101, "yüz bir"),
    (200, "iki yüz"),
    (999, "dokuz yüz doksan dokuz"),
    (1000, "bin"),
    (1001, "bin bir"),
    (2000, "iki bin"),
    (10000, "on bin"),
    (1234, "bin iki yüz otuz dört"),
    (1234567, "bir milyon iki yüz otuz dört bin beş yüz altmış yedi"),
    (1_000_000, "bir milyon"),
    (1_000_000_000, "bir milyar"),
    (-5, "eksi beş"),
])
def test_number_to_turkish(n: int, expected: str) -> None:
    assert number_to_turkish(n) == expected


@pytest.mark.parametrize("s,expected", [
    ("3.14", "üç virgül bir dört"),
    ("3,14", "üç virgül bir dört"),
    ("0.5", "sıfır virgül beş"),
    ("-2.7", "eksi iki virgül yedi"),
])
def test_decimal_to_turkish(s: str, expected: str) -> None:
    assert decimal_to_turkish(s) == expected


def test_exceeds_range() -> None:
    with pytest.raises(ValueError):
        number_to_turkish(10**15)
