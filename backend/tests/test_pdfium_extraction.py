"""Guard production PDF geometry and physical lines while evaluating PDFium."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pdfplumber
import pytest

from app.services.pdf_parser import PDFParserService, _has_text_layer, extract_reading_order_text

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def geometry_pdf(tmp_path):
    """Use the benchmark's deterministic Unicode/columns/rotation/crop fixture."""
    import runpy

    benchmark = runpy.run_path(str(ROOT / "benchmarks/bench_pdf_extraction.py"))
    path = tmp_path / "geometry.pdf"
    benchmark["make_geometry_fixture"](path)
    return path


def _assert_plumber_parity(path):
    parser = PDFParserService()
    with patch.object(parser, "_detect_docling_regions") as detector:
        actual = parser._fallback_parse(str(path))
    detector.assert_not_called()
    with pdfplumber.open(path) as pdf:
        assert actual["num_pages"] == len(pdf.pages)
        for source, parsed in zip(pdf.pages, actual["pages"]):
            words = source.extract_words(x_tolerance=1.5)
            expected = extract_reading_order_text(source, words)
            assert parsed["content"] == expected
            assert parsed["content"].splitlines() == expected.splitlines()
            assert parsed["bounding_boxes"] == [
                {"text": w["text"], "bbox": [w[key] for key in ("x0", "top", "x1", "bottom")]}
                for w in words
            ]
            assert parsed["page_width"] == source.width
            assert parsed["page_height"] == source.height
            assert parsed["layout_regions"] == []
            assert parsed["narration_exclusions"] == []
    return actual


@pytest.mark.parametrize("name", ["e2e-sample.pdf", "citation-evidence.pdf", "docling-narration.pdf"])
def test_upload_geometry_and_physical_lines_keep_existing_contract(name):
    _assert_plumber_parity(ROOT / "e2e/fixtures" / name)


def test_unicode_repetition_columns_hyphens_crop_and_rotation_keep_geometry(geometry_pdf):
    actual = _assert_plumber_parity(geometry_pdf)
    body = actual["pages"][1]
    assert "café naïve αβ infor-\n" in body["content"]
    assert "infor-mation" not in body["content"]
    assert body["content"].index("Same left 17") < body["content"].index("Same right 00")
    same = [box for box in body["bounding_boxes"] if box["text"] == "Same"]
    assert len(same) == 36
    assert len({tuple(box["bbox"]) for box in same}) == 36


def test_sparse_cover_does_not_force_text_document_to_ocr(geometry_pdf):
    with pdfplumber.open(geometry_pdf) as pdf:
        assert len(pdf.pages[0].extract_text()) < 100
    assert _has_text_layer(str(geometry_pdf))


def test_no_text_still_requests_ocr(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    path = tmp_path / "blank.pdf"
    doc = canvas.Canvas(str(path))
    doc.showPage()
    doc.save()
    assert not _has_text_layer(str(path))
    received = []

    def load(file_path, *, use_ocr):
        received.append(use_ocr)
        return [SimpleNamespace(text="OCR output", words=[], width=612, height=792)]

    monkeypatch.setitem(__import__("sys").modules, "opendataloaders", SimpleNamespace(pdf=load))
    parser = PDFParserService()
    with patch.object(parser, "_detect_docling_regions") as detector:
        result = parser.parse_pdf(str(path))
    detector.assert_not_called()
    assert received == [True]
    assert result["pages"][0]["content"] == "OCR output"
    assert result["pages"][0]["layout_regions"] == []
    assert result["pages"][0]["narration_exclusions"] == []


def test_explicit_ocr_preference_is_forwarded(geometry_pdf, monkeypatch):
    received = []

    def load(file_path, *, use_ocr):
        received.append(use_ocr)
        return []

    monkeypatch.setitem(__import__("sys").modules, "opendataloaders", SimpleNamespace(pdf=load))
    parser = PDFParserService()
    parser.parse_pdf(str(geometry_pdf), use_ocr=True)
    parser.parse_pdf(str(geometry_pdf), use_ocr=False)
    assert received == [True, False]


def test_explicit_detector_failures_are_visible_to_task(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "docling.document_converter", None)
    with pytest.raises(ModuleNotFoundError):
        PDFParserService()._detect_docling_regions("unread.pdf", 1)
