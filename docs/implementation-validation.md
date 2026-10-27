# Implementation validation

The existing application now has an authenticated gallery at `/dashboard` and
addressable readers at `/dashboard/read/[docId]`. The gallery includes cached
PDF covers, recent reading, progress, shelves, search, format filtering,
sorting, pagination, and gallery/list views. Covers render in background
workers, so browsing does not download every PDF.

Chat streams through the existing OmniRoute/Gemini configuration, including
final citations and saved messages. Shared Redis admission bounds interactive
and background calls across processes. Extraction retries replace pages
rather than duplicate them; embedding backfills are durable Celery jobs.

## Checks performed

- Backend suite: 317 tests passed, including tenant isolation, library queries,
  cached previews, extraction retries, streaming, and cancellation.
- Real Redis: admitted 20 foreground and 4 background calls simultaneously,
  rejected the next foreground call, and verified renewal, expiry, and cleanup
  in a unique test key namespace.
- Disposable PostgreSQL 16 with pgvector: upgraded the full migration chain,
  checked legacy duplicate cleanup and BYTEA vector backfill, created the HNSW
  index, ran vector retrieval, and checked advisory lock ownership across ORM
  commits. No existing application database was migrated.
- Production Next.js build and TypeScript checks passed. Six mocked browser
  tests passed against the standalone production server, including desktop and
  phone gallery flows, pagination, search, empty/error states, reader refresh,
  and incremental chat text.
- Runtime frontend dependency audit: zero reported vulnerabilities after
  updating Next.js, Axios, and PostCSS. Development-tool dependencies still
  have registry advisories; the runtime audit excludes them.
- Compose configurations, frozen backend lockfile, and shell syntax checked.
  CI now repeats backend/Redis, frontend build, and mocked browser checks.

## Deployment gates

The live installation was not restarted. Use the backup, storage copy, and
migration instructions in [production deployment](deployment-production.md).
The new frontend and backend must be deployed together.

The Redis test proves application admission behavior. It does not prove Gemini
quota, first-token latency, token throughput, local embedding capacity, or
end-to-end performance with 20 actual AI chats. No paid model calls were made.
Validate those using representative documents and the configured provider
before claiming the requested response speed under concurrent load.

Two pre-existing Ruff failures remain outside this change: unused `pool` in
`backend/app/routers/documents.py` and unused `timedelta` in
`backend/app/tasks/tts_tasks.py`. The production build passes its frontend lint
and type checks. No full remote CI run or production image deployment occurred.

## Separate running preview

A separate preview now runs at http://localhost:3013/dashboard. Its database
was restored from the original installation, then migrated independently.
The original database remains at revision `d2a8e41c70b3`. Real browser checks
verified an authenticated gallery with 15 documents for the selected copied
account, worker-generated PDF covers, PDF rendering, and return navigation.
The temporary verification token file was removed. See [preview operations](preview.md).

## Notebook folders validation

The backend suite passed 324 tests. Nine mocked desktop and phone browser
tests passed, including notebook creation, filing, scoped uploads, renaming,
and deletion with document preservation. The production frontend build and
TypeScript checks passed. The SQLite migration upgrade and downgrade ran
successfully inside the backend image; the local migration regression test
skips because the shared interpreter does not have Alembic installed.

The isolated preview was rebuilt and migrated to `b4e8f2a61c90`. An actual
authenticated browser on port 3013 verified notebook creation, filing an
existing PDF, persistence after refresh, and deletion without document loss.
The temporary notebook was removed and the document returned to Unfiled.
The homepage appearance was checked after transitions settled.

## Notebook interaction regression checks, 2026-10-06

The final backend suite passed 325 tests with one Alembic regression test
skipped in the shared local interpreter. Thirteen mocked desktop/phone
browser tests and the opt-in live notebook check passed. Production frontend
and Docker builds passed, and the preview health endpoint is healthy after
deployment. The live check reuses `deepread.qa`, previews without leaving the
picker, moves two sources from A to B, refreshes the reader, verifies the
sidebar excludes the source notebook's remaining book, and returns to B.

