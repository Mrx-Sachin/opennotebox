"""OpenDataLoader PDF parser service.

Handles PDF parsing with OCR for scanned documents, extracts page-level text
with bounding boxes, headings, tables, and images.
"""
import re
from typing import Any, Dict, List, Optional, Set

from loguru import logger
from app.services.narration import (
    compute_narration_exclusions,
    is_narratable_label,
)


def _has_text_layer(file_path: str) -> bool:
    """Check if first page has meaningful text (not scanned)."""
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            first_page = pdf.pages[0]
            text = first_page.extract_text() or ""
            return len(text.strip()) > 100
    except Exception:
        return False  # fallback to OCR if detection fails


class PDFParserService:
    """Wraps OpenDataLoader PDF for page-level text extraction with bounding boxes."""

    def parse_pdf(
        self,
        file_path: str,
        use_ocr: bool | None = None,
        layout_mode: str = "auto",
    ) -> Optional[dict]:
        """
        Parse a PDF file and return extracted content.

        use_ocr: True to force OCR, False to skip, None to auto-detect.
        When auto-detecting, the first page is sampled for extractable text.

        layout_mode: "auto" | "single_column" | "research_paper" - controls
        multi-column reading-order reconstruction in the pdfplumber fallback.

        Returns dict with:
          - num_pages: int
          - pages: list of dicts, each with page_number, content, bounding_boxes, headings
        """
        if use_ocr is None:
            has_text = _has_text_layer(file_path)
            use_ocr = not has_text
            logger.info(f"OCR auto-detect for {file_path}: has_text={has_text}, use_ocr={use_ocr}")

        try:
            from opendataloaders import pdf as pdf_loader

            logger.info(f"Parsing PDF: {file_path} (use_ocr={use_ocr})")

            # OpenDataLoader returns a list of page objects
            pages = pdf_loader(file_path, use_ocr=use_ocr)

            result = {
                "num_pages": len(pages) if hasattr(pages, "__len__") else 0,
                "pages": [],
            }

            for i, page in enumerate(pages, 1):
                page_data = self._extract_page_data(page, i)
                result["pages"].append(page_data)

            self._enrich_with_docling_regions(file_path, result)
            logger.info(f"Successfully parsed {result['num_pages']} pages from {file_path}")
            return result

        except ImportError:
            logger.warning("OpenDataLoader not installed, falling back to pdfplumber")
            return self._fallback_parse(file_path, layout_mode=layout_mode)
        except Exception as e:
            logger.error(f"Failed to parse PDF {file_path}: {e}")
            raise

    def _extract_page_data(self, page, page_number: int) -> dict:
        """Extract structured data from a single page."""
        text = getattr(page, "text", str(page))
        bounding_boxes = self._get_bounding_boxes(page)
        headings = self._get_headings(page)

        pw = float(getattr(page, "width", 612.0) or 612.0)
        ph = float(getattr(page, "height", 792.0) or 792.0)
        return {
            "page_number": page_number,
            "content": text,
            "bounding_boxes": bounding_boxes,
            "headings": headings,
            "tables": getattr(page, "tables", []),
            "images": getattr(page, "images", []),
            "page_width": pw,
            "page_height": ph,
        }

    def _get_bounding_boxes(self, page) -> Optional[list]:
        """Extract word-level bounding boxes for synced highlighting."""
        try:
            if hasattr(page, "words"):
                return [
                    {"text": w.get("text", ""), "bbox": w.get("bbox", [])}
                    for w in page.words
                ]
            if hasattr(page, "blocks"):
                boxes = []
                for block in page.blocks:
                    if hasattr(block, "words"):
                        boxes.extend([
                            {"text": w.get("text", ""), "bbox": w.get("bbox", [])}
                            for w in block.words
                        ])
                return boxes
        except Exception as e:
            logger.warning(f"Bounding box extraction failed: {e}")
        return None

    def _get_headings(self, page) -> Optional[list]:
        """Detect headings in page content."""
        try:
            if hasattr(page, "headings"):
                return page.headings
            if hasattr(page, "blocks"):
                return [b for b in page.blocks if getattr(b, "heading", False)]
        except Exception:
            pass
        return None

    def _enrich_with_docling_regions(self, file_path: str, result: Optional[dict]) -> None:
        """Enrich parsed pages with Docling semantic regions and narration exclusions."""
        if not result or not result.get("pages"):
            return
        try:
            docling_regions = self._detect_docling_regions(file_path, result.get("num_pages", 0))
        except Exception as e:
            logger.warning(f"Docling region detection failed: {e}")
            docling_regions = {}
        for page in result["pages"]:
            p_no = page["page_number"]
            regions = docling_regions.get(p_no, [])
            pw = float(page.get("page_width", 612.0) or 612.0)
            ph = float(page.get("page_height", 792.0) or 792.0)
            exclusions = compute_narration_exclusions(
                page["content"],
                page.get("bounding_boxes"),
                regions,
                page_width=pw,
                page_height=ph,
            )
            page["layout_regions"] = regions
            page["narration_exclusions"] = exclusions

    def _detect_docling_regions(
        self, file_path: str, num_pages: int
    ) -> dict[int, list[dict]]:
        """Run separate Docling layout detection with do_ocr=False and do_table_structure=False."""
        regions_by_page: dict[int, list[dict]] = {p: [] for p in range(1, num_pages + 1)}
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                AcceleratorOptions,
                AcceleratorDevice,
            )
            from docling.datamodel.base_models import InputFormat

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = False
            pipeline_options.do_table_structure = False
            pipeline_options.accelerator_options = AcceleratorOptions(
                num_threads=4, device=AcceleratorDevice.CPU
            )
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            conv_res = converter.convert(file_path)

            # Prefer raw layout predictions for region-only classification
            if hasattr(conv_res, "pages") and isinstance(conv_res.pages, list):
                for idx, page_obj in enumerate(conv_res.pages):
                    page_no = getattr(page_obj, "page_no", idx + 1)
                    if page_no not in regions_by_page:
                        regions_by_page[page_no] = []
                    pw = float(getattr(page_obj.size, "width", 612.0))
                    ph = float(getattr(page_obj.size, "height", 792.0))

                    clusters = []
                    if (
                        hasattr(page_obj, "predictions")
                        and hasattr(page_obj.predictions, "layout")
                        and hasattr(page_obj.predictions.layout, "clusters")
                        and page_obj.predictions.layout.clusters
                    ):
                        clusters = page_obj.predictions.layout.clusters

                    for c in clusters:
                        label = (
                            c.label.value
                            if hasattr(c.label, "value")
                            else str(c.label)
                        )
                        bbox = c.bbox
                        if hasattr(bbox, "to_top_left_origin"):
                            tl = bbox.to_top_left_origin(page_height=ph)
                            l, t, r, b = tl.l, tl.t, tl.r, tl.b
                        else:
                            l, t, r, b = bbox.l, bbox.t, bbox.r, bbox.b
                        norm_bbox = [
                            max(0.0, min(1.0, float(l) / pw)),
                            max(0.0, min(1.0, float(t) / ph)),
                            max(0.0, min(1.0, float(r) / pw)),
                            max(0.0, min(1.0, float(b) / ph)),
                        ]
                        narrate = is_narratable_label(label)
                        regions_by_page[page_no].append({
                            "label": label,
                            "narrate": narrate,
                            "bbox": norm_bbox,
                            "source": "docling",
                        })

            # If layout clusters were empty, fallback to doc.iterate_items()
            total_extracted = sum(len(v) for v in regions_by_page.values())
            if total_extracted == 0 and hasattr(conv_res, "document"):
                doc = conv_res.document
                for item, _ in doc.iterate_items():
                    label = getattr(item, "label", None)
                    label_str = label.value if hasattr(label, "value") else str(label)
                    prov = getattr(item, "prov", [])
                    for p in prov:
                        p_no = getattr(p, "page_no", None)
                        bbox = getattr(p, "bbox", None)
                        if p_no in regions_by_page and bbox is not None:
                            doc_page = doc.pages.get(p_no)
                            if doc_page and doc_page.size:
                                pw = float(doc_page.size.width)
                                ph = float(doc_page.size.height)
                            else:
                                pw, ph = 612.0, 792.0
                            if hasattr(bbox, "to_top_left_origin"):
                                tl = bbox.to_top_left_origin(page_height=ph)
                                l, t, r, b = tl.l, tl.t, tl.r, tl.b
                            else:
                                l, t, r, b = bbox.l, bbox.t, bbox.r, bbox.b
                            norm_bbox = [
                                max(0.0, min(1.0, float(l) / pw)),
                                max(0.0, min(1.0, float(t) / ph)),
                                max(0.0, min(1.0, float(r) / pw)),
                                max(0.0, min(1.0, float(b) / ph)),
                            ]
                            narrate = is_narratable_label(label_str)
                            regions_by_page[p_no].append({
                                "label": label_str,
                                "narrate": narrate,
                                "bbox": norm_bbox,
                                "source": "docling",
                            })
        except Exception as e:
            logger.warning(f"Docling region detection failed for {file_path}: {e}")
            return {}

        return regions_by_page

    def _fallback_parse(
        self, file_path: str, *, layout_mode: str = "auto"
    ) -> Optional[dict]:
        """Fallback to pdfplumber if OpenDataLoader is unavailable."""
        try:
            import pdfplumber

            result = {"num_pages": 0, "pages": []}
            with pdfplumber.open(file_path) as pdf:
                result["num_pages"] = len(pdf.pages)
                for i, page in enumerate(pdf.pages, 1):
                    words = page.extract_words(x_tolerance=1.5)
                    text = extract_reading_order_text(
                        page, words, layout_mode=layout_mode
                    )
                    boxes = [
                        {"text": w.get("text", ""), "bbox": [w.get("x0"), w.get("top"), w.get("x1"), w.get("bottom")]}
                        for w in words
                    ]
                    page_data = {
                        "page_number": i,
                        "content": text,
                        "bounding_boxes": boxes,
                        "headings": None,
                        "page_width": float(page.width),
                        "page_height": float(page.height or 0.0),
                    }
                    result["pages"].append(page_data)
            self._enrich_with_docling_regions(file_path, result)
            return result
        except ImportError:
            logger.error("Neither OpenDataLoader nor pdfplumber is available")
            return None


