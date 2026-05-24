"""Turkish cardinal number → words. Covers 0 ≤ n < 10^12 (a trillion)."""

from __future__ import annotations

_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_SCALES = [
    (10**12, "trilyon"),
    (10**9, "milyar"),
    (10**6, "milyon"),
    (10**3, "bin"),
]


def _below_thousand(n: int) -> str:
    if n == 0:
        return ""
    hundreds, rem = divmod(n, 100)
    parts: list[str] = []
    if hundreds:
        # In Turkish "yüz" alone means 100; "bir yüz" sounds wrong.
        if hundreds == 1:
            parts.append("yüz")
        else:
            parts.append(f"{_ONES[hundreds]} yüz")
    tens, ones = divmod(rem, 10)
    if tens:
        parts.append(_TENS[tens])
    if ones:
        parts.append(_ONES[ones])
    return " ".join(parts)


def number_to_turkish(n: int) -> str:
    """Convert an integer to its Turkish words form.

    >>> number_to_turkish(0)
    'sıfır'
    >>> number_to_turkish(1234567)
    'bir milyon iki yüz otuz dört bin beş yüz altmış yedi'
    """
    if not isinstance(n, int):
        raise TypeError(f"number_to_turkish expects int, got {type(n).__name__}")
    if n == 0:
        return "sıfır"
    if n < 0:
        return "eksi " + number_to_turkish(-n)
    if n >= 10**15:
        raise ValueError(f"number {n} exceeds supported range (< 10^15)")

    parts: list[str] = []
    remainder = n
    for scale_value, scale_name in _SCALES:
        if remainder >= scale_value:
            count, remainder = divmod(remainder, scale_value)
            # Turkish: "bin" alone = 1000, "bir bin" sounds wrong.
            if scale_name == "bin" and count == 1:
                parts.append("bin")
            else:
                parts.append(f"{_below_thousand(count)} {scale_name}".strip())
    if remainder > 0:
        parts.append(_below_thousand(remainder))
    return " ".join(p for p in parts if p)


def decimal_to_turkish(s: str) -> str:
    """Convert a decimal string like '3.14' or '3,14' → 'üç virgül bir dört'."""
    s = s.replace(",", ".").strip()
    if "." not in s:
        return number_to_turkish(int(s))
    int_part, frac_part = s.split(".", 1)
    sign = ""
    if int_part.startswith("-"):
        sign = "eksi "
        int_part = int_part[1:]
    int_words = number_to_turkish(int(int_part or "0"))
    frac_words = " ".join(_ONES[int(d)] if d != "0" else "sıfır" for d in frac_part)
    return f"{sign}{int_words} virgül {frac_words}".strip()
