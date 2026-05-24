"""Sentence segmentation invariants."""

from __future__ import annotations

from frontend.segment import MAX_CHARS_PER_SEGMENT, MIN_CHARS_PER_SEGMENT, segment_sentences


def test_basic_split() -> None:
    text = (
        "Bir varmış, bir yokmuş. Çok uzak bir ülkede küçük bir tavşan yaşarmış. "
        "Adı Pamuk'tu, kulakları uzundu."
    )
    segs = segment_sentences(text)
    assert len(segs) >= 2
    for s in segs:
        assert s.strip() == s


def test_apostrophe_kept() -> None:
    text = "Ankara'da yaşıyorum. Neeko'nun hikayesi başlıyor."
    segs = segment_sentences(text)
    joined = " ".join(segs)
    assert "Ankara'da" in joined
    assert "Neeko'nun" in joined


def test_empty_input() -> None:
    assert segment_sentences("") == []
    assert segment_sentences("   ") == []


def test_min_chars_respected_for_non_trivial_input() -> None:
    text = "Bir cümle. Çok daha uzun bir ikinci cümle burada bulunuyor."
    segs = segment_sentences(text)
    # short first segment ("Bir cümle.") should be merged with the next one
    assert all(len(s) >= MIN_CHARS_PER_SEGMENT for s in segs)


def test_max_chars_splits_long_runon() -> None:
    long_sentence = (
        "Bu çok uzun bir cümle, içinde birçok virgül var, "
        + "ve sürekli devam ediyor, durmuyor, sonra başka bir konuya atlıyor, "
        + "ve yine devam ediyor, gerçekten uzun bir cümle, biraz daha sürüyor, "
        + "neredeyse bitiyor şimdi, evet işte bitti, ama sonra yine başlıyor, "
        + "ve devam ediyor, hala bitmedi, neredeyse şimdi bitiyor cidden bu sefer."
    )
    assert len(long_sentence) > MAX_CHARS_PER_SEGMENT
    segs = segment_sentences(long_sentence)
    assert len(segs) >= 2
    for s in segs:
        assert len(s) <= MAX_CHARS_PER_SEGMENT + 50  # soft cap, see _split_long
