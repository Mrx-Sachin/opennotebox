# Technical decisions

## 2026-10-05: team deployment and library home

- Improve the existing Next.js/FastAPI application. Preserve existing user edits and reader functionality.
- Target a private 100-member team, including 10-20 simultaneous AI chats plus reading and uploads. Provider capacity and response times must be measured rather than inferred from account count.
- Route generative AI through the configured Gemini endpoint in OmniRoute. Store documents locally and keep retrieval, embeddings, and narration local. Retrieved text sent for Gemini inference leaves the host.
- Make `/dashboard` the authenticated library home and `/dashboard/read/[docId]` a directly addressable reader. Keep upload and document actions available in both contexts.
- Use server-side search, filtering, sorting, and pagination for the gallery. Load recent-reading metadata separately from its current page of results.
- Reuse TanStack Query for document data and existing Zustand state for reader controls. Keep the existing authenticated Axios client and its refresh handling.
- Render low-resolution PDF covers in background workers, cache them on disk, and load only visible covers. Do not download full PDFs to render gallery covers in the browser.
- Preserve the established reader typography and palette. Use document covers, reading progress, and a recent-reading shelf to distinguish the library from the reader.
- Stream chat content end to end, retaining final citations and persisted messages. Keep the existing non-streaming API compatible.
- Make extraction replay-safe and move embedding backfill from API daemon threads to durable jobs. Support a safe migration path for existing databases.
- Add configurable shared inference admission and reserve interactive capacity. Production images must be reproducible and migration readiness must fail closed.
- Validate with isolated browser fixtures and hermetic backend tests. Do not call paid models, load-test the user's running installation, or recreate its database.

## 2026-10-05: verification and deployment boundaries

- Add CI for locked backend tests, Redis admission, the production frontend build, and mocked gallery/reader/chat browser checks. CI installs Python dependencies into its runner environment without creating a virtual environment.
- Verify the selected 20 foreground plus 4 background admission limits against Redis without sending model requests. This proves admission behavior only; provider quotas, first-token latency, and sustained Gemini throughput require a separate representative load run.
- Keep live application services and existing database untouched during implementation. Validate the schema migration on a disposable PostgreSQL instance.
- Record existing unrelated Ruff failures rather than silently changing them: unused `pool` in the URL-upload transport and unused `timedelta` in TTS tasks.

## 2026-10-05: frontend dependency audit

- Upgrade Next.js from 14.2.35 to 15.5.24 and align its ESLint package after the registry audit identified runtime advisories. Retain the existing React 18 reader implementation, which is accepted by this Next.js release's declared peer dependencies.
- Update Axios to 1.20.0. Override transitive PostCSS with the same patched 8.5 release used by the application build, then validate CSS output, build, browser flows, and the runtime dependency audit.
- Keep the Next.js upgrade bounded to the compatible major rather than introduce a broader React and reader migration alongside the gallery.

## 2026-10-05: final validation

- The final standalone Next.js 15.5.24 build passed all six mocked desktop/phone browser checks. The backend suite passed 317 tests, and focused processing checks passed after lock failure cleanup was hardened.
- PostgreSQL/pgvector migration tests used a disposable instance which was removed. Redis tests used unique test keys and made no provider requests.
- Test preview servers were stopped after verification. Existing services, stored documents, and production database were not restarted or migrated. Deployment and remaining performance gates are recorded in `docs/implementation-validation.md`.

## 2026-10-05: separate running preview

- The user requested a different port. Run the new application at localhost:3013 using the `deepread-preview` Compose project. Keep the original localhost:3003 installation running.
- Clone accounts, documents, and artifacts into an independent PostgreSQL/pgvector database and private host storage under `/home/mrx/.local/state/deepread/preview`. Apply the schema migration only to this clone. Use a distinct JWT secret and Redis instance.
- Start an independent streaming OmniRoute bridge on port 8767, inside the host firewall's existing Docker-accessible bridge-port range. Reuse the existing TTS service through a dedicated shared Docker network so a second GPU model is not loaded.
- Install locked backend dependencies into the container system interpreter using frozen export and uv pip. Do not create a new Python virtual environment.

- Preview deployment completed. Both original and preview login routes return HTTP 200, preview health is healthy, and the original schema revision remains unchanged. A real authenticated browser verified copied documents, worker-generated covers, PDF reader rendering, and gallery navigation without calling Gemini.

## 2026-10-05: notebook folders

- Make the authenticated homepage a notebook shelf, with a separate All documents view. A notebook groups related PDFs and other sources around a user-chosen topic or project.
- Keep one optional notebook assignment per document. Use an Unfiled area for existing and unassigned documents, and preserve their files, notes, and progress when a notebook is deleted.
- Add owned notebook CRUD, atomic bulk filing, scoped library metadata, and notebook-aware uploads. Store filing metadata in PostgreSQL; physical files stay at their existing paths.
- Capture the destination notebook in each upload-queue item, so changing routes while uploads run does not change their destination.
- Deploy and migrate only the isolated localhost:3013 preview. Keep the original installation and database unchanged.

- Notebook validation completed: 324 backend tests and nine mocked desktop/phone browser checks passed. SQLite upgrade/downgrade preserved existing documents, and the isolated PostgreSQL preview migrated to `b4e8f2a61c90`. A real browser verified create, file, refresh, and delete with document preservation on port 3013. Grouping remains manual; no paid AI classification requests were made.

## 2026-10-06: notebook interaction fixes and shared-library proposal

