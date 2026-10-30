"""Tests for Docling layout region detection, exclusion ranges, and TTS page narration."""

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.models import Document, ExtractedText, LibraryEntry, User
from app.services.narration import (
    EXCLUDED_LABELS,
    char_to_utf16_offset,
    compute_narration_exclusions,
    compute_word_center,
    get_narratable_text,
    is_center_in_rects,
    is_narratable_label,
    utf16_len,
    utf16_to_char_offset,
)
from app.services.pdf_parser import PDFParserService


def test_is_narratable_label():
    for label in EXCLUDED_LABELS:
        assert not is_narratable_label(label), f"{label} should be excluded"
        assert not is_narratable_label(label.upper()), f"{label.upper()} should be excluded"

    for label in ["title", "section_header", "heading", "list_item", "text", "paragraph", "custom_unknown"]:
        assert is_narratable_label(label), f"{label} should be included"

    assert is_narratable_label(None) is True
    assert is_narratable_label("") is True


def test_utf16_len_ascii_and_surrogates():
    assert utf16_len("") == 0
    assert utf16_len("Hello world") == 11
    assert len("😀") == 1
    assert utf16_len("😀") == 2
    assert utf16_len("Hello 😀 world 🚀") == len("Hello  world ") + 2 + 2

    # Conversion round trip
    text = "Hello 😀 world 🚀 Cafe"
    for i in range(len(text) + 1):
        u_off = char_to_utf16_offset(text, i)
        c_idx = utf16_to_char_offset(text, u_off)
        assert c_idx == i


def test_get_narratable_text_filters_exclusions():
    content = (
        "Journal of AI 2026\n\n"
        "1. Introduction\n"
        "This is paragraph one of the introduction.\n\n"
        "Table 1: Benchmark Results\n"
        "Accuracy 98%\n\n"
        "Conclusion: The system works well."
    )
    tbl_start = content.index("Table 1")
    tbl_end = content.index("Conclusion:")
    hdr_end = len("Journal of AI 2026")

    exclusions = [
        {"start": 0, "end": hdr_end + 2},
        {"start": tbl_start, "end": tbl_end},
    ]

    spoken = get_narratable_text(content=content, narration_exclusions=exclusions)
    assert "Journal of AI" not in spoken
    assert "Table 1" not in spoken
    assert "Accuracy 98%" not in spoken
    assert "1. Introduction" in spoken
    assert "This is paragraph one" in spoken
    assert "Conclusion: The system works well." in spoken


def test_word_center_boundary_adjacent_prose():
    """Verify word-center calculation cleanly separates words on or adjacent to region boundaries."""
    # Rectangle covering [0.1, 0.1, 0.5, 0.5] (normalized)
    rect = [0.1, 0.1, 0.5, 0.5]

    # Word A: box [0.4, 0.2, 0.48, 0.3] -> center x = 0.44 (inside)
    word_a_box = [0.4, 0.2, 0.48, 0.3]
    cx_a, cy_a = compute_word_center(word_a_box)
    assert is_center_in_rects(cx_a, cy_a, [rect]) is True

    # Word B: box [0.48, 0.2, 0.54, 0.3] -> center x = 0.51 (just outside right boundary)
    word_b_box = [0.48, 0.2, 0.54, 0.3]
    cx_b, cy_b = compute_word_center(word_b_box)
    assert is_center_in_rects(cx_b, cy_b, [rect]) is False

    # Word C: box [0.48, 0.48, 0.51, 0.51] -> center = (0.495, 0.495) (just inside corner)
    word_c_box = [0.48, 0.48, 0.51, 0.51]
    cx_c, cy_c = compute_word_center(word_c_box)
    assert is_center_in_rects(cx_c, cy_c, [rect]) is True


def test_rejected_only_page():
    """Verify pages where all regions are excluded yield empty narration."""
    content = "Table 1: Data Table\nRow 1 | Value 100\nRow 2 | Value 200"
    layout_regions = [
        {"source": "docling", "label": "table", "narrate": False, "bbox": [0.1, 0.1, 0.9, 0.9]},
    ]
    words = [{"text": word, "bbox": [0.1, 0.1, 0.2, 0.15]} for word in content.split()]

    exclusions = compute_narration_exclusions(content, words, layout_regions)
    assert len(exclusions) == 1
    assert exclusions[0]["start"] == 0
    assert exclusions[0]["end"] == utf16_len(content)

    spoken = get_narratable_text(content=content, narration_exclusions=exclusions, layout_regions=layout_regions)
    assert spoken == ""