Claude Code with Gemini independently reviewed the notebook interactions.
Related fixes include destination-change selection reset, source notebook
filtering, preserving multi-page selections, disabling already-filed sources,
and unavailable-notebook routes. Sharing remains a proposal in
[shared-library design](shared-library-plan.md), not an enabled preview feature.

## Shared library validation, 2026-10-08

The shared-library implementation and notebook flows are deployed to the
isolated preview at http://localhost:3013. The full backend suite passed 340
tests. Ruff passed across `backend/app` and `backend/tests`. A follow-up focused
notes and TTS run passed 37 tests after unused imports were removed. The only
test warning is SQLAlchemy skipping reflection of the SQLite expression index
`uq_notebooks_user_name_ci` in the notebook migration test.

The final backend and frontend Docker images built successfully. Twelve
mocked desktop/phone library and notebook browser tests passed. Live notebook
filing and shared publication, catalog search, authenticated PDF preview, and
unsharing passed with the persistent `deepread.qa` account. Its fixture was
left unpublished after the check.

The disposable PostgreSQL validation upgraded, downgraded, and upgraded the
shared-library schema while preserving personal notebook and progress state.
Twelve concurrent add requests created exactly one membership. The clone was
dropped afterward. A mode-600 database backup was made before the preview
migration at
`/home/mrx/.local/state/deepread/preview/testing/database-pre-deploy-20261008.dump`.

The preview uses host bridge port 8767 for OmniRoute because the API container
could not reach the previous endpoint. From the API container, `/v1/models`
and the authenticated health probe now succeed; the configured Gemini model
is listed. No model generation request was sent. This does not measure Gemini
latency under load or prove the target of 10-20 simultaneous chats.

This deployment used only `docker-compose.preview.yml`; the original app on
port 3003 was not recreated and its health endpoint still returns 200. When
checked, its database already reported `f8d3c6a17b42`; the timing of that
revision change is unknown, so no operation was run against that database.

## Shared-library implementation review, 2026-10-08

Two independent Luna xhigh reviews covered backend authorization/lifetime and
frontend selection/navigation. The review reproduced one backend failure in a
real API/database flow: failing a private whole-book summary changed shared
asset readiness to failed, hiding the book from the catalog. Two assertions
failed before the fix and passed after it. Personal summary jobs now use only
private markers; existing legacy asset status is recovered on completion.
Queue failure preserves readiness, and unprocessed books cannot queue summaries.

A live browser check visited both QA books through the reader sidebar, opened
the sharing form, and navigated back to the cached other book. The dialog
closed, so the suspected stale-form navigation issue was not reproduced and
no frontend change was made for it.

Follow-up inspection items, not proven failures in this review: individual
reader page components do not explicitly cancel render tasks on unmount, and
the collector retry window is shorter than the longest whole-book job. The
private-marker fix removes whole-book jobs' dependency on global readiness;
legacy jobs and other processing stages still merit a longer-running cleanup
check. The target of 10-20 simultaneous Gemini chats remains unmeasured.

Validation after the review fix: full backend suite 341 passed, eight focused
asset/summary tests passed (including two added after full-suite collection),
full backend Ruff and `git diff --check` passed. Backend Docker image rebuilt
and deployed to the isolated preview. Both live notebook and shared-library
flows passed after deployment; port 3013 health is healthy. Original port 3003
was not recreated and remains healthy. No frontend code changed in this review.

## Pull request validation, 2026-10-09

The final source tree passed 343 backend tests, full backend Ruff, and staged
whitespace checks. The same SQLite expression-index reflection warning remains.
The frontend Docker production build passed. All 18 mocked browser checks passed
against a temporary frontend container on loopback port 3102, covering streaming
chat, library, notebooks, shared-library selection/preview/publication and phone
layouts. The temporary container was stopped afterward. Desktop and phone
shared-library suites are now included in CI. Preview port 3013 remains healthy.
QA credentials and runtime data remain outside the staged files.
