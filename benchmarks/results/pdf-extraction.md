Production keeps pdfplumber word extraction and the existing reading-order
algorithm. The PDFium candidate did not preserve the canonical text contract.

Run the comparison from the repository root:

```bash
/home/mrx/.local/venv/bin/python benchmarks/bench_pdf_extraction.py
```

The JSON artifact records five alternating fresh-process repeats for each of
five PDFs. Every repeat extracts word boxes, character direction, vector masks
and text using the existing column-aware reading-order algorithm. Extraction
includes imports and retains the full parsed result. It excludes Docling, OCR,
database writes and PDF.js rendering. No text-only timing is used.

The three small existing fixtures preserve canonical content with the candidate,
but their font-box coordinates differ by as much as 3.04 points. The generated
fixture covers Unicode, repeated words, columns, line-wrapped hyphens, rotated
text, a rotated page and cropping. Its word sequence differs on the rotated
page, and its coordinates differ by as much as 32.44 points. Existing production
behavior on rotated pages is preserved, including its noise filtering.

All 28 pages of basper.pdf differ in canonical physical-line content. On page
one, line eleven ends in `zero-` with pdfplumber and `zero\u0002` with PDFium.
Only six pages preserve the raw word sequence. The JSON records each page's
first different line and unmatched words. Repeated words are compared by
occurrence, so coordinate deltas become diagnostic rather than exact glyph
alignment when segmentation or order differs.

The candidate converts crop coordinates to the displayed page, while existing
pdfplumber extraction uses MediaBox coordinates. PDFium's loose character boxes
and path grouping also differ from pdfminer. These differences need further
work and browser evidence before a production switch can be considered safe.

Raw timing samples remain available for reruns. Shared-host CPU contention,
including concurrent test runs, prevents treating them as a production speed
ratio. No PDFium speed improvement is claimed. The supported upload change is
deferring semantic Docling analysis until explicitly requested, while preserving
the current extraction and highlighting inputs.
