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

- The user requested a different port. Run the new application at localhost:3013 using the `opennotebox-preview` Compose project. Keep the original localhost:3003 installation running.
- Clone accounts, documents, and artifacts into an independent PostgreSQL/pgvector database and private host storage under `/home/mrx/.local/state/opennotebox/preview`. Apply the schema migration only to this clone. Use a distinct JWT secret and Redis instance.
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

## 2026-10-09: architecture survey assessment

- The user authorized selective architecture improvements on a new branch based on `feat/team-notebooks-shared-library`, parallel Gemini 3.8 Flash workers, and separate commits for each section. Work on `refactor/deepen-domain-boundaries`; keep implementation workers in separate worktrees.
- No completed grilling conversation or saved grilling decision was supplied or found in the repository. Treat the earlier survey as proposals to assess, not as an agreed refactoring specification.
- Correct the survey's factual overstatement: `asset_lifecycle.py` contains 43 lines and three substantive helpers, not three five-line functions. A short file, a large file, or a circular import alone does not establish a useful refactor. The survey's promised elimination of races and line-count reductions were not demonstrated.
- Focus asset work on retiring one user's membership. Centralize child cleanup, publication ownership, active-job retention, and post-commit cleanup behind that operation. Preserve the active-job snapshot taken before deleting personal summary markers. Keep upload ingestion and catalog batch claims separate; combining these distinct operations into one large lifecycle service would broaden its responsibilities without a demonstrated benefit.
- Focus chat work on the common turn lifecycle used by JSON and SSE endpoints: access checks, scope selection, user-message persistence, history loading, and assistant persistence. Keep HTTP streaming, bounded token queues, keep-alives, and cancellation in the transport layer. Preserve the existing retrieval algorithms and model interfaces.
- Put chapter discovery/backfill and private chapter persistence in `services/document_sections.py`. Both HTTP handlers and summary workers use the service instead of a router importing persistence from a Celery task module. Preserve commit boundaries, metadata-only copying between members, generated content, and the current synchronous discovery behavior.
- Defer the proposed all-in-one RAG pipeline. `ChatEngine.answer` already hides query rewriting, retrieval, reranking, prompt construction, and citations behind a small interface. Its coupling to hybrid retrieval merits a separate focused design with retrieval parity tests, rather than merging conversation storage, SSE, and retrieval during this change.
- Defer speech synthesis consolidation. Interactive synthesis and batch synthesis have different cancellation, retry, and file-publication requirements. A common engine needs tests for interrupted streams, duplicate requests, and concurrent cache eviction before that boundary can safely change. Small shared audio helpers alone would not deliver the proposed architectural improvement.
- Reject replacing the frontend upload queue merely to unify endpoint wrappers. The queue already hides concurrency, cancellation, progress, and processing polling behind a small subscription API, and survives modal unmounts. Preserve this useful boundary.
- Validate with the existing hermetic HTTP/backend suite and new invariant tests. Use the shared Python environment; do not create environments, call model providers, deploy services, or migrate running databases during this architecture work.
- Independent Gemini review found no chapter refactor regression or changed commit semantics. Add coverage for background discovery replay and on-demand chapter dispatch. The reviewer also identified existing legacy-outline copying behavior: imported members may initially receive fallback or duplicate-title stubs from the uploader. Preserve that behavior here; any correction needs an endpoint reproduction and an explicit chapter identity rule, since equal titles do not necessarily mean equal chapters.
- Integrate the asset and chat workers after reviewing their patches. Keep the child-cleanup and history helpers private, omit an unnecessary configurable history limit and compatibility alias, and leave SSE serialization in the router. Add a regression proving that retiring membership retains the asset when the running private summary marker is itself deleted during cleanup. Prefer endpoint history parity coverage over a redundant test of the private history helper.
- Final combined validation passed: 359 backend tests versus 343 at baseline, full Ruff, and whitespace checks. The existing SQLite expression-index reflection warning appears at baseline and after integration; it is unchanged. Tests used `/home/mrx/.local/venv/bin/python` running Python 3.14.4, not a new environment. No supported-version container or browser run was added because this change alters only backend boundaries and preserves the HTTP contract; these results do not establish production concurrency or model throughput.

