"""Verify the frozen Whisper fixture or rebuild its PDF from the original file.

Expected physical-line text and geometry are reviewed test data. This utility
never regenerates expectations from the parser being tested.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.pdf_parser import PDFParserService


def verify(fixtures: Path) -> None:
    """Check the frozen PDF hash and fixed line boundaries without rewriting them."""
    expected = json.loads((fixtures / "whisper-expectations.json").read_text())
    pdf = fixtures / "whisper-pages-7-8-11.pdf"
    actual_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if actual_hash != expected["provenance"]["fixture_sha256"]:
        raise ValueError("Fixture PDF changed; review its contents and expectations")
    parsed = PDFParserService().parse_pdf(str(pdf), use_ocr=False)
    if not parsed or parsed["num_pages"] != 11:
        raise ValueError("Expected the eleven-page fixture")
    for passage in expected["passages"]:
        lines = parsed["pages"][passage["page_number"] - 1]["content"].splitlines()
        if passage["expected_line_text"] not in lines:
            raise ValueError(f"Physical line missing: {passage['id']}")
    corrupt = json.loads((fixtures / "whisper-corrupt-payload.json").read_text())
    if {page["page_number"] for page in corrupt["pages"]} != {7, 8, 11}:
        raise ValueError("Corrupt fixture must contain original pages 7, 8 and 11")
    print("Whisper fixture hash, physical lines and corrupt-payload pages verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--source-pdf", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "e2e/fixtures")
    args = parser.parse_args()
    if not args.verify:
        if args.source_pdf is None:
            parser.error("Specify --verify or --source-pdf <original PDF>")
        expected = json.loads((args.output_dir / "whisper-expectations.json").read_text())
        if hashlib.sha256(args.source_pdf.read_bytes()).hexdigest() != expected["provenance"]["source_sha256"]:
            parser.error("Original PDF hash does not match fixture provenance")
        import pypdf

        reader = pypdf.PdfReader(args.source_pdf)
        writer = pypdf.PdfWriter()
        for index in range(11):
            if index + 1 in {7, 8, 11}:
                writer.add_page(reader.pages[index])
            else:
                writer.add_blank_page(width=612, height=792)
        with (args.output_dir / "whisper-pages-7-8-11.pdf").open("wb") as stream:
            writer.write(stream)
    verify(args.output_dir)


if __name__ == "__main__":
    main()
