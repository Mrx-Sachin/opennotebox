# Optional PDF layout verification

PDF text extraction now completes without Docling. The upload modal offers
Open document before extraction or summaries finish. Section summaries remain
automatic background work. Reading-overlay requests use a persistent,
asset-wide state and atomic task claim; ready and processing requests do not
enqueue another analysis. Empty successful results remain ready. Failure has
an explicit retry action. Re-parsing invalidates earlier layout work.

## Measured extraction and peak memory

Command, from the repository root:

```sh
/home/mrx/.local/venv/bin/python benchmarks/bench_optional_layout.py \
  --pdf /home/mrx/local/github/meta_deepread/prototypes/docling_quantization_benchmark/basper.pdf \
  --output benchmarks/results/optional-layout.json
```

Five alternating pairs used fresh Python processes and the same 28-page PDF.
The baseline is commit `596cd40`. Every run produced 28 pages and 18,010 word
boxes. Page text matched exactly across all ten outputs. Every baseline run
also produced 403 actual Docling regions; every initial candidate run produced
zero layout regions. Errors: zero.

| Measure | Eager layout | Deferred layout |
| --- | --- | --- |
| Extraction time, median | 36.13 s | 2.46 s |
| Extraction time, range | 34.98-47.21 s | 2.29-2.59 s |
| Parser process peak RSS, median | 2,649 MiB | 274 MiB |
| Parser process peak RSS, range | 2,559-2,697 MiB | 274.0-274.6 MiB |

This removes CPU model loading/inference and exclusion mapping from initial
extraction. It defers that work until the user requests it. It does not make
Docling inference itself faster. CPU tests and other verification ran on this
shared 12-thread host during measurement, contributing to baseline variation.
The raw artifact records versions, samples, fixture hash and final host load.

The timer covers automatic OCR detection, text extraction and word geometry.
It excludes network upload, database writes, broker delay and browser rendering.
The peak RSS is for an isolated parser process, not average RAM, Docker memory,
GPU memory, or whole-stack usage. A worker that previously loaded Docling may
retain that model until recycled. These results do not establish 30-user
throughput or new production Docker limits. Scanned-document OCR latency was
not benchmarked.

## Correctness evidence

```sh
/home/mrx/.local/venv/bin/python benchmarks/verify_optional_layout.py
/home/mrx/.local/venv/bin/python -m pytest backend/tests -q
/home/mrx/.local/venv/bin/python -m pytest \
  backend/tests/test_optional_layout.py \
  backend/tests/test_pdfium_extraction.py \
  backend/tests/test_docling_narration.py \
  backend/tests/test_processing_reliability.py -q
```

The disposable PostgreSQL check uses 512 MiB and one CPU for the database,
independent of existing services. Thirty simultaneous real handler calls with
separate sessions enqueue one build. The actual CPU Docling task then writes
17 regions and four exclusion ranges for the three-page test fixture. Text
and word boxes remain unchanged, repeat delivery is ignored, and migration
upgrade/downgrade/upgrade preserves legacy empty results. Dispatch is recorded
in memory; this check does not launch a Celery broker/worker pair. Raw output
is in `results/optional-layout-integration.json`.

The full backend run passed 556 tests with two skips. After the final task
geometry adjustment, all 43 affected tests passed again. Warnings concern
existing Docling deprecations and SQLite expression-index reflection.

New HTTP/task tests cover authentication, shared membership, failures, explicit
retry, queued/running/ready idempotency, lease expiry, concurrent claims,
reparse races, cropped/rotated geometry, and private conditional file downloads.
Summary dispatch failure no longer changes successfully extracted text to
failed. PDF file requests revalidate a private ETag after access checks.

Touched backend modules, new tests and benchmark scripts pass scoped Ruff.
The existing PDF parser has seven lint findings, verified identical to its
baseline findings. These unrelated findings were left unchanged.

The final production frontend build, TypeScript check and ESLint passed.
Twenty browser tests passed against the actual built frontend and PDF.js:
ten new optional-layout cases, eight existing narration/layout cases, and two
earlier resource regression cases. The existing suite also consumed the real
parser-plus-Docling artifact, rather than only its committed fixture. Backend
API responses and TTS audio are mocked in these browser tests; actual backend
and PostgreSQL behavior is verified separately above.

The new cases cover opening from the upload modal during extraction, recovery
from a page's initial 404, physical-line highlights including hyphen wraps,
rapid clicks, status polling, empty results and reloads, nonblocking errors,
document switching, reparse invalidation without a canvas remount, active
playback preservation, prefetched future-page audio replacement, and late
responses from pre-layout requests. Screenshots are in
`../_scratch/optional-layout-ready.png`, `optional-layout-processing.png`, and
`optional-layout-failed.png`.

The standalone new browser suite starts and stops its own frontend process:

```sh
cd frontend && npm run build && npm run lint && npx tsc --noEmit
cd ../e2e && ./node_modules/.bin/playwright test \
  tests/optional-layout.spec.ts --project=chromium \
  --output=../_scratch/optional-layout-browser
```

No production services were deployed or migrated. This worktree includes the
new Alembic migration `a91c2e7d408b`, verified as the single migration head.

## PDFium decision

PDFium was not adopted. The full candidate includes character and word geometry
and existing reading-order logic. It changes canonical physical-line text on
all 28 pages of the research-paper sample and shifts some crop/rotation
coordinates. These are material highlighting risks. Production retains
pdfplumber's exact word boxes and physical line breaks. See
`results/pdf-extraction.md` and `results/pdf-extraction.json` for the five-PDF,
five-repeat comparison and first differing lines. No text-only speed claim is
used to justify a parser switch.