## 2026-10-09: architecture branch preview startup

- The user requested starting the application for manual testing. Rebuild the preview backend from `refactor/deepen-domain-boundaries` and recreate only its API, default worker, and TTS worker. Explicitly select the `opennotebox-preview` Compose project and its private environment file so the repository's original project settings cannot redirect these operations.
- Keep the existing preview frontend, gateway, model bridge, PostgreSQL, Redis, and shared TTS engine running. The frontend source and schema have not changed in this branch.
- Both port 3013 and the original port 3003 return healthy. The preview API's deployed chat, chapter, and asset service checksums match this branch, and its login page loads in the collaborative browser. Use `http://localhost:3013/dashboard` with the existing preview account credentials for manual testing.

## 2026-10-09: per-book reading position and podcast gallery

- The user confirmed that each book must reopen at its own last viewed page after switching books or refreshing. UI reproduction on preview port 3013 showed page 2 resetting to page 1 when switching books before the progress debounce elapsed.
- Capture outgoing progress synchronously from reader-store transitions, update account-scoped document caches immediately, and flush captured progress on navigation and page hide. Keep normal saves debounced and serialize same-document requests. Use authenticated fetch keepalive for progress writes.
- Remount viewers per document and prevent PDF viewport observation from overwriting progress before restoration completes. Mark restoration complete only after the delayed scroll actually runs.
- Initial implementation passes frontend TypeScript and ESLint checks. Browser regression coverage and independent Gemini verification remain required before declaring this fix complete.
- The user also confirmed a main-gallery section showing podcasts across all books, with in-place playback and source-book links. Reuse existing playback and access-control boundaries rather than introduce another audio engine. Delegate implementation and UI verification to Gemini 3.8 Flash in isolated worktrees, separately from reader verification.

## 2026-10-09: integrated Gemini delegation replaces Astra

- The user explicitly requested stopping the two Astra-managed workers and uninstalling the shared Astra skill and manager, while preserving their worktrees and reports. Stop those worker process groups and their supervising tmux sessions before removing the skill. Preserve all task artifacts in local state.
- Integrated Gemini 3.8 Flash agents are now exposed by the harness. Restart reader regression work and podcast implementation through those tools with disjoint file ownership. Assign UI testing to Gemini as requested. Do not use Astra for new work.
- Both integrated agents work on the existing branch. Share one development server on port 3114 rather than running competing Next servers against the same build directory. Preserve the original and preview deployments until combined validation is complete.
- On resuming implementation, the unchanged backend baseline passes all 359 tests in 95.85 seconds and full Ruff checks. The sole SQLite expression-index reflection warning is unchanged. The preview services remain running; these baseline checks do not validate the pending podcast feature or reader browser regressions.

## 2026-10-09: podcast listing and durable pending progress

- Extend the existing podcast listing with optional document scope. All-book listing joins the requesting user's podcasts to their current library memberships and resolves source names through visible_document_title. Preserve the document-specific response fields and access checks. Do not expose another member's filename or podcasts, issue per-book requests, or add an audio engine or schema migration.
- Seven new hermetic podcast tests pass, covering original document-specific behavior, authentication, missing documents, empty and ordered all-book listings, safe title precedence, ownership isolation, and membership revocation. The preview backend image builds successfully; it is not yet deployed.
- Keep pending reader progress in account-scoped local storage until its exact revision saves successfully. Apply unsaved progress before viewer restoration and retain ordered network writes. Bypassing the queue on page hide can allow an older request to overwrite the latest page, so do not use that shortcut. Local storage failures remain non-blocking, and queued writes must verify that their original account is still active.
- A temporary Node harness running the actual progress hook and reader store passes outgoing capture, pending restoration, optimistic cache updates, request ordering, revision-specific clearing, and account-switch isolation. This is a state-machine check, not a browser test; Gemini browser regression checks remain required.

## 2026-10-09: reader and podcast validation and preview deployment

