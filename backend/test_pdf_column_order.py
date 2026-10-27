"""Self-check for column-aware PDF reading order (services/pdf_parser.py).

Run: python3 test_pdf_column_order.py
Uses synthetic word dicts (same shape as pdfplumber's extract_words()) so it
needs no PDF fixture and no dependencies beyond the parser module itself.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.pdf_parser import extract_reading_order_text


class FakePage:
    def __init__(self, width, height=400):
        self.width = width
        self.height = height
        self.chars = []
        self.lines = []
        self.curves = []
        self.rects = []

    def extract_text(self):
        return ""


def word(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 10}


def make_two_column_words():
    """10 rows: left column words consistently spanning x0=90-270, right
    column consistently spanning x0=310-490 (as justified column text does),
    same baselines - the exact shape that breaks naive top-to-bottom
    extraction, since a real column's text reliably fills its column width
    rather than trailing off at a variable point like ragged-right text."""
    left = [f"leftword{i}" for i in range(10)]
    right = [f"rightword{i}" for i in range(10)]
    words = []
    for i in range(10):
        top = 100 + i * 12
        words.append(word(left[i], 90, 270, top))
        words.append(word(right[i], 310, 490, top))
    return words, left, right


def test_two_column_reorders_correctly():
    words, left, right = make_two_column_words()
    page = FakePage(width=600)
    text = extract_reading_order_text(page, words)
    left_pos = [text.index(w) for w in left]
    right_pos = [text.index(w) for w in right]
    assert left_pos == sorted(left_pos), "left column not in top-to-bottom order"
    assert right_pos == sorted(right_pos), "right column not in top-to-bottom order"
    assert max(left_pos) < min(right_pos), "columns interleaved instead of left-then-right"


def test_single_column_untouched():
    words = [word(f"word{i}", 90, 90 + 40, 100 + i * 12) for i in range(8)]
    page = FakePage(width=600)
    text = extract_reading_order_text(page, words)
    positions = [text.index(f"word{i}") for i in range(8)]
    assert positions == sorted(positions), "single-column order changed"


def test_short_centered_line_not_split():
    """A short heading/title-like line with one incidental gap must not be
    torn in half just because a real gutter exists elsewhere on the page."""
    words, _, _ = make_two_column_words()
    # A two-word title line, narrower than the real columns, sitting above them.
    words.append(word("Title", 200, 240, top=50))
    words.append(word("Line", 340, 380, top=50))
    page = FakePage(width=600)
    text = extract_reading_order_text(page, words)
    lines = text.split("\n")
    assert any("Title" in ln and "Line" in ln for ln in lines), (
        "short centered line was incorrectly split across the gutter"
    )


def test_no_gutter_no_crash():
    words = [word(f"w{i}", 50 + i * 5, 50 + i * 5 + 4, 100) for i in range(3)]
    page = FakePage(width=600)
    extract_reading_order_text(page, words)  # must not raise


def test_single_column_mode_skips_detection():
    """layout_mode=single_column must never reorder even with a clear gutter."""
    words, left, right = make_two_column_words()
    page = FakePage(width=600)
    text = extract_reading_order_text(page, words, layout_mode="single_column")
    # Interleaved order: left0, right0, left1, right1, ...
    assert text.index(left[0]) < text.index(right[0]) < text.index(left[1])


def test_research_paper_mode_accepts_fewer_rows():
    """research_paper mode uses lower min_rows so short 2-col pages still fix."""
    left = [f"L{i}" for i in range(6)]
    right = [f"R{i}" for i in range(6)]
    words = []
    for i in range(6):
        top = 100 + i * 12
        words.append(word(left[i], 90, 270, top))
        words.append(word(right[i], 310, 490, top))
    page = FakePage(width=600)
    text = extract_reading_order_text(page, words, layout_mode="research_paper")
    left_pos = [text.index(w) for w in left]
    right_pos = [text.index(w) for w in right]
    assert max(left_pos) < min(right_pos), "research_paper mode failed to reorder"


def test_histogram_fallback_offset_columns():
    """Columns that don't share baselines still get fixed via x-histogram.

    Real paper columns fill nearly to the gutter edge; word centers sit
    close enough that the histogram side-band check sees them.
    """
    words = []
    # No shared baselines: left and right columns are vertically offset.
    # Word widths ~40pt (like real tokens); gutter empty band ~290-310.
    for i in range(15):
        top = 80 + i * 14
        words.append(word(f"la{i}", 55, 95, top))
        words.append(word(f"lb{i}", 100, 150, top))
        words.append(word(f"lc{i}", 155, 210, top))
        words.append(word(f"ld{i}", 215, 255, top))
        words.append(word(f"le{i}", 260, 290, top))  # abuts gutter
    for i in range(15):
        top = 87 + i * 14
        words.append(word(f"ra{i}", 310, 340, top))  # abuts gutter
        words.append(word(f"rb{i}", 345, 390, top))
        words.append(word(f"rc{i}", 395, 450, top))
        words.append(word(f"rd{i}", 455, 500, top))
        words.append(word(f"re{i}", 505, 545, top))
    page = FakePage(width=600)
    page.height = 400
    text = extract_reading_order_text(page, words, layout_mode="auto")
    assert text.index("le14") < text.index("ra0"), (
        "histogram fallback failed on vertically offset columns"
    )


def test_empty_words():
    page = FakePage(width=600)
    extract_reading_order_text(page, [])  # must not raise


def test_research_paper_section_detection():
    from app.services.summarizer import SummarizerService

    pages = [
        "Whisper: Robust Speech Recognition\n\nAbstract\n\nWe present Whisper...\n",
        "1 Introduction\n\nSpeech recognition has...\n",
        "2 Related Work\n\nPrior work on ASR...\n",
        "2.1 Multilingual Models\n\nSeveral models...\n",
        "3 Approach\n\nOur model uses...\n",
        "4 Experiments\n\nWe evaluate on...\n",
        "5 Conclusion\n\nWe showed that...\n",
        "References\n\n[1] Someone et al.\n",
    ]
    svc = SummarizerService()
    sections = svc.detect_research_paper_sections(pages)
    titles = [s["title"] for s in sections]
    assert "Abstract" in titles, titles
    assert any("Introduction" in t for t in titles), titles
    assert "References" in titles, titles
    assert len(sections) >= 5, titles
    sub = next((s for s in sections if "2.1" in s["title"] or "Multilingual" in s["title"]), None)
    if sub:
        assert sub["level"] == 1


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f" FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(failed)