- Reproduce selection navigation against port 3013 with one persistent non-admin QA account. Store its generated credentials outside the repository in a mode-600 state file; reuse the account and small fixtures for subsequent live checks.
- Use whole-card selection in the existing-document picker and after selection starts. Keep explicit previews in a separate tab so selection remains intact. Add a source-notebook filter.
- Derive the reader scope from the current owned document's notebook assignment. Scope the reader list on the server, return to that notebook, and file reader uploads there. Preserve scoped shelf filters and reject missing or malformed notebook routes.
- Preserve selections across pages; merge select-page actions, reset selections when the destination changes, and disable documents already in the destination. Exclude the current notebook from the move dialog.
- Claude Code with Gemini completed a read-only interaction review. The automatic fleet merge workflow was not used because the workspace contains ongoing uncommitted work.
- Record the user's shared-library idea in docs/shared-library-plan.md. Default to private uploads and explicit sharing with signed-in team members. Separate immutable processed assets from user library entries before implementing catalog access, so files and indexes are reused while personal data stays isolated.

- Final notebook regression validation passed: 325 backend tests with one local Alembic skip, 13 mocked browser tests, and the live persistent-account flow on port 3013. Rebuilt preview images and deployed the fixes; original port 3003 remains unchanged.

## 2026-10-08: shared-library implementation

- The user authorized implementation of the reviewed shared-library design and use of subagents. Preserve all existing working-tree changes and deploy only the isolated port-3013 preview.
- Use immutable Document assets plus unique user/document LibraryEntry memberships. Membership carries notebook, progress, private title/source URL, and verified-upload publication eligibility. Initial upload and exact-byte upload reuse grant publication eligibility; adding from the catalog does not.
- A single active publication is controlled by its publishing user, independently of the original uploader. Duplicate private uploads reuse bytes without revealing another user's filename or URL. Shared-book additions create memberships, not physical copies or parse jobs.
- Authorize personal document features through memberships and user-owned child rows. Personalized summaries stay private. Shared/reused assets cannot be reparsed in place; revised PDF bytes create a new asset/version.
- Begin discovery with topic/type filters and explainable metadata matches. No private chat/annotation-based recommendations and no AI call on catalog browsing.

## 2026-10-08: user-requested pause

- Pause implementation to conserve the user's token budget. Interrupt all three shared-library workers and preserve the working tree. No sharing migration or deployment has run.
- Save complete scope, design, partial implementation, known blockers, validation and resumption steps in `docs/shared-library-handoff.md`. The PDF.js shared preview rewrite is interrupted and its component currently missing; finish it before any build or deployment.

## 2026-10-08: shared-library completion and preview validation

- Finish the PDF.js preview with one bounded canvas page, range transport, cancellation, and PDF cleanup. The shared catalog uses a separate preview action so selecting a result never navigates away.
- Track each whole-book summary job with a private durable marker. Acquire locks in asset, membership, then personal-job order so removal and summary completion cannot deadlock; asset collection waits for active jobs.
- Normalize blank optional author, description, and language values to null so the publishing form can submit empty optional fields. Keep title and document type nonblank.
- Set the isolated preview OmniRoute bridge to host port 8767 because host port 9876 was unreachable from the API container. Update the preview backend registry while preserving model credentials and model ID. `/v1/models` and the health probe pass; Gemini appears in discovery. No generation request was sent.
- Deploy only `docker-compose.preview.yml` on port 3013. Preview migration head is `f8d3c6a17b42`; create a mode-600 database backup before the migration.
- Validate with 340 backend tests, passing full Ruff, 37 focused notes/TTS tests, 12 mocked browser checks, two live flows with the persistent QA account, and a disposable PostgreSQL round trip with 12 concurrent idempotent additions.
- The performance target of 10-20 simultaneous chats remains unmeasured. The original port-3003 services were not recreated during this deployment and remain healthy. Its database already reported `f8d3c6a17b42` at inspection; do not run operations against it.

## 2026-10-08: implementation review

- Independently review shared-library authorization and frontend interactions using Luna at xhigh, as requested. Reuse the existing QA account and operate only on port 3013.
- Reproduce a private whole-book summary failure through the real API/database workflow: it incorrectly marks the reusable asset failed and removes it from the shared catalog. Keep private job state in Summary markers; never change asset readiness when queuing, retrying, or failing personal summaries.
- Recover legacy summarizing_pages asset status to completed when all whole-book markers finish, independently of individual-page jobs. Reject whole-book summarization before text extraction. Preserve private failure status when queue dispatch fails.
- Live cached-reader back navigation did not reproduce the suspected persistent sharing dialog. Do not change frontend navigation based on that suspicion.
- Record PDF rendering cancellation and collector retry-window concerns as follow-up inspection items. They were identified by static review, not reproduced in browser or long-running worker tests during this review.
- Review fix validation: 341 full backend tests and eight focused asset/summary tests passed, Ruff and diff checks passed. Rebuild and redeploy only preview API/workers, then pass both live persistent-account flows. Both ports 3013 and 3003 report healthy; original services were not recreated.

## 2026-10-09: pull request preparation

- The user authorized opening a PR after the final check. Include the accumulated notebook/shared-library implementation and supporting reliability, deployment, and validation work on `feat/team-notebooks-shared-library`.
- Add desktop and phone shared-library browser suites to CI. Preserve the documented cleanup follow-ups and unmeasured 10-20 concurrent Gemini-chat target; do not describe the PR as proving those targets.
- Final PR gate passed: 343 backend tests, Ruff, staged whitespace checks, frontend Docker build, and all 18 mocked desktop/phone browser checks. Stop the temporary port-3102 test container after validation; preview port 3013 remains healthy.
