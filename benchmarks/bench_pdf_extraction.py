"""Compare complete word geometry and reading order in isolated processes.

PDFium is an experimental candidate only. Production retains pdfplumber until
content, physical lines, and highlight coordinates can be proved compatible.
Run from the repository root with the existing Python environment.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import resource
import statistics
import subprocess
import sys
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def make_geometry_fixture(path: Path) -> None:
    """Build reproducible Unicode, columns, hyphenation, crop and rotation cases."""
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(TTFont("GeometryDejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    buffer = BytesIO()
    doc = canvas.Canvas(buffer, pagesize=(612, 792), invariant=1)
    for page_no in range(4):
        doc.setFont("GeometryDejaVu", 11)
        if page_no == 0:
            doc.drawString(72, 700, "Sparse cover")
        else:
            for row in range(18):
                y = 700 - row * 18
                left = f"Same left {row:02d} café naïve αβ infor-"
                right = f"Same right {row:02d} mation repeated words."
                doc.drawString(50, y, left)
                doc.drawString(330, y, right)
            doc.saveState()
            doc.translate(25, 180)
            doc.rotate(90)
            doc.drawString(0, 0, "Rotated axis label")
            doc.restoreState()
        doc.showPage()
    doc.save()
    reader = PdfReader(buffer)
    reader.pages[2].rotate(90)
    reader.pages[3].cropbox.lower_left = (30, 40)
    reader.pages[3].cropbox.upper_right = (580, 760)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        writer.write(output)


def _display_box(box, crop, rotation):
    """Convert PDF bottom-left coordinates to the displayed crop's top-left."""
    left, bottom, right, top = crop
    width, height = right - left, top - bottom

    def point(x, y):
        x, y = x - left, y - bottom
        if rotation == 90:
            return y, x
        if rotation == 180:
            return width - x, y
        if rotation == 270:
            return height - y, width - x
        return x, height - y

    xs, ys = zip(*(point(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])))
    return {"x0": min(xs), "top": min(ys), "x1": max(xs), "bottom": max(ys)}


def extract_pdfium(file_path: str) -> dict:
    """Experimental complete words, geometry, vector masks, and reading order.

    Each character is read by PDFium's Unicode API, avoiding the UCS-2 limitation
    of get_text_range. Loose boxes include font bounds. This is intentionally
    kept out of production: PDFium's glyph metrics and text segmentation differ.
    """
    import pdfplumber
    import pypdfium2 as pdfium
    from app.services.pdf_parser import extract_reading_order_text
    from pypdfium2 import raw

    result = {"num_pages": 0, "pages": []}
    with pdfium.PdfDocument(file_path) as pdf:
        result["num_pages"] = len(pdf)
        for index in range(len(pdf)):
            with closing(pdf[index]) as page:
                crop, rotation = page.get_bbox(), page.get_rotation()
                width, height = page.get_size()
                chars = []
                with closing(page.get_textpage()) as textpage:
                    for char_index in range(textpage.count_chars()):
                        codepoint = raw.FPDFText_GetUnicode(textpage, char_index)
                        if not codepoint or codepoint in (10, 13):
                            continue
                        char = _display_box(textpage.get_charbox(char_index, loose=True), crop, rotation)
                        angle = raw.FPDFText_GetCharAngle(textpage, char_index)
                        char.update(
                            text=chr(codepoint),
                            upright=abs(math.sin(angle)) < 0.01 and rotation in (0, 180),
                            doctop=char["top"],
                            height=char["bottom"] - char["top"],
                            width=char["x1"] - char["x0"],
                        )
                        chars.append(char)
                shapes = {"lines": [], "curves": [], "rects": []}
                for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
                    segment_count = raw.FPDFPath_CountSegments(obj)
                    curved = any(
                        raw.FPDFPathSegment_GetType(raw.FPDFPath_GetPathSegment(obj, i))
                        == raw.FPDF_SEGMENT_BEZIERTO
                        for i in range(segment_count)
                    )
                    kind = "curves" if curved else "lines" if segment_count <= 2 else "rects"
                    shapes[kind].append(_display_box(obj.get_bounds(), crop, rotation))
                words = pdfplumber.utils.extract_words(chars, x_tolerance=1.5)
                adapter = SimpleNamespace(width=width, height=height, chars=chars, **shapes)
                adapter.extract_text = lambda: ""
                text = extract_reading_order_text(adapter, words, layout_mode="auto")
                result["pages"].append({
                    "page_number": index + 1,
                    "content": text,
                    "bounding_boxes": [
                        {"text": word["text"], "bbox": [word[k] for k in ("x0", "top", "x1", "bottom")]}
                        for word in words
                    ],
                    "page_width": width,
                    "page_height": height,
                })
    return result