def test_equal_word_counts_do_not_imply_equal_column_order():
    content = "Left\nprose\nRight\ncaption"
    words = [
        {"text": "Left", "bbox": [0.1, 0.1, 0.2, 0.12]},
        {"text": "Right", "bbox": [0.6, 0.1, 0.7, 0.12]},
        {"text": "prose", "bbox": [0.1, 0.2, 0.2, 0.22]},
        {"text": "caption", "bbox": [0.6, 0.2, 0.7, 0.22]},
    ]
    regions = [{"narrate": False, "bbox": [0.5, 0, 1, 1]}]
    exclusions = compute_narration_exclusions(content, words, regions)
    spoken = get_narratable_text(content, exclusions)
    assert spoken == "Left\nprose"


def test_excluded_regions_do_not_exclude_uncovered_prose():
    content = "Caption\nBody"
    words = [
        {"text": "Caption", "bbox": [0.1, 0.1, 0.4, 0.2]},
        {"text": "Body", "bbox": [0.1, 0.5, 0.4, 0.6]},
    ]
    regions = [{"narrate": False, "bbox": [0, 0, 1, 0.3]}]
    exclusions = compute_narration_exclusions(content, words, regions)
    assert get_narratable_text(content, exclusions) == "Body"


def test_repeated_words_in_columns_use_their_visual_line():
    content = "Same body\nSame caption"
    words = [
        {"text": "Same", "bbox": [0.1, 0.1, 0.2, 0.12]},
        {"text": "body", "bbox": [0.21, 0.1, 0.3, 0.12]},
        {"text": "Same", "bbox": [0.6, 0.1, 0.7, 0.12]},
        {"text": "caption", "bbox": [0.71, 0.1, 0.9, 0.12]},
        {"text": "removed", "bbox": [0.1, 0.01, 0.2, 0.02]},
    ]
    regions = [{"narrate": False, "bbox": [0.5, 0, 1, 1]}]
    exclusions = compute_narration_exclusions(content, words, regions)
    assert get_narratable_text(content, exclusions) == "Same body"


def test_failed_detector_fallback():
    """Verify that if Docling detector raises an exception, parser falls back gracefully."""
    parser = PDFParserService()
    with patch.object(parser, "_detect_docling_regions", side_effect=RuntimeError("Docling model load error")):
        fixture_path = Path(__file__).resolve().parents[2] / "e2e" / "fixtures" / "docling-narration.pdf"
        result = parser.parse_pdf(str(fixture_path), use_ocr=False)

        assert result is not None
        assert result["num_pages"] == 3
        for page in result["pages"]:
            assert page["layout_regions"] == []
            assert page["narration_exclusions"] == []
            assert len(page["content"]) > 0
            assert len(page["bounding_boxes"]) > 0

            # Spoken text is unmasked legacy content
            spoken = get_narratable_text(
                content=page["content"],
                narration_exclusions=page["narration_exclusions"],
                layout_regions=page["layout_regions"],
            )
            assert spoken == page["content"]


def test_non_ascii_utf16_offsets():
    """Verify UTF-16 surrogate pairs (emojis) and non-ASCII chars do not introduce offset drift."""
    content = (
        "Header 🚀\n\n"
        "Café overview:\n"
        "DeepRead 📚 handles non-ASCII characters cleanly.\n\n"
        "Figure 1: Diagram 🎨"
    )
    hdr_end = content.index("\n\n")
    fig_start = content.index("Figure 1")

    u_hdr_end = char_to_utf16_offset(content, hdr_end)
    u_fig_start = char_to_utf16_offset(content, fig_start)
    u_total = utf16_len(content)

    exclusions = [
        {"start": 0, "end": u_hdr_end + 2},
        {"start": u_fig_start, "end": u_total},
    ]

    spoken = get_narratable_text(content=content, narration_exclusions=exclusions)
    assert "Header" not in spoken
    assert "Figure 1" not in spoken
    assert "Café overview:" in spoken
    assert "DeepRead 📚 handles non-ASCII characters cleanly." in spoken


