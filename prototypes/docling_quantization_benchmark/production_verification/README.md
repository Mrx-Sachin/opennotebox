# Production Docling backend verification

Production extracts PDF text through OpenDataLoader when available, otherwise
pdfplumber. Docling provides semantic regions used to exclude figures, tables,
and footnotes from narration. Matching extracted text alone cannot validate a
Docling backend change.

The production change selects `PyPdfiumDocumentBackend` and two CPU inference
threads. OCR and table reconstruction remain disabled. The layout model is
unchanged. The memory reduction comes with slower parsing.

## Reproduce

Use the existing environment. No new virtual environment is required.

```bash
/home/mrx/.local/venv/bin/python prototypes/docling_quantization_benchmark/production_verification/verify_production_docling.py --runs 5 --pages all --configs git_head_4t,production_current --save-json prototypes/docling_quantization_benchmark/production_verification/reviewed_full_results.json
```

Supply `--pdf /path/to/basper.pdf` to use a different fixture location.
The baseline is the exact parser source loaded from commit
`596cd40c848a7c9bb761d0bcc051c9763a21c202`, not a reimplementation.
`production_current` calls the working production parser directly.
Each configuration runs in a fresh subprocess, alternated over five rounds.
Kernel peak RSS includes imports, model loading, and native allocations. Units
are MiB, despite the historical `peak_rss_mb` field name. Latency includes imports
and conversion but excludes interpreter startup and shutdown.

Successful runs require a successful Docling conversion, every expected page,
and nonempty semantic regions. The artifact includes text and spoken-text
hashes, exclusion ranges, region labels, and boxes. Spatial agreement uses a
maximum-IoU one-to-one assignment, so region reordering does not count as a
classification change. Summary values are medians and observed ranges, not
confidence intervals. Repeated outputs are checked for stability separately.

The full standard Docling cross-check uses the same production extraction path
with table reconstruction enabled. Its own Markdown is also compared per page
against native extracted text after whitespace normalization. Token overlap
ignores word order; edit similarity includes ordering and Markdown formatting.
Neither is an accuracy score or a substitute for labeled ground truth.

## Scope and limitations

These results cover one 28-page text-layer PDF on this CPU host. They do not
establish general 99% accuracy, scanned-PDF performance, GPU use, concurrent
worker memory, or container-wide peak memory. Remaining baseline memory includes
Docling model/runtime imports. The default backend has a native threaded parser
and rendering path; PyPdfium changes that backend without replacing the layout
model. A lower inference thread count also reduces peak memory but costs time.

Verification used the user-required environment, Python 3.14.4. The project's
package metadata declares Python 3.11 through 3.12. Versions were Docling 2.144.0,
docling-parse 7.24.0, docling-core 2.105.0, PyTorch 2.14.1,
pypdfium2 5.14.0, pdfplumber 0.11.10, RapidFuzz 3.14.5, and SciPy 1.18.1.

The earlier prototype used single runs on pages 1-2. Its apparent 25% saving
was against full Docling with table reconstruction, which production already
omits. It was not evidence of a 25% improvement over production. Polling RSS
can miss short peaks; this verification uses kernel high-water RSS instead.

## Reviewed full-document result

Five alternating runs per side, all 28 pages, zero failures:

| Configuration | Peak RSS median [min, max], MiB | Time median [min, max], seconds |
| --- | --- | --- |
| Committed parser, default backend, four threads | 2849.27 [2790.13, 2901.02] | 27.78 [26.74, 28.14] |
| Production candidate, PyPdfium, two threads | 2265.82 [2265.28, 2266.04] | 40.71 [40.09, 41.01] |

The candidate reduces median peak RSS by 583.45 MiB, or 20.48%, and takes
46.54% longer. This is the combined backend/thread change, not a claim that
thread count alone produces the reduction. See `reviewed_full_results.json`.

All 28 page text hashes match the committed parser. Both return 403 regions;
398 labels agree, or 98.76%. Spatial mean IoU is 0.9350. Narration text and
exclusion ranges match exactly on 27 of 28 pages. On page 1 the candidate
excludes 116 additional characters from a footnote, verified against a rendered
crop of the source PDF. The raw extracted text retains those characters.
Other label changes do not change spoken output in this fixture.

All extracted-text and spoken-text hashes are stable across repeated runs.
Baseline boxes have floating-point variations below 0.000001, so raw region
objects are not byte-identical across baseline runs; candidate region objects
are identical across its five runs. This does not change exclusions or speech.

New verification files pass Ruff with the backend configuration. The production
parser has seven pre-existing Ruff findings also present at the baseline commit;
this change does not fix those unrelated findings.

## Standard Docling cross-check

`reviewed_standard_results.json` records one full-document cross-check with
Docling table reconstruction enabled, not a repeated performance benchmark.
The detector's 403 semantic regions and resulting narration match the committed
production detector across all 28 pages. The PyPdfium candidate's 98.76% label
agreement therefore also applies to this reference.

Docling's own Markdown is not identical to native production text. Mean
per-page token Jaccard is 79.72%; whitespace-normalized edit similarity is
69.97%. Those metrics include tables, formatting, reading order, and filtering
differences. They describe the existing extraction paths, not a regression from
this backend switch. The earlier two-page composite similarity was not evidence
of 99% accuracy across the whole document.

Final relevant regression suite:

```bash
cd backend
/home/mrx/.local/venv/bin/python -m pytest tests/test_docling_narration.py tests/test_pdf_parser_pypdfium.py tests/test_processing_reliability.py -q
```

Result: 26 passed, with Docling/Pydantic deprecation warnings. This is a focused
parser, narration, and document-processing suite, not the entire application
suite.