- Both integrated Gemini 3.8 Flash agents completed implementation and UI verification. The reader suite passes seven regressions, including rapid book switching, same-book clicks, confirmed saves with cached sidebar metadata, refresh while an older save is in flight, deliberate page-one navigation, failed saves, and account isolation.
- The podcast suite passes eleven regressions covering listing and status badges, empty/error states, actual WAV playback and controls, delayed-response switching, navigation cleanup, failure notices, source-book navigation, and 375px layouts. Five existing library checks and twelve notebook/shared-library desktop and phone checks also pass. These comprise 35 distinct browser tests across the combined and related suites.
- Full backend validation passes 366 tests in 96.48 seconds with the unchanged SQLite expression-index reflection warning. Ruff, TypeScript, whitespace checks, and frontend lint pass. Correct the new playback-cleanup ref warning found during the first production build; the final frontend Docker build completes without that warning.
- Recreate only the preview API and frontend through the explicitly selected opennotebox-preview Compose project. Both preview port 3013 and original port 3003 remain healthy. The deployed podcast router checksum matches the source; authenticated all-book listing returns an empty list for the QA account, and document-specific response fields remain unchanged.
- Gemini verifies the built application through the native browser on port 3013. The reader retains page two after rapid book switching, same-book selection, and reload, with a rendered page-two canvas and no application console errors. The podcast section, dedicated navigation, desktop layout, and 375px empty-state layout are verified live. Because the QA account has no generated podcasts, actual playback is proven by the hermetic WAV browser tests rather than live generated audio. Do not generate podcasts, call application model providers, or fabricate database episodes just for this smoke test.
- Keep the preview at http://localhost:3013/dashboard running for the user. Retire the temporary port-3114 development server and close the completed agents. Preserve the discontinued Astra task artifacts and worktrees outside the repository.

## 2026-10-10: isolated audio and public deployment release

- The user confirmed podcast deletion, immediate temporary reading-audio cleanup after playback, evidence-supported remaining architecture corrections, public deployment hardening, and a backed-up update of port 3003. Preserve active readers, generated podcasts, accounts, documents, and production storage.
- Work in the isolated fix/podcast-audio-public-hardening worktree based on 99045a4. Production currently mounts the original repository source, so never edit that source before release validation. Integrated Gemini 3.8 Flash workers own disjoint feature, audio, security, and architecture work; Gemini owns browser verification.
- Deploy immutable local backend and frontend images through an explicit existing-install overlay. Preserve the open-notebox project, PostgreSQL 16 image and volume, Redis and TTS volumes, and existing data/export host paths. Do not use the generic production overlay to silently substitute empty storage.
- Validate the preliminary and final PostgreSQL backups with a separate restore database. Stop application writers before the final database and file backup. Keep private environment snapshots and rollback references outside the repository with restrictive permissions. Never remove persistent volumes during deployment.

- Reassess all five Deepread survey cards against their actual callers and producers. Keep the implemented asset, chapter, and chat-turn boundaries. Defer monolithic retrieval and synthesis rewrites and reject upload-queue consolidation; no completed grilling transcript was provided. Record the evidence and limitations in docs/architecture-review-20261010.md.
- Protect queued embedding backfills and initial pending knowledge-graph extraction during asset collection. The graph producer currently performs its first extraction while the persisted status remains pending. Extend the collector's bounded retry budget to four hours, exceeding configured single-run task limits but not guaranteeing cleanup after arbitrary broker delays. Parent validation passes 53 asset, chapter, and chat tests and scoped Ruff checks.

- Fresh npm runtime and OSV Python-distribution scans found advisories not present in the earlier release assessment. Patch Next.js to 15.5.27 and source-map-js to 1.2.2; the new npm runtime audit reports zero advisories. Constrain vulnerable Python transitive dependencies to verified patched compatible versions and update image setuptools to 83.0.0. The rebuilt Python image passes 69 focused tests. Remaining unpatched python-jose and ecdsa advisories concern public-key confusion/ECDSA operations; current authentication fixes HS256 and uses a symmetric secret, with explicit negative algorithm/key tests. Do not invent a crypto migration without an affected application capability.
- Verify upstream Traefik release and advisory metadata through GitHub. Upgrade the application proxy to 3.7.14, pin its published image digest for this release, and validate routing before production. Keep PostgreSQL, Redis, and TTS infrastructure unchanged. Public access uses the existing Cloudflare tunnel on loopback 3003; narrow direct HTTPS publishing to loopback and inject only the actual host bridge gateway into a private static proxy snapshot. Do not hardcode that installation's Docker subnet in the repository configuration.