# ---------------------------------------------------------------------------
# Column-aware reading order (research paper mode)
#
# Approach (aligned with common practice: pdfplumber crop, PyMuPDF
# multi_column, XY-cut band partitioning):
#   1. Drop non-content: running headers, page numbers, figure-label junk
#   2. Detect the column gutter (x-histogram preferred; row-gap as backup)
#   3. Partition the page into vertical bands of full-width content
#      (titles, spanning tables) vs two-column body
#   4. In two-column bands, emit the ENTIRE left column then the ENTIRE
#      right column - never interleave co-baseline lines
#
# This is more reliable than per-row split/reassemble, which fails when
# the gutter estimate is slightly off or when section heads sit mid-column.

MIN_COLUMN_ROWS = 8
MIN_GUTTER_WIDTH = 8  # points
HEADER_MARGIN_PT = 48  # drop running titles / page nums in this top band
FOOTER_MARGIN_PT = 36


def extract_reading_order_text(
    page, words: list, *, layout_mode: str = "auto"
) -> str:
    """Extract text from a page with correct column reading order.

    ``layout_mode``:
    - ``"auto"`` - detect columns; strip headers/footers/figure junk
    - ``"research_paper"`` - lower thresholds; more aggressive noise filter
    - ``"single_column"`` - no column detection (still strips header/footer)

    Deliberately does NOT dehyphenate line-wrapped words ("infor-\\nmation")
    here, tempting as that is for narration/search quality. The frontend's
    click-to-read matches PDF.js's own rendered text-layer spans (built
    independently from the PDF file, oblivious to any transform we apply on
    our side) against this string by literal substring search, then finds
    which sentence contains the matched offset. Changing this string's
    length/content shifts every sentence offset after the change point out
    from under that matching - tried it once, and every click near a
    hyphenated word silently fell back to "no match, start from sentence
    0", i.e. clicking anywhere in the bottom-left of a two-column research
    paper page jumped to reading the header. If dehyphenation is worth
    doing, it needs to happen narration-side only (see narrationFilter.ts's
    approach), never on the string sentence offsets are computed against.
    """
    if not words:
        return page.extract_text() or ""

    page_width = float(page.width)
    page_height = float(getattr(page, "height", None) or 0)

    words = _filter_noise_words(
        words, page, page_width, page_height,
        aggressive=(layout_mode == "research_paper"),
    )
    if not words:
        return ""

    if layout_mode == "single_column":
        return _rows_to_text(_group_into_rows(words))

    min_rows = 6 if layout_mode == "research_paper" else MIN_COLUMN_ROWS
    min_words = 25 if layout_mode == "research_paper" else 30

    # Prefer x-histogram: more stable mid-document (row-gap can latch onto
    # tables/figures and return a wrong gutter, which then interleaves text).
    hist = _detect_gutter_histogram(
        words, page_width, page_height, min_words=min_words
    )
    rows = _group_into_rows(words)
    row_gap = _detect_gutter(rows, page_width, min_rows=min_rows)
    gutter = _pick_gutter(hist, row_gap)

    if not gutter:
        return _rows_to_text(rows)

    return _extract_banded_columns(
        words, gutter, page_width, min_rows=min_rows
    )


