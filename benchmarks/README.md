# Resource improvement verification

Run from the repository root with the existing Python environment:

```bash
/home/mrx/.local/venv/bin/python benchmarks/verify_resource_improvements.py --output _scratch/resource-verification
```

Docker must be available. The runner starts uniquely named disposable PostgreSQL
and Traefik containers, binds random loopback ports, and removes its own containers
and volumes on exit. It never connects to production databases. PostgreSQL is
limited to 512 MiB and one CPU; Traefik to 128 MiB and half a CPU. The API runs as a
host Python process, so these limits do not establish a whole-stack resource cap.

Both runners pin the original baseline commit `596cd40`; `--baseline` can select
another revision. Baseline routers and admission code execute only inside these
disposable benchmark processes.

The HTTP fixture uses real authentication, document access checks, database
sessions, page routes, and response serialization. Thirty accounts access three
100-page synthetic text documents with null geometry, matching the non-PDF
parsers used by TextViewer. A separate PDF page checks geometry compatibility.
Full responses
execute the committed baseline document router under a benchmark-only prefix;
lightweight responses execute the candidate TextViewer path. The runner also
checks equality with the candidate's default full response.
Each side runs five alternating rounds at 1, 15, and 30 simultaneous requests.
Reported latency is the time to finish the entire batch, including client JSON
decoding. It is neither individual request p95 nor sustained user capacity.

The PostgreSQL comparison loads identical 12,000-row chunk fixtures into separate
schemas. It compares committed baseline DDL against the working tree DDL, checks
equal results, saves actual query plans, and exercises migration upgrade,
downgrade, and replay. The partial index benefit depends on the fraction of
unembedded chunks; it is not a claim about every retrieval query.

Separate profiling calls identify database JSON decoding and response
serialization costs without including profiling overhead in timed rounds.
The proxy checks actual successful JSON compression, SSE event arrival times,
uncompressed audio, and WebSocket echo through the candidate Traefik config.
Fixture text is repetitive, so its compression ratio is not representative of
arbitrary documents.

`results.json` preserves all samples, ranges, output checks, plans, and profiles.
The API RSS observation is taken after both variants and cannot establish a
before/after memory reduction. These tests exclude model inference, document
parsing, TTS, and mixed long-running user sessions. They do not establish that
the entire application fits under 1 GB or supports thirty simultaneous model
requests on the available GPU.

The runner records local dependency versions. The required existing Python
environment currently uses Python 3.14; the backend Dockerfile uses Python 3.11.
Treat timings as a local comparison, not production Docker latency estimates.

Run the Redis lease comparison separately:

```bash
/home/mrx/.local/venv/bin/python benchmarks/bench_admission_connections.py --output _scratch/admission-results.json
```

It executes the actual committed admission implementation and the candidate
against its own disposable Redis container, limited to 64 MiB and half a CPU.
Five alternating rounds check sequential leases, a thirty-request burst, and
repeated event loops. A persistent control connection counts new TCP connections
without adding measurement connections. Every round checks that leases and
candidate pools are cleaned up. Thirty requests exceed the twenty foreground
slots, so the admission retry interval can dominate burst timing. These are
lease overhead measurements, not evidence that model responses become faster.

After building the frontend, the browser regression tests can be run with the
existing Playwright installation from the repository root:

```bash
NODE_PATH=/home/mrx/local/github/meta_deepread/e2e/node_modules /home/mrx/local/github/meta_deepread/e2e/node_modules/.bin/playwright test e2e/tests/resource-improvements.spec.ts
```

The test starts and stops its own frontend on a random loopback port. Backend
responses are mocked for these browser checks; the HTTP benchmark above covers
the real backend and PostgreSQL separately. Screenshots are written under
`_scratch/resource-browser`.