- Reject unauthenticated multipart uploads before body buffering and enforce bounded streamed requests. The Gemini worker reproduced 3,145,913 bytes being consumed before authentication in the previous implementation. Parent validation passes 53 upload-guard, security-hardening, and podcast-deletion tests without new warnings. Revoked accounts with still-valid tokens remain subject to the bounded body cap before database authentication.
- Serialize administrator removal through a transaction advisory lock before row locks, refresh identity-map values after acquiring the lock, and reject removal of the last administrator. Independent concurrent PostgreSQL demotion/deletion checks retain one administrator and return one success and one rejection. The proof used a disposable restored database, not production mutations.

- Podcast deletion uses owner and current-membership checks, a locked episode row, and post-commit file cleanup restricted to the podcast directory. Worker final publication and failure cleanup share that row lock. Duplicate tasks preserve completed episodes, and deletion during synthesis cannot leave a late published WAV. Delayed UI deletion updates the originating account cache without disturbing a different account, reader route, or another active episode. Gemini validation passes 27 backend tests with warnings treated as errors and 20 desktop/mobile browser checks on the patched Next.js development server. Built-preview validation remains a separate release gate.

- Ephemeral reading audio enforces explicit session ownership, hash-based reference counts in Redis, and atomic release via Lua. Streaming response lifetime guarantees paired producer/reader counter cleanup even on unstarted generators. Unloading the browser or switching documents immediately issues an authenticated release. Stored summaries and podcasts are preserved and excluded from ephemeral sweep. Independent verification covers 25 unit/race tests, 2 dedicated Redis tests, and 7 Playwright lifecycle browser tests.

- Create missing `document_topics` table and shared catalog index via Alembic migration `2c8a9f4e1b3d` with safe reflection checks. This fixes the production PostgreSQL failure (`relation "document_topics" does not exist`) when updating shared document listings and synchronizing topic tags.
- Harden the team sharing dialog layout in `ShareDocumentDialog.tsx` and `library.css`. Prevent nested scroll container collapse that clipped Title and Author fields on tablet viewports, ensure visible labels with descriptive placeholders, and correct margin spacing for custom document types.
- Decouple podcast playback into an independent entity with full-screen studio mode. `PodcastPlayerModal.tsx` provides an animated audio waveform visualizer with Host A / Host B avatars and live dialogue transcript navigation with click-to-seek. Direct "Go to book page" links preserve source document reader context while allowing standalone podcast browsing, playback, and deletion.

- Fix unscoped general conversation export failure in `export_service.py`. When a conversation has no associated document IDs, `doc` is `None` representing the `_unfiled` export directory. Safely evaluate document titles to avoid `AttributeError: NoneType object has no attribute id` during automated user data export sync.

## 2026-10-11: podcast playback across dashboard navigation

- The user requires podcast playback and its bottom controls to survive navigation into PDFs, shared library, and other dashboard sections until explicit stop or another audio selection. Keep playback metadata and controls outside the gallery route lifecycle, reuse the singleton TTS engine, and preserve immediate temporary reading-audio cleanup.
- Use the existing release worktree and branch. Integrated Gemini 3.8 Flash workers own playback implementation and navigation/browser regression checks with disjoint file ownership. Preserve a frontend rollback image and deploy only the validated frontend service to the existing port 3003 stack; no database migration or data changes are required.
- Store the active episode and expanded transcript in the existing reader store and render their controls in the stable dashboard layout. Gallery unmounts only retire local UI callbacks. Document and library navigation stop temporary narration only when no podcast is active. Starting narration, summary playback, another episode, logout, or confirmed deletion of the active episode owns the corresponding playback transition.
- Clear stale direct-audio identity and release its browser object URL when narration takes over or playback stops. Preserve generation checks around asynchronous loading and playback, without allowing an obsolete request to pause the singleton audio element used by a newer request. Closing the studio or following its source-book links closes the transcript without stopping the episode.
- Reserve the measured player height using ResizeObserver so reader panel buttons and library card actions remain reachable on phones. Gemini verifies zero overlap at 375x667, 37.11px clearance above shared-library actions, and no horizontal overflow on the compiled candidate.
- The expanded studio owns its playback keys in the capture phase. Merely checking expanded state in the reader shortcut handler is insufficient: the studio can close first and let the same Escape event stop playback in a later listener. The regression suite reproduced that ordering failure before the capture fix.
- Final Gemini browser validation passes all 40 podcast and reading-audio lifecycle checks. Parent review confirms the actual suite output, clean lint and TypeScript checks, whitespace checks, and a successful immutable frontend build. Preserve production backend and database containers while updating only the frontend image, with the previous frontend image and environment retained for rollback.
- Deploy frontend image `7c1980f672a9c0a5e7219e009fbbad834bf17423c5990c35325de5c1786fae9d` through the existing release overlay. Port 3003 and the public login endpoint return HTTP 200. Backend and PostgreSQL container identities remain unchanged. Gemini's six targeted playback, navigation, keyboard, and phone regression checks also pass against the deployed port 3003 using mocked API fixtures, without production data mutations.

