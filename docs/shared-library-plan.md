# Shared library design

Status: implemented and deployed at http://localhost:3013/dashboard. This file
records the design and current implementation boundaries. The service is an
isolated team preview, not a production deployment.

## Product defaults

- Every upload starts private. Sharing is an explicit action called Share with team.
- Shared means visible to signed-in users of this installation, not anonymous internet visitors.
- Publish safe metadata: title, author, short description, topics, language, and document type. Never publish annotations, conversations, reading progress, or user-specific AI output.
- The Shared library page supports search, topic and type filters, preview, and Add to my notebook. The whole card selects in selection mode. Preview is a separate action that preserves the picker.
- Show Added for an existing attachment. Adding the same asset again is idempotent.
- Unsharing removes catalog discovery and prevents new additions. Users who already added the book retain access to that version. Explain this when publishing.
- Removing a book removes that user's attachment and personal data, not other users' access. Deleting an account must not delete a book still used by others.

## Separate the asset from its users

The current Document row combines the file, extraction, owner, notebook, and reading progress. Giving other users access to that row would also make its lifetime and personal state ambiguous.

Use the existing document ID as the processed asset identity. Add LibraryEntry with a unique (user_id, document_id) constraint. Move notebook placement, library-added time, and reading progress to the entry. Keep the PDF, extraction, retrieval chunks, embeddings, and cover attached to the asset. Keep chats, annotations, bookmarks, and personalized generated output scoped to the user and their authorized entry.

Do not create a new Document or reparse the PDF when another user adds a shared book. One small membership row is necessary per user; one PDF and one processed representation serve all those memberships. Asset lifetime must no longer depend on a cascading uploader-user foreign key.

An asset is treated as immutable. Exact-byte uploads use a streamed SHA-256 hash and unique database constraint to reuse the existing asset; different editions remain separate. Shared or reused assets cannot be reparsed in place. A full versioned reprocessing workflow is not implemented yet. Private duplicate uploads must not reveal another user's filename or publication state.

Generic cached summaries can be shared only when generated solely from the asset using an explicit deployment-level template and configuration. Summaries using personal prompts or settings remain user-owned. Parsing, embedding, and cover jobs run once per asset/version, with the existing durable jobs and locks.

## Finding useful books

Start with deliberate metadata and fast database search rather than generating recommendations during every page load:

1. Let publishers choose a few topics and a type such as textbook, paper, manual, or report. Display the topics and description on catalog cards.
2. Search title, author, description, and topics. Add indexed full-text search over extracted content as a separate ranking signal.
3. Offer Related to this notebook using its declared topics and the public metadata of its books. Exclude books already added, and show a concrete reason such as Matches distributed systems.
4. Let users correct suggested tags. Add asynchronous tag suggestions and semantic ranking only after measuring whether metadata search misses useful results.

Recommendations must not use private chats or annotations, and may only return assets with an active publication. Avoid popularity-only ranking in a small team, since it tends to bury new or specialist material.

## Resource use

- Keep catalog filtering, ranking, pagination, and counts in PostgreSQL. Index memberships by user and added time, publications by asset, and searchable metadata appropriately.
- Fetch one catalog page and its covers. Do not return every PDF or load whole extracted books into application memory for search.
- Stream uploads, hashing, downloads, and PDF range reads. Use bounded worker concurrency and bounded caches rather than consuming every available CPU or all RAM.
- Reuse the single asset extraction/index. Deduplicate asset IDs before multi-document retrieval, then map citations back to the requesting user's allowed entries.
- Prioritize interactive reading and chat over parsing and embedding. Cache immutable file and cover responses independently of user-specific library state.
- Measure database latency, cache hit rate, worker peak RSS, reader canvas memory, and provider first-token latency with representative simultaneous users. Hardware utilization alone does not prove speed.

## Required access and migration work

Document.user_id ownership checks have been replaced with explicit LibraryEntry membership on file, preview, page, summary, chat, export, TTS, knowledge-graph, note, bookmark, podcast, flashcard, and notebook paths. Memberships are validated before retrieval in both normal and streaming chat. Catalog access grants preview of published content only; it does not grant access to another user's child records.

The migration backfills one entry per existing document without changing document IDs or personal data. Removal is separate from asset garbage collection. Garbage collection removes an unpublished asset only when no memberships or active jobs reference it, using transactional locking. Preview paths are asset-scoped. Reparse is disabled for shared/reused assets until versioned processing is implemented.

Tests must cover two users adding the same asset, one stored PDF and one extraction/index, independent notebook/progress/annotations/chat, idempotent additions under concurrency, private catalog exclusion, unsharing, owner removal, account deletion, asset retention, and denial of guessed IDs. Use the persistent QA account for live browser checks; use isolated database fixtures for multi-user authorization tests.

This is a data-model and authorization change, not just another gallery page. The asset/membership migration and access tests shipped together before the catalog was enabled.

## Validation and remaining limits

- Backend suite: 340 passed after the optional-metadata regression was added. Ruff passed across backend app and tests. The final run is recorded in `docs/implementation-validation.md`.
- Disposable PostgreSQL migration round trip preserved notebook and reading state. Twelve concurrent additions created one membership.
- Live notebook and shared-library browser flows use the same `opennotebox.qa` account and passed on port 3013. Mocked desktop/phone library checks passed.
- The preview OmniRoute bridge now uses port 8767, which the API container can reach. Health is healthy and model discovery reports the configured Gemini model. No model generation request or 10-20 simultaneous-chat throughput test has been run.
- Exact-byte uploads deduplicate future copies; historical duplicates remain separate. Versioned reprocessing, semantic discovery, production deployment hardening, and measured concurrency acceptance remain future work.