def _pick_gutter(
    hist: Optional[tuple], row_gap: Optional[tuple]
) -> Optional[tuple]:
    """Choose the better gutter estimate.

    Histogram midpoints are usually more accurate on real paper pages.
    Only prefer row-gap when histogram is missing, or when both agree
    closely (use row-gap's tighter bounds in that case).
    """
    if hist and row_gap:
        hist_mid = (hist[0] + hist[1]) / 2
        row_mid = (row_gap[0] + row_gap[1]) / 2
        if abs(hist_mid - row_mid) <= 12:
            # Agree: use hist x-range (stable) with row-gap column extents
            return (hist[0], hist[1], row_gap[2], row_gap[3])
        # Disagree: trust histogram (row-gap often fooled by tables/figures)
        return hist
    return hist or row_gap


def _filter_noise_words(
    words: list, page, page_width: float, page_height: float, *, aggressive: bool
) -> list:
    """Drop running headers, page numbers, rotated labels, and figure junk.

    These are the main sources of "TTS reads junk" and of false gutters on
    figure-heavy pages (axis ticks, scatter labels, diagram callouts).
    """
    if not words:
        return words

    # --- Rotated / non-upright text (chart axis labels) ---
    words = _drop_rotated_words(page, words)

    # --- Text inside dense drawing regions (architecture diagrams, plots) ---
    # Keep caption-like lines ("Figure 2. ...", "Table 1. ...").
    words = _drop_words_in_figure_regions(page, words)

    if not page_height:
        return words

    # --- Running header / page number in top band ---
    top_band = [w for w in words if w["top"] < HEADER_MARGIN_PT]
    body = [w for w in words if w["top"] >= HEADER_MARGIN_PT]

    # Keep top-band only when it looks like a real title block (page 1).
    # Thin one-line headers with a trailing page number are dropped.
    if top_band:
        tops = [w["top"] for w in top_band]
        span = max(tops) - min(tops)
        has_page_num = any(re.fullmatch(r"\d{1,3}", w["text"]) for w in top_band)
        if has_page_num or (len(top_band) <= 12 and span < 14):
            pass  # drop header band
        else:
            body = top_band + body

    # --- Footer page numbers ---
    body = [
        w for w in body
        if w["top"] < page_height - FOOTER_MARGIN_PT
        or not re.fullmatch(r"\d{1,3}", w["text"])
    ]

    # --- Row-level figure debris (spaced single chars, axis ticks) ---
    rows = _group_into_rows(body)
    cleaned: list = []
    for row in rows:
        if _is_figure_junk_row(row):
            continue
        cleaned.extend(row["words"])

    if aggressive and cleaned:
        mid_y = page_height / 2
        cleaned = [
            w for w in cleaned
            if not (
                re.fullmatch(r"\d{1,3}", w["text"])
                and (
                    w["top"] < HEADER_MARGIN_PT + 10
                    or w["top"] > page_height - FOOTER_MARGIN_PT - 10
                    or (
                        w["x0"] > page_width * 0.9
                        and abs(w["top"] - mid_y) > page_height * 0.3
                    )
                )
            )
        ]

    return cleaned