## 2026-10-11: continuous book narration

- Keep the singleton narration engine alive across dashboard navigation. Its source document and page bounds belong to playback, not the currently displayed PDF. Explicit stop, audio takeover, logout, and completed source playback still release temporary audio.
- Store source metadata and the existing parsed narration sentences for a dashboard-level bottom player and a text-focused Now reading view. Reuse extracted text without additional OCR or generation. Closing this view leaves playback running; Open in book explicitly selects the currently narrated source page.
- Last viewed page remains a viewing preference. Background narration must not overwrite another book's page or saved progress. Automatic PDF following and sentence highlighting apply only when the displayed document matches the narration source. Selecting PDF text alone does not stop playback.
- Integrated Gemini 3.8 Flash workers own source-bound playback, the reading UI, and browser reproduction/regressions with disjoint files. Preserve the current release worktree and production data, validate the compiled frontend, and deploy only the frontend with its previous image retained for rollback.

## 2026-10-28: Fix Claude Code 400 "tool use concurrency issues" on skill invocation

- Symptom: Claude Code session `0e137835-3d8a-43bb-b267-e432155a9791` failed with `API Error: 400 due to tool use concurrency issues. Run /rewind to recover the conversation.` whenever invoking `Skill(artifact-design)` or other tools where skill instructions or user text follow the tool result.
- Root Cause:
  1. In Claude Code, executing a skill generates a user turn with both `tool_result` ("Launching skill: artifact-design") and injected `text` ("## Page contract...").
  2. The local model-router (:20130) forwards Anthropic Messages requests to OmniRoute (:20128).
  3. When OmniRoute translated the request via `open-sse/translator/request/openai-to-gemini/helpers.ts`, `mergeConsecutiveSameRoleContents` invoked `moveFunctionResponsesLast([...last.parts, ...entry.parts])`.
  4. `moveFunctionResponsesLast` erroneously partitioned the parts and placed `others` (text) before `responses` (`functionResponse`), resulting in `[text, functionResponse]`.
  5. When Google Cloud Code / Antigravity forwarded this Gemini `contents` representation to Vertex AI Claude Opus, Vertex AI received the `text` block before the `tool_result` block.
  6. Anthropic Vertex AI strictly requires each `tool_use` to be followed immediately by its corresponding `tool_result` before any user text; otherwise it rejects with HTTP 400 `messages.<idx>: tool_use ids were found without tool_result blocks immediately after: <id>`.
  7. Claude Code caught this specific 400 message in `AXt(e)` and displayed the synthetic error message: `API Error: 400 due to tool use concurrency issues. Run /rewind to recover the conversation.`
- Fix:
  - In `open-sse/translator/request/openai-to-gemini/helpers.ts` (and corresponding runtime chunks in `/home/mrx/.nvm/versions/node/v22.23.1/lib/node_modules/omniroute/dist/.build/next/server/chunks/`), updated `moveFunctionResponsesLast` to place `responses` first: `return [...responses, ...others]`.
  - Restarted `omniroute.service` via systemd.
  - Verified with live E2E test cases on both Vertex Claude Opus and Gemini Flash: both succeed with HTTP 200 and valid tool call generation.
  - Replayed the exact 231-message request payload that previously failed with 400; verified it now completes with HTTP 200.