def test_line_wrapping_and_source_offset_preservation():
    """Verify line-wrapped text preserves original newlines and character offsets for Whisper/PDF sync."""
    content = (
        "Multilingual speech recognition systems\n"
        "must preserve exact line wrapped\n"
        "formatting so that audio alignment\n"
        "matches rendered lines."
    )
    words = [
        {"text": "Multilingual", "bbox": [0.1, 0.1, 0.3, 0.12], "start": 0, "end": 12},
        {"text": "speech", "bbox": [0.31, 0.1, 0.4, 0.12], "start": 13, "end": 19},
        {"text": "recognition", "bbox": [0.41, 0.1, 0.55, 0.12], "start": 20, "end": 31},
        {"text": "systems", "bbox": [0.56, 0.1, 0.7, 0.12], "start": 32, "end": 39},
    ]
    layout_regions = [
        {"source": "docling", "label": "text", "narrate": True, "bbox": [0.1, 0.1, 0.8, 0.5]},
    ]
    exclusions = compute_narration_exclusions(content, words, layout_regions)
    assert exclusions == []

    spoken = get_narratable_text(content=content, narration_exclusions=exclusions)
    assert spoken == content
    assert "\n" in spoken


def test_ocr_geometry_compatibility():
    """Verify word boxes in unnormalized PDF points (> 1.0) correctly scale against page dimensions."""
    # Page size 612 x 792
    pw, ph = 612.0, 792.0

    # Word at x0=60, top=36, x1=260, bottom=44 (in PDF points, page header)
    word_header = {"text": "Header", "bbox": [60.0, 36.0, 260.0, 44.0]}
    # Word at x0=60, top=100, x1=200, bottom=120 (in PDF points, body text)
    word_body = {"text": "Body", "bbox": [60.0, 100.0, 200.0, 120.0]}

    # Layout region in normalized coords [0, 0, 1.0, 0.08] (page header)
    layout_regions = [
        {"source": "docling", "label": "page_header", "narrate": False, "bbox": [0.0, 0.0, 1.0, 0.08]},
        {"source": "docling", "label": "text", "narrate": True, "bbox": [0.0, 0.1, 1.0, 0.9]},
    ]

    content = "Header\n\nBody text follows here."
    exclusions = compute_narration_exclusions(
        content,
        [word_header, word_body],
        layout_regions,
        page_width=pw,
        page_height=ph,
    )

    assert len(exclusions) == 1
    # Header should be excluded
    assert exclusions[0]["start"] == 0
    assert exclusions[0]["end"] >= len("Header")


def test_parse_pdf_docling_fixture():
    fixture_path = (
        Path(__file__).resolve().parents[2] / "e2e" / "fixtures" / "docling-narration.pdf"
    )
    assert fixture_path.exists(), "e2e fixture docling-narration.pdf must exist"

    parser = PDFParserService()
    result = parser.parse_pdf(str(fixture_path), use_ocr=False)

    assert result is not None
    assert result["num_pages"] == 3
    assert len(result["pages"]) == 3

    page_one = result["pages"][0]
    assert len(page_one["content"]) > 0
    # Bounding boxes are word-level boxes
    boxes = page_one["bounding_boxes"]
    assert len(boxes) > 0
    assert "bbox" in boxes[0]
    assert "text" in boxes[0]

    # Layout regions are separate
    regions = page_one["layout_regions"]
    assert len(regions) > 0
    region_labels = [r["label"] for r in regions]
    assert "section_header" in region_labels
    assert "table" in region_labels
    assert "caption" in region_labels

    # Exclusions are computed
    exclusions = page_one["narration_exclusions"]
    assert len(exclusions) > 0

    spoken_page_one = get_narratable_text(
        content=page_one["content"],
        narration_exclusions=exclusions,
        layout_regions=regions,
    )
    assert "Neural Layout Analysis for Voice Narration" in spoken_page_one
    assert "Benchmark Evaluation" in spoken_page_one
    assert "Table 1" not in spoken_page_one
    assert "Architecture Precision" not in spoken_page_one

    # Page 2 is excluded-only
    page_two = result["pages"][1]
    spoken_page_two = get_narratable_text(
        content=page_two["content"],
        narration_exclusions=page_two["narration_exclusions"],
        layout_regions=page_two["layout_regions"],
    )
    assert spoken_page_two == ""

    # Page 3 has narratable text
    page_three = result["pages"][2]
    spoken_page_three = get_narratable_text(
        content=page_three["content"],
        narration_exclusions=page_three["narration_exclusions"],
        layout_regions=page_three["layout_regions"],
    )
    assert len(spoken_page_three) > 0


