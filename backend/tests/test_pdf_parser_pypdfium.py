"""Tests for PDFParserService with PyPdfium Docling backend."""

from pathlib import Path

import pytest
from app.services.narration import get_narratable_text
from app.services.pdf_parser import PDFParserService
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat


@pytest.fixture
def narration_pdf_fixture() -> Path:
    path = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "fixtures"
        / "docling-narration.pdf"
    )
    assert path.exists(), f"Fixture file not found at {path}"
    return path


def test_pdf_parser_configured_with_pypdfium(narration_pdf_fixture: Path, monkeypatch):
    """Verify DocumentConverter is instantiated with PyPdfiumDocumentBackend."""
    captured_backends = []

    from docling.document_converter import DocumentConverter

    orig_init = DocumentConverter.__init__

    def mock_init(self, *args, **kwargs):
        fmt_opts = kwargs.get("format_options", {})
        pdf_opt = fmt_opts.get(InputFormat.PDF)
        if pdf_opt is not None:
            captured_backends.append(pdf_opt.backend)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(DocumentConverter, "__init__", mock_init)

    parser = PDFParserService()
    regions = parser._detect_docling_regions(str(narration_pdf_fixture), num_pages=3)

    assert len(captured_backends) == 1
    assert captured_backends[0] is PyPdfiumDocumentBackend
    assert len(regions) == 3


def test_detect_docling_regions_bounding_boxes_and_labels(narration_pdf_fixture: Path):
    """Verify semantic layout regions, normalized boxes, and labels."""
    parser = PDFParserService()
    regions_by_page = parser._detect_docling_regions(
        str(narration_pdf_fixture), num_pages=3
    )

    assert 1 in regions_by_page
    assert 2 in regions_by_page
    assert 3 in regions_by_page

    page_1_regions = regions_by_page[1]
    assert len(page_1_regions) > 0

    labels = {r["label"] for r in page_1_regions}
    assert "section_header" in labels
    assert "table" in labels
    assert "caption" in labels

    for r in page_1_regions:
        assert r["source"] == "docling"
        assert isinstance(r["narrate"], bool)
        bbox = r["bbox"]
        assert len(bbox) == 4
        left, top, right, bottom = bbox
        assert 0.0 <= left <= right <= 1.0
        assert 0.0 <= top <= bottom <= 1.0


def test_parse_pdf_end_to_end_with_narration_exclusions(narration_pdf_fixture: Path):
    """Verify full parse_pdf flow correctly excludes non-narratable elements."""
    parser = PDFParserService()
    result = parser.parse_pdf(str(narration_pdf_fixture), use_ocr=False)

    assert result is not None
    assert result["num_pages"] == 3
    assert len(result["pages"]) == 3

    page_one = result["pages"][0]
    content = page_one["content"]
    assert len(content) > 0
    assert len(page_one["bounding_boxes"]) > 0
    assert len(page_one["layout_regions"]) > 0

    exclusions = page_one["narration_exclusions"]
    assert len(exclusions) > 0

    spoken_text = get_narratable_text(
        content=content,
        narration_exclusions=exclusions,
        layout_regions=page_one["layout_regions"],
    )

    assert "Neural Layout Analysis for Voice Narration" in spoken_text
    assert "Benchmark Evaluation" in spoken_text
    assert "Table 1" not in spoken_text
    assert "Architecture Precision" not in spoken_text
