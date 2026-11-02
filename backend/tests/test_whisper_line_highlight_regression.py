"""Regression test for Whisper PDF line highlighting and physical line preservation.

Verifies:
- Physical lines remain split on newlines and do not collapse into paragraph blocks.
- Real PDFParserService on committed fixture PDF preserves exact symbol characters:
  '10×' (multiplication U+00D7), 'X→en' (arrow U+2192), '(CY)' (parentheses).
- Every word in target lines has an individual, geometrically valid word box.
- Words in target lines do not cross column gutters or merge with adjacent lines.
- Oracle rejection: corrupt legacy payload is confirmed to fail both the physical-line
  split check and the word-level geometry check.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from app.services.pdf_parser import PDFParserService

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "e2e/fixtures"
FIXTURE_PDF = FIXTURES_DIR / "whisper-pages-7-8-11.pdf"
EXPECTATIONS_JSON = FIXTURES_DIR / "whisper-expectations.json"
CORRUPT_JSON = FIXTURES_DIR / "whisper-corrupt-payload.json"


@pytest.fixture(scope="module")
def expectations():
    """Load independent expectations."""
    assert EXPECTATIONS_JSON.exists(), f"Missing {EXPECTATIONS_JSON}"
    with open(EXPECTATIONS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def corrupt_payload():
    """Load corrupt legacy payload."""
    assert CORRUPT_JSON.exists(), f"Missing {CORRUPT_JSON}"
    with open(CORRUPT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def parsed_fixture():
    """Run real PDFParserService on the committed fixture PDF."""
    assert FIXTURE_PDF.exists(), f"Missing {FIXTURE_PDF}"
    svc = PDFParserService()
    parsed = svc.parse_pdf(str(FIXTURE_PDF))
    assert parsed is not None, "PDFParserService returned None"
    assert parsed.get("num_pages") == 11, f"Expected 11 pages, got {parsed.get('num_pages')}"
    return parsed


def test_fixture_provenance_and_integrity(expectations):
    """Verify fixture metadata and independent expectations structure."""
    prov = expectations.get("provenance", {})
    assert prov.get("source_sha256") == "9185ccc01c249b68444af1808f17868b71c11afecb29c3fbc59111f6bf5b16ec"
    assert prov.get("fixture_pages") == [7, 8, 11]
    assert len(expectations.get("passages", [])) == 6
    assert hashlib.sha256(FIXTURE_PDF.read_bytes()).hexdigest() == prov["fixture_sha256"]


@pytest.mark.parametrize(
    "passage_id",
    [
        "page8-welsh",
        "page8-example",
        "page8-measuring",
        "page7-multiplier",
        "page7-caution",
        "page11-wer",
    ],
)
def test_physical_lines_remain_split(parsed_fixture, expectations, passage_id):
    """Verify each target line is a distinct physical line bounded by newlines."""
    passage = next(p for p in expectations["passages"] if p["id"] == passage_id)
    p_num = passage["page_number"]
    page_data = next(p for p in parsed_fixture["pages"] if p["page_number"] == p_num)
    content = page_data.get("content", "")

    expected_line = passage["expected_line_text"]
    assert expected_line in content, f"Passage text not found in page {p_num}: {expected_line}"

    # Verify physical line boundary: line must appear exactly in split lines
    physical_lines = [line.strip() for line in content.split("\n") if line.strip()]
    assert (
        expected_line in physical_lines
    ), f"Line is not a separate physical line in page {p_num}. Content lines: {physical_lines}"

    # Verify preceding and following lines match sequence (no line skipping)
    line_idx = physical_lines.index(expected_line)
    if passage.get("prev_line_text"):
        assert (
            physical_lines[line_idx - 1] == passage["prev_line_text"]
        ), f"Previous line mismatch: expected '{passage['prev_line_text']}', got '{physical_lines[line_idx - 1]}'"
    if passage.get("next_line_text"):
        assert (
            physical_lines[line_idx + 1] == passage["next_line_text"]
        ), f"Next line mismatch: expected '{passage['next_line_text']}', got '{physical_lines[line_idx + 1]}'"


def test_special_symbols_preserved(parsed_fixture, expectations):
    """Verify critical font symbols (10×, X→en, (CY)) are preserved intact."""
    for passage in expectations["passages"]:
        symbol = passage.get("symbol")
        if not symbol:
            continue
        p_num = passage["page_number"]
        page_data = next(p for p in parsed_fixture["pages"] if p["page_number"] == p_num)
        content = page_data.get("content", "")
        assert symbol in content, f"Special symbol '{symbol}' missing from page {p_num} text"


@pytest.mark.parametrize(
    "passage_id",
    [
        "page8-welsh",
        "page8-example",
        "page8-measuring",
        "page7-multiplier",
        "page7-caution",
        "page11-wer",
    ],
)
def test_word_level_geometry(parsed_fixture, expectations, passage_id):
    """Verify word-level bounding boxes exist, have valid geometry, and respect columns."""
    passage = next(p for p in expectations["passages"] if p["id"] == passage_id)
    p_num = passage["page_number"]
    page_data = next(p for p in parsed_fixture["pages"] if p["page_number"] == p_num)
    boxes = page_data.get("bounding_boxes", [])

    expected_words = passage["words"]
    expected_count = passage["word_count"]
    assert len(expected_words) == expected_count

    # Find the matching box sequence
    matched_boxes = []
    for i in range(len(boxes) - expected_count + 1):
        if [b["text"] for b in boxes[i : i + expected_count]] == expected_words:
            matched_boxes = boxes[i : i + expected_count]
            break

    assert matched_boxes, f"Could not find word boxes matching {expected_words} on page {p_num}"

    # Geometry checks for each word box
    prev_x0 = -1.0
    for word_box, expected_box in zip(matched_boxes, passage["word_boxes"]):
        assert word_box["bbox"] == pytest.approx(expected_box["bbox"], abs=0.01)
        x0, top, x1, bottom = word_box["bbox"]
        width = x1 - x0
        height = bottom - top

        assert width > 0, f"Word {word_box['text']} has invalid width: {width}"
        assert height > 0, f"Word {word_box['text']} has invalid height: {height}"
        assert top >= 0 and bottom <= 792, f"Word {word_box['text']} outside vertical bounds: [{top}, {bottom}]"

        # Horizontal ordering: left-to-right
        assert (
            x0 >= prev_x0
        ), f"Word {word_box['text']} out of order: x0 {x0} < prev_x0 {prev_x0}"
        prev_x0 = x0

    # Column boundary check
    min_x = min(b["bbox"][0] for b in matched_boxes)
    max_x = max(b["bbox"][2] for b in matched_boxes)
    column = passage["column"]
    if column == "left":
        assert max_x < 300.0, f"Left column line '{passage['expected_line_text']}' crossed into right column: max_x={max_x}"
    elif column == "right":
        assert min_x > 295.0, f"Right column line '{passage['expected_line_text']}' crossed into left column: min_x={min_x}"


def test_oracle_rejects_corrupt_legacy_payload(corrupt_payload, expectations):
    """Verify that corrupt legacy Docling payload fails physical line and word geometry tests."""
    for passage in expectations["passages"]:
        p_num = passage["page_number"]
        corrupt_page = next((p for p in corrupt_payload["pages"] if p["page_number"] == p_num), None)
        assert corrupt_page is not None, f"Corrupt payload missing page {p_num}"

        content = corrupt_page.get("content", "")
        corrupt_boxes = corrupt_page.get("bounding_boxes", [])

        # 1. Physical line split check: in corrupt legacy payload, lines were merged
        # into markdown paragraphs or tables, so the clean physical line is NOT present.
        corrupt_physical_lines = [line.strip() for line in content.split("\n") if line.strip()]
        expected_line = passage["expected_line_text"]

        # Specifically for page 8 Welsh line: it was collapsed into a markdown paragraph block!
        if passage["id"] == "page8-welsh":
            assert expected_line not in corrupt_physical_lines, (
                "Expected corrupt payload to NOT have 'amount of Welsh' as a separate line"
            )

        # 2. Word-level geometry check: corrupt payload only had 2-6 paragraph boxes for the entire page
        assert len(corrupt_boxes) <= 10, (
            f"Expected corrupt page {p_num} to have few paragraph boxes, found {len(corrupt_boxes)}"
        )
        # Verify word-level boxes are completely absent in the corrupt payload
        words = passage["words"]
        found_word_sequence = any(
            [b["text"] for b in corrupt_boxes[i : i + len(words)]] == words
            for i in range(max(0, len(corrupt_boxes) - len(words) + 1))
        )
        assert not found_word_sequence, (
            f"Corrupt payload unexpectedly contained word-level boxes for {passage['id']}"
        )