def compare(baseline: dict, candidate: dict) -> dict:
    """Compare physical lines and each repeated word occurrence in source order."""
    pages = []
    for old, new in zip(baseline["pages"], candidate["pages"]):
        old_boxes, new_boxes = old["bounding_boxes"], new["bounding_boxes"]
        old_words = [box["text"] for box in old_boxes]
        new_words = [box["text"] for box in new_boxes]
        old_by_text = {}
        for box in old_boxes:
            old_by_text.setdefault(box["text"], []).append(box["bbox"])
        occurrences = Counter()
        deltas = []
        for box in new_boxes:
            word = box["text"]
            occurrence = occurrences[word]
            occurrences[word] += 1
            matches = old_by_text.get(word, [])
            if occurrence < len(matches):
                deltas.append(max(abs(a - b) for a, b in zip(matches[occurrence], box["bbox"])))
        old_lines, new_lines = old["content"].splitlines(), new["content"].splitlines()
        first_line_difference = next((
            {"line": i + 1, "baseline": old_lines[i] if i < len(old_lines) else None,
             "candidate": new_lines[i] if i < len(new_lines) else None}
            for i in range(max(len(old_lines), len(new_lines)))
            if i >= len(old_lines) or i >= len(new_lines) or old_lines[i] != new_lines[i]
        ), None)
        pages.append({
            "page": old["page_number"],
            "content_equal": old["content"] == new["content"],
            "physical_lines_equal": old["content"].splitlines() == new["content"].splitlines(),
            "word_sequence_equal": old_words == new_words,
            "word_multiset_equal": Counter(old_words) == Counter(new_words),
            "dimensions_equal": (old["page_width"], old["page_height"]) == (new["page_width"], new["page_height"]),
            "baseline_words": len(old_words), "candidate_words": len(new_words),
            "matched_occurrences": len(deltas),
            "max_coordinate_delta_pt": max(deltas, default=0),
            "occurrences_over_2pt": sum(delta > 2 for delta in deltas),
            "first_different_line": first_line_difference,
            "baseline_only_words": dict(Counter(old_words) - Counter(new_words)),
            "candidate_only_words": dict(Counter(new_words) - Counter(old_words)),
            "baseline_excerpt": old["content"][:200],
            "candidate_excerpt": new["content"][:200],
        })
    return {"page_count_equal": baseline["num_pages"] == candidate["num_pages"], "pages": pages}


def worker(engine: str, file_path: str) -> None:
    # Imports are included in elapsed time. JSON writing is excluded; RSS includes
    # the retained complete result, just as upload persistence requires.
    start = time.perf_counter()
    if engine == "pdfplumber":
        from app.services.pdf_parser import PDFParserService
        parsed = PDFParserService()._fallback_parse(file_path)
    else:
        parsed = extract_pdfium(file_path)
    elapsed = time.perf_counter() - start
    print(json.dumps({"seconds": elapsed, "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "parsed": parsed}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=["pdfplumber", "pdfium"])
    parser.add_argument("--file")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/results/pdf-extraction.json")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.file)
        return
    if args.repeats < 5:
        parser.error("at least five alternating repeats are required")
    fixture = args.output.parent / "pdf-extraction-geometry.pdf"
    make_geometry_fixture(fixture)
    files = sorted((ROOT / "e2e/fixtures").glob("*.pdf")) + [fixture]
    basper = Path("/home/mrx/local/github/meta_deepread/prototypes/docling_quantization_benchmark/basper.pdf")
    if basper.exists():
        files.append(basper)
    report = {
        "python": sys.version,
        "versions": {name: importlib.metadata.version(name) for name in ("pdfplumber", "pdfminer-six", "pypdfium2")},
        "scope": "Fresh process full extraction including import, per-word geometry, vector masks and existing normalized reading-order code. No Docling, OCR, database or browser rendering timing.",
        "decision": "Keep production pdfplumber. PDFium is experimental and has not proved physical-line/content and coordinate parity; no PDFium upload speed claim.",
        "limitations": [
            "Shared-host CPU contention is uncontrolled; recorded timings do not support a production speed ratio.",
            "Per-occurrence geometry comparison is diagnostic when word segmentation differs; unmatched words are counted separately.",
            "PDFium loose font boxes and path grouping differ from pdfminer metrics and vector grouping.",
            "The cropped-page candidate uses displayed crop coordinates; production pdfplumber currently uses media coordinates.",
            "Backend parity cannot establish browser PDF.js highlighting correctness. No PDFium switch is made.",
        ],
        "files": [],
    }
    for file_path in files:
        samples = {engine: [] for engine in ("pdfplumber", "pdfium")}
        artifacts = {}
        comparison = None
        for round_index in range(args.repeats):
            order = ("pdfplumber", "pdfium") if round_index % 2 == 0 else ("pdfium", "pdfplumber")
            for engine in order:
                process = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), "--worker", engine, "--file", str(file_path)],
                    check=True, capture_output=True, text=True,
                )
                result = json.loads(process.stdout)
                parsed = result.pop("parsed")
                digest = hashlib.sha256(json.dumps(parsed, sort_keys=True).encode()).hexdigest()
                result.update(round=round_index + 1, output_sha256=digest)
                samples[engine].append(result)
                if engine in artifacts:
                    assert parsed == artifacts[engine], "Extraction changed between identical fresh processes"
                artifacts[engine] = parsed
            comparison = compare(artifacts["pdfplumber"], artifacts["pdfium"])
        entry = {
            "path": str(file_path), "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
            "samples": samples, "comparison": comparison,
            "median_seconds": {engine: statistics.median(s["seconds"] for s in values) for engine, values in samples.items()},
        }
        report["files"].append(entry)
        print(file_path.name, entry["median_seconds"], flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
