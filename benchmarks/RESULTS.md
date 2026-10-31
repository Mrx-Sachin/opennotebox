# Verified resource improvements

Local verification on 2026-10-30, against baseline `596cd40`.
Raw samples, query plans, profiles, and dependency versions are in
[`results/resource-improvements.json`](results/resource-improvements.json) and
[`results/admission-connections.json`](results/admission-connections.json).
Commands and measurement limits are in [README.md](README.md).

## Findings

- Lightweight TextViewer responses remove 7,200 bytes from the 100-page text
  fixture: 203,677 to 196,477 bytes, with equal text and headings. Thirty
  simultaneous requests completed without errors. Their batch completion time
  was 160.45 ms before, range 158.11 to 184.15, and 139.53 ms after, range
  134.94 to 142.61. These are five alternating rounds, not individual p95
  latencies. At one and fifteen concurrent requests the ranges overlap, so do
  not claim a reliable latency improvement at those loads.
- Non-PDF parsers normally store null geometry. An earlier fixture populated
  geometry on text documents and exaggerated the TextViewer improvement. Those
  timings were discarded. A separate PDF page now verifies geometry retention.
- The partial unembedded-chunk index returns the same six IDs using fewer
  buffer accesses than filtering all 600 chunks in the selected document.
  Median query time was 0.228 ms before, range 0.205 to 0.546, and 0.040 ms
  after, range 0.035 to 0.121. This is a small absolute saving, not a material
  model-inference speedup. The redundant composite index is removed while
  the equivalent unique constraint remains. Upgrade, downgrade, and replay
  passed on disposable PostgreSQL.
- Redis connection reuse reduces connections for 100 sequential leases from
  100 to 1 in every measured round. Across ten short-lived event loops with
  five leases each, connections fall from 50 to 10. Pools close after each
  event loop. The thirty-request burst still queues against twenty foreground
  slots; its timing ranges overlap, so no burst speedup is claimed.
- JSON compression works through Traefik. SSE events arrive separately,
  audio stays uncompressed, and WebSocket echo succeeds. The compression ratio
  of this repetitive text fixture does not predict real document ratios.
- Pending document-list polling changes from two seconds to eight seconds.
  This trades status freshness for fewer requests while processing continues.

Profiling the page handlers separately from timed runs shows that projecting
columns reduces ORM row construction and JSON decoding work. Remaining costs
include database queries, access checks, response serialization, and client JSON
decoding. The SQL query plans attribute the retrieval change to the partial
index. Redis connection counts directly establish reduced connection churn;
these lease tests do not measure end-to-end chat latency.

## Correctness checks

- Full backend suite: 537 passed, 2 skipped. Five existing warnings concern
  Docling deprecations and SQLite expression-index reflection.
- Redis focused suite with warnings treated as errors: 9 passed. Includes
  shared admission limits, renewals, cancellation, failures, loop teardown,
  process forks, thirty callers with two connections, and replacement cleanup.
- Frontend production build, TypeScript check, and ESLint passed.
- Two Playwright browser tests passed against the actual built frontend with
  mocked backend responses. They verify lightweight query selection, rendered
  content and headings, chapter navigation, the eight-second polling interval,
  completed status rendering, and polling termination. Separate HTTP tests
  above exercise the real backend and database.
- Document projection preserves default full responses, pagination, access
  isolation, and the single-page PDF response. The lightweight HTTP request
  uses three queries total, including authentication and document access.

## Limits

The API runs in the required existing host Python environment, not a constrained
Docker container. Only disposable PostgreSQL, Redis, and Traefik are resource
limited by these runners. Local Python and dependency versions differ from the
production Docker lockfile. The API RSS observation follows both variants and
does not measure memory savings.

No whole-stack memory cap, under-1-GB result, sustained thirty-user capacity,
or thirty simultaneous model responses is established. Docling and TTS were
excluded from the changes. No malloc trimming hook was added because the
allocation experiment did not establish useful retained-memory savings.
Production and existing preview services and data were left untouched.

Independent Gemini review of the index migration, projection, frontend,
compression, benchmark code, and corrected evidence found no blockers.