def _drop_rotated_words(page, words: list) -> list:
    """Remove words whose glyphs are non-upright (rotated axis labels)."""
    try:
        chars = getattr(page, "chars", None) or []
    except Exception:
        return words
    if not chars:
        return words

    # Grid of cells that contain any non-upright character
    bad = set()
    for c in chars:
        if c.get("upright", True):
            continue
        # 4pt cells
        bad.add((int(c["x0"] // 4), int(c["top"] // 4)))
        bad.add((int(((c["x0"] + c["x1"]) / 2) // 4), int(c["top"] // 4)))
    if not bad:
        return words

    kept = []
    for w in words:
        cx = int(((w["x0"] + w["x1"]) / 2) // 4)
        cy = int(w["top"] // 4)
        if (cx, cy) in bad or (cx, cy - 1) in bad or (cx, cy + 1) in bad:
            continue
        kept.append(w)
    return kept


def _drop_words_in_figure_regions(page, words: list) -> list:
    """Drop body text that sits inside dense vector-drawing regions.

    Captions starting with Figure/Table/Fig. are kept so the reader still
    hears what the figure is about.
    """
    fig_cells, bin_pt = _figure_density_cells(page)
    if not fig_cells:
        return words

    # Expand figure cells by 1 neighbor so labels on the figure edge go too
    expanded = set(fig_cells)
    for x, y in fig_cells:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                expanded.add((x + dx, y + dy))

    # Y-bands that contain any figure-dense cell (labels sit beside curves)
    fig_y = {y for _, y in expanded}

    rows = _group_into_rows(words)
    kept_words: list = []
    for row in rows:
        text = _row_text(row).strip()
        # Always keep figure/table captions
        if re.match(r"^(Figure|Fig\.|Table|Algorithm|Listing)\s*\d", text, re.I):
            kept_words.extend(row["words"])
            continue
        # Drop row if most of its words fall in the figure mask
        in_fig = 0
        on_fig_band = False
        for w in row["words"]:
            cx = int(((w["x0"] + w["x1"]) / 2) // bin_pt)
            cy = int(w["top"] // bin_pt)
            if (cx, cy) in expanded:
                in_fig += 1
            if cy in fig_y:
                on_fig_band = True
        if row["words"] and in_fig / len(row["words"]) >= 0.6:
            continue
        # Short callouts/labels sitting in a figure band (e.g. "MLP",
        # "English transcription", emoji markers) - not real body prose.
        if (
            on_fig_band
            and len(row["words"]) <= 8
            and len(text) < 55
            and not re.match(r"^\d+(\.\d+)*\.?\s+\w+", text)  # keep "2.4 Title"
        ):
            continue
        kept_words.extend(row["words"])
    return kept_words


def _figure_density_cells(page, bin_pt: float = 28.0):
    """Return (set of (x,y) cells, bin_pt) with high vector-drawing density."""
    from collections import Counter

    cells: Counter = Counter()
    try:
        lines = getattr(page, "lines", None) or []
        curves = getattr(page, "curves", None) or []
        rects = getattr(page, "rects", None) or []
    except Exception:
        return set(), bin_pt

    def mark(x0, top, x1, bottom, weight=1.0):
        if x1 < x0:
            x0, x1 = x1, x0
        if bottom < top:
            top, bottom = bottom, top
        x_lo = max(0, int(x0 // bin_pt))
        x_hi = int(x1 // bin_pt)
        y_lo = max(0, int(top // bin_pt))
        y_hi = int(bottom // bin_pt)
        for y in range(y_lo, y_hi + 1):
            for x in range(x_lo, x_hi + 1):
                cells[(x, y)] += weight

    # Curves dominate real figures (plots, architecture diagrams).
    # Straight lines/rects are common in tables - weight them lightly so we
    # don't mask Table 1 style content as a figure.
    for curve in curves:
        mark(curve["x0"], curve["top"], curve["x1"], curve["bottom"], 3.0)
    for line in lines:
        mark(line["x0"], line["top"], line["x1"], line["bottom"], 0.25)
    for rect in rects:
        mark(rect["x0"], rect["top"], rect["x1"], rect["bottom"], 0.15)

    if not cells:
        return set(), bin_pt

    # Require meaningful curve activity - pure table pages have lines only
    total_weight = sum(cells.values())
    if total_weight < 60 or len(curves) < 15:
        return set(), bin_pt

    # Cells at/above ~55th percentile, min weight 4 (needs real curve mass)
    weights = sorted(cells.values())
    thresh = max(4.0, weights[int(len(weights) * 0.55)])
    return {k for k, v in cells.items() if v >= thresh}, bin_pt


def _is_figure_junk_row(row: dict) -> bool:
    """True for diagram/axis-label rows that should not be read aloud."""
    ws = row["words"]
    if not ws:
        return True
    n = len(ws)
    single = sum(1 for w in ws if len(w["text"]) == 1)
    # "T h e q u i c k b r o w n f o x" style
    if n >= 6 and single / n >= 0.55:
        return True
    # Chart legend codes / axis debris ("80 TG MT", "MY LO", ")%(").
    # Skip this check when the row has a wide internal gap (real 2-col line
    # that happens to use short tokens).
    max_gap = 0.0
    for i in range(len(ws) - 1):
        max_gap = max(max_gap, ws[i + 1]["x0"] - ws[i]["x1"])
    if n >= 2 and max_gap < MIN_GUTTER_WIDTH:
        short = sum(1 for w in ws if len(w["text"]) <= 2)
        avg_len = sum(len(w["text"]) for w in ws) / n
        if short / n >= 0.7 and avg_len <= 2.5:
            return True
        if any(re.search(r"[)(%\[\]]", w["text"]) for w in ws) and avg_len <= 3:
            return True
    # Isolated axis tick
    if n == 1 and re.fullmatch(r"\d{1,4}", ws[0]["text"]):
        return True
    # Reversed-looking tokens common on rotated axes that slipped through
    # ("nommoC", "egarevA", "ecioV") - mostly letters, no lowercase vowel
    # pattern of English, length >= 4, and reverse looks more word-like.
    if n <= 3:
        rev_hits = 0
        for w in ws:
            t = w["text"]
            if len(t) >= 4 and t.isalpha():
                rev = t[::-1]
                # heuristic: original has unusual camel/odd casing from rotation
                if t[0].islower() and any(c.isupper() for c in t[1:]):
                    rev_hits += 1
                elif rev[0].isupper() and rev[1:].islower() and not t[1:].islower():
                    rev_hits += 1
        if rev_hits and rev_hits == n:
            return True
    return False


def _extract_banded_columns(
    words: list, gutter: tuple, page_width: float, *, min_rows: int
) -> str:
    """Partition into full-width vs two-column vertical bands; emit L then R.

    Full-width bands (page title, spanning tables, figure captions that
    cross the gutter) are emitted in place. Two-column bands emit every
    left-column line top-to-bottom, then every right-column line - the
    correct reading order for research papers.
    """
    gutter_x0, gutter_x1, column_x0, column_x1 = gutter
    mid = (gutter_x0 + gutter_x1) / 2
    span_tol = page_width * 0.05

    rows = _group_into_rows(words)
    classified: List[dict] = []
    for row in rows:
        side = _classify_row_side(row, mid, gutter_x0, gutter_x1, column_x0, column_x1, span_tol)
        if side == "split":
            # Co-baseline left+right content: force into two half-rows
            left_w = [w for w in row["words"] if (w["x0"] + w["x1"]) / 2 < mid]
            right_w = [w for w in row["words"] if (w["x0"] + w["x1"]) / 2 >= mid]
            if left_w:
                r = _finalize_row(left_w)
                r["side"] = "left"
                classified.append(r)
            if right_w:
                r = _finalize_row(right_w)
                r["side"] = "right"
                classified.append(r)
        else:
            row = dict(row)
            row["side"] = side
            classified.append(row)

    classified.sort(key=lambda r: r["top"])

    left_n = sum(1 for r in classified if r["side"] == "left")
    right_n = sum(1 for r in classified if r["side"] == "right")
    if left_n < min_rows or right_n < min_rows:
        return _rows_to_text(rows)

    # Walk top-to-bottom; group consecutive non-full rows into a column band
    output: List[str] = []
    left_buf: List[dict] = []
    right_buf: List[dict] = []

    def flush_cols():
        # Sort each buffer by vertical position so mid-column heads stay ordered
        left_buf.sort(key=lambda r: r["top"])
        right_buf.sort(key=lambda r: r["top"])
        for r in left_buf:
            output.append(_row_text(r))
        for r in right_buf:
            output.append(_row_text(r))
        left_buf.clear()
        right_buf.clear()

    for row in classified:
        if row["side"] == "full":
            flush_cols()
            output.append(_row_text(row))
        elif row["side"] == "left":
            left_buf.append(row)
        else:
            right_buf.append(row)
    flush_cols()

    return "\n".join(line for line in output if line.strip())


def _classify_row_side(
    row: dict, mid: float,
    gutter_x0: float, gutter_x1: float,
    column_x0: float, column_x1: float, span_tol: float,
) -> str:
    """Return 'left', 'right', 'full', or 'split' for a visual row."""
    ws = row["words"]
    if not ws:
        return "full"

    left_w = [w for w in ws if (w["x0"] + w["x1"]) / 2 < mid]
    right_w = [w for w in ws if (w["x0"] + w["x1"]) / 2 >= mid]

    if left_w and right_w:
        # Content on both sides of the gutter mid-line.
        gap_x0 = max(w["x1"] for w in left_w)
        gap_x1 = min(w["x0"] for w in right_w)
        gap_overlaps_gutter = gap_x0 <= gutter_x1 + 2 and gap_x1 >= gutter_x0 - 2
        gap_wide = (gap_x1 - gap_x0) >= MIN_GUTTER_WIDTH * 0.5

        # Short centered titles (a few words floating over the gutter) are
        # full-width, not two columns - only split when at least one side
        # looks like real column body (multi-word or near a column edge).
        left_near = min(w["x0"] for w in left_w) <= column_x0 + span_tol * 2
        right_near = max(w["x1"] for w in right_w) >= column_x1 - span_tol * 2
        multi = len(left_w) >= 2 or len(right_w) >= 2
        looks_like_columns = (left_near and right_near) or (
            multi and (left_near or right_near)
        )

        if gap_overlaps_gutter and gap_wide and looks_like_columns:
            return "split"
        # Spans most of the column width -> full-width title/table row
        if (
            row["x0"] <= column_x0 + span_tol
            and row["x1"] >= column_x1 - span_tol
        ):
            return "full"
        # Both sides look like columns even without a clean gap measure
        if looks_like_columns:
            return "split"
        return "full"

    if left_w and not right_w:
        return "left"
    if right_w and not left_w:
        return "right"
    return "full"


def _rows_to_text(rows: list) -> str:
    return "\n".join(_row_text(r) for r in rows if r.get("words"))


def _detect_gutter(
    rows: list, page_width: float, *, min_rows: int = MIN_COLUMN_ROWS,
) -> Optional[tuple]:
    """Find a column gutter as a consensus position across many rows' own
    internal word gaps, rather than scanning the whole page at once.

    Scanning the whole page for one big empty x-band (the naive approach)
    gets fooled the moment ANY other element - a wide table, a figure
    caption - happens to occupy that x-range anywhere on the page. Instead,
    for each row (words already grouped by baseline - which for a genuine
    2-column row means it holds BOTH columns' words spliced together), find
    that row's own single largest internal gap. A handful of rows sharing
    almost the same gap position is exactly what a real column gutter looks
    like; unrelated elements don't share a consistent gap position.
    """
    mid = page_width / 2
    search_margin = page_width * 0.3
    candidates = []
    for row in rows:
        ws = row["words"]
        if len(ws) < 2:
            continue
        best_gap, best_i = 0.0, None
        for i in range(len(ws) - 1):
            gap = ws[i + 1]["x0"] - ws[i]["x1"]
            if gap > best_gap:
                best_gap, best_i = gap, i
        if best_i is not None and best_gap >= MIN_GUTTER_WIDTH:
            gap_x0, gap_x1 = ws[best_i]["x1"], ws[best_i + 1]["x0"]
            center = (gap_x0 + gap_x1) / 2
            if abs(center - mid) <= search_margin:
                candidates.append((gap_x0, gap_x1, ws[0]["x0"], ws[-1]["x1"]))

    if len(candidates) < min_rows:
        return None

    candidates.sort(key=lambda c: c[1])
    median_gap_x1 = candidates[len(candidates) // 2][1]
    agreeing = [c for c in candidates if abs(c[1] - median_gap_x1) <= 15]
    if len(agreeing) < min_rows:
        return None

    gutter_x0 = sorted(c[0] for c in agreeing)[len(agreeing) // 2]
    gutter_x1 = sorted(c[1] for c in agreeing)[len(agreeing) // 2]
    column_x0 = sorted(c[2] for c in agreeing)[len(agreeing) // 2]
    column_x1 = sorted(c[3] for c in agreeing)[len(agreeing) // 2]
    return (gutter_x0, gutter_x1, column_x0, column_x1)


def _detect_gutter_histogram(
    words: list, page_width: float, page_height: Optional[float] = None,
    *, min_words: int = 30,
) -> Optional[tuple]:
    """Detect the column gutter by finding an empty band in the x-histogram.

    Unlike ``_detect_gutter`` this method does not require rows from both
    columns to share the same baseline, so it catches title pages, footnotes,
    and references where the two columns are vertically offset.

    The key idea: bin all word centers along the x-axis and find a run of
    near-empty bins between the left and right column content bands.
    """
    from collections import Counter

    if len(words) < min_words:
        return None

    # Ignore the top 8% of the page (title, authors, running headers)
    if page_height:
        body = [w for w in words if w["top"] >= page_height * 0.08]
    else:
        body = words
    if len(body) < min_words:
        body = words

    bin_size = 4.0
    bins: Counter = Counter()
    for w in body:
        cx = (w["x0"] + w["x1"]) / 2
        bins[int(cx // bin_size)] += 1

    mid = page_width / 2
    # Only search for the gutter in the central 30-70% of the page width
    lo = int((page_width * 0.32) // bin_size)
    hi = int((page_width * 0.68) // bin_size)
    # The gutter can be at most ~12% of page width (covers most standard layouts)
    max_empty_bins = int((page_width * 0.12) // bin_size)
    min_empty_bins = max(2, int(MIN_GUTTER_WIDTH // bin_size))

    # Find runs of near-empty bins (0-1 words each)
    best = None  # (score, start_bin, end_bin, left_count, right_count)
    b = lo
    while b <= hi:
        if bins.get(b, 0) <= 1:
            start = b
            while b <= hi and bins.get(b, 0) <= 1:
                b += 1
            end = b - 1
            width_bins = end - start + 1
            if min_empty_bins <= width_bins <= max_empty_bins:
                # Check there is substantial content on both sides within ~80pt
                side = int(80 // bin_size)
                left_count = sum(bins.get(i, 0) for i in range(max(lo, start - side), start))
                right_count = sum(bins.get(i, 0) for i in range(end + 1, min(hi, end + 1 + side)))
                if left_count >= 20 and right_count >= 20:
                    center = ((start + end + 1) / 2) * bin_size
                    score = abs(center - mid) - 0.01 * (left_count + right_count)
                    if best is None or score < best[0]:
                        best = (score, start, end, left_count, right_count)
        else:
            b += 1

    if best is None:
        return None

    _, start, end, lc, rc = best
    gutter_x0 = start * bin_size
    gutter_x1 = (end + 1) * bin_size

    left_words = [w for w in body if w["x1"] <= gutter_x1]
    right_words = [w for w in body if w["x0"] >= gutter_x0]

    if len(left_words) < 20 or len(right_words) < 20:
        return None

    ratio = min(len(left_words), len(right_words)) / max(len(left_words), len(right_words))
    if ratio < 0.35:
        return None

    # Column extents from body words
    column_x0 = sorted(w["x0"] for w in left_words)[len(left_words) // 10]
    column_x1 = sorted(w["x1"] for w in right_words)[int(len(right_words) * 0.9)]
    return (gutter_x0, gutter_x1, column_x0, column_x1)


def _group_into_rows(words: list, tolerance: float = 3.0) -> list:
    """Cluster words into visual rows by vertical position, sorted left to right."""
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows: list = []
    for w in ordered:
        if rows and abs(w["top"] - rows[-1]["top"]) <= tolerance:
            rows[-1]["words"].append(w)
            rows[-1]["top"] = min(rows[-1]["top"], w["top"])
        else:
            rows.append({"top": w["top"], "words": [w]})
    return [_finalize_row(r["words"], top=r["top"]) for r in rows]


def _finalize_row(words: list, top: Optional[float] = None) -> dict:
    words = sorted(words, key=lambda w: w["x0"])
    return {
        "top": top if top is not None else min(w["top"] for w in words),
        "x0": words[0]["x0"],
        "x1": words[-1]["x1"],
        "words": words,
    }


def _row_text(row: dict) -> str:
    return " ".join(w["text"] for w in row["words"])
