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


# --------------------------------------------------------------------------- #
# B.2 — protect Turkish abbreviation periods (Dr., M.Ö., T.C., ...) from
# the sentence splitter. Real-world TR text contains these heavily;
# false-positive splits put a pause mid-name on a child-directed product.
# --------------------------------------------------------------------------- #
def test_segment_dr_title_not_split_mid_name() -> None:
    """`Dr. Ayşe Yılmaz geldi. Ben de gittim.` → exactly 2 segments,
    first contains the full `Dr. Ayşe Yılmaz geldi.`"""
    text = "Dr. Ayşe Yılmaz geldi. Ben de gittim."
    segs = segment_sentences(text)
    assert len(segs) == 2
    assert "Dr. Ayşe Yılmaz" in segs[0]
    assert segs[1].startswith("Ben")


def test_segment_mo_historical_era_not_split() -> None:
    """`M.Ö. 500'de yaşadı. Sümerler bunu yazdı.` → 2 segments."""
    text = "M.Ö. 500'de yaşadı. Sümerler bunu yazdı."
    segs = segment_sentences(text)
    assert len(segs) == 2
    assert "M.Ö." in segs[0] or "M.Ö" in segs[0]
    assert segs[1].startswith("Sümerler")


def test_segment_tc_state_abbreviation_not_split() -> None:
    """`T.C. Sağlık Bakanlığı duyurdu. Yeni karar var.` → 2 segments."""
    text = "T.C. Sağlık Bakanlığı duyurdu. Yeni karar var."
    segs = segment_sentences(text)
    assert len(segs) == 2
    assert "T.C." in segs[0]
    assert segs[1].startswith("Yeni")


def test_segment_time_apostrophe_suffix_not_split() -> None:
    """`Saat 14:00'da buluşalım. Yarın da yine görüşelim mi?` → 2
    segments. The colon in 14:00 already doesn't trigger the splitter;
    this test pins the contract so a future regex tweak can't regress
    it. Second clause needs to be ≥ MIN_CHARS_PER_SEGMENT so the
    short-segment merger doesn't fold it back into segment 1."""
    text = "Saat 14:00'da buluşalım. Yarın da yine görüşelim mi?"
    segs = segment_sentences(text)
    assert len(segs) == 2
    assert "14:00" in segs[0]
    assert segs[1].startswith("Yarın")


def test_segment_prof_doc_titles_not_split() -> None:
    """Sn., Prof., Doç. all protected."""
    text = "Prof. Dr. Mehmet konuştu. Sn. Başkan dinledi. Doç. Ayşe yorum yaptı."
    segs = segment_sentences(text)
    assert len(segs) == 3
    assert "Prof." in segs[0]
    assert "Sn." in segs[1]
    assert "Doç." in segs[2]


def test_segment_no_cad_address_abbrev_not_split() -> None:
    """`Cad. No. 12, Mah. Çamlık` shouldn't be cut into 4 sentences."""
    text = "Çamlık Mah. Atatürk Cad. No. 12'de buluşalım. Yarın görüşürüz."
    segs = segment_sentences(text)
    assert len(segs) == 2
    assert "No. 12" in segs[0]
    assert segs[1].startswith("Yarın")