@pytest.mark.asyncio
async def test_play_page_honors_exclusions_and_guards_empty(
    client: AsyncClient,
    auth_header: dict,
    test_user: User,
    test_document: Document,
    db: Session,
):
    entry = db.query(LibraryEntry).filter(
        LibraryEntry.user_id == test_user.id,
        LibraryEntry.document_id == test_document.id,
    ).first()
    if not entry:
        db.add(LibraryEntry(user_id=test_user.id, document_id=test_document.id))
        db.flush()
    """Test play_page endpoint with narration_exclusions and layout_regions."""
    content_page1 = "Header text\n\nThis is narratable body content.\n\nFooter text"
    hdr_len = len("Header text")
    ftr_idx = content_page1.index("Footer text")

    p1 = db.query(ExtractedText).filter(
        ExtractedText.document_id == test_document.id,
        ExtractedText.page_number == 1,
    ).first()
    p1.content = content_page1
    p1.bounding_boxes = [{"text": "This", "bbox": [60, 100, 90, 115]}]
    p1.layout_regions = [
        {"source": "docling", "label": "page_header", "narrate": False, "bbox": [0, 0, 1, 0.1]},
        {"source": "docling", "label": "text", "narrate": True, "bbox": [0, 0.15, 1, 0.85]},
        {"source": "docling", "label": "page_footer", "narrate": False, "bbox": [0, 0.9, 1, 1.0]},
    ]
    p1.narration_exclusions = [
        {"start": 0, "end": hdr_len + 2},
        {"start": ftr_idx, "end": len(content_page1)},
    ]

    p2 = db.query(ExtractedText).filter(
        ExtractedText.document_id == test_document.id,
        ExtractedText.page_number == 2,
    ).first()
    p2.content = "Table 1: Benchmark\nCol A | Col B"
    p2.bounding_boxes = []
    p2.layout_regions = [
        {"source": "docling", "label": "table", "narrate": False, "bbox": [0, 0, 1, 1]},
    ]
    p2.narration_exclusions = [
        {"start": 0, "end": len("Table 1: Benchmark\nCol A | Col B")},
    ]
    db.flush()
    with patch("app.routers.tts._cached_or_queue") as mock_queue:
        mock_queue.return_value = {
            "status": "generating",
            "task_id": "mock-task-1",
            "text_hash": "mock-hash",
        }

        # Page 1 should narrate body content
        resp = await client.post(
            f"/api/tts/play/page/{test_document.id}/1",
            headers=auth_header,
        )
        assert resp.status_code == 200
        called_text = mock_queue.call_args[0][2]
        assert "This is narratable body content." in called_text
        assert "Header text" not in called_text
        assert "Footer text" not in called_text

        # Page 2 has only excluded content -> 400
        resp_two = await client.post(
            f"/api/tts/play/page/{test_document.id}/2",
            headers=auth_header,
        )
        assert resp_two.status_code == 400
        assert "no narratable text" in resp_two.json()["detail"].lower()


@pytest.mark.parametrize("bbox", [[], None, [None, 0, 1, 1], [0, 0, float('nan'), 1]])
def test_missing_word_geometry_remains_readable(bbox):
    regions = [{"narrate": False, "bbox": [0, 0, 1, 1]}]
    for offsets in ({}, {"start": 0, "end": 4}):
        words = [{"text": "Body", "bbox": bbox, **offsets}]
        assert compute_narration_exclusions("Body", words, regions) == []
