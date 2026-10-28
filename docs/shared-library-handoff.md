# Shared library implementation handoff

Status: implementation and isolated preview deployment completed on 2026-10-08.

## Preview

The notebook home, shared catalog, and reader run at http://localhost:3013. The preview uses an independent database, Redis, JWT secret, cloned files, and OmniRoute bridge. The existing `opennotebox.qa` account is reused for browser checks; its credentials remain outside the repository at `/home/mrx/.local/state/opennotebox/preview/testing/account.json`.

Uploads are private by default. An eligible uploader can publish a PDF to the signed-in team catalog. People can search by metadata and topic, preview a page, select a whole card, and add the existing asset to their own notebook. Chat, annotations, notes, and reading progress remain private to each membership. Unsharing removes catalog discovery while existing members retain access.

## Architecture and boundaries

- One `Document` stores the PDF, extracted pages, retrieval data, and cover. Unique `LibraryEntry(user_id, document_id)` rows hold notebook placement and personal reading state.
- Future exact-byte uploads use streamed SHA-256 deduplication. Historical duplicate documents have not been merged.
- Membership checks protect document and AI routes. Last-member removal retains assets until memberships and active processing jobs are gone.
- Topics are indexed in PostgreSQL. Catalog pagination, search, facets, and related-notebook suggestions use public metadata only.
- Shared and reused assets cannot be reparsed in place. Versioned reprocessing is not implemented.
- The preview API reaches the configured Gemini model through OmniRoute at host port 8767. The endpoint health check and model listing pass. No paid generation request or concurrent-chat throughput benchmark was run.
- The port-3003 app was not restarted in this deployment. Its database already reported migration `f8d3c6a17b42` when inspected; the timing of that earlier migration is unknown, so it was left untouched.

## Validation

- Backend suite: see `docs/implementation-validation.md` for the final run.
- Disposable PostgreSQL migration upgrade/downgrade/upgrade preserved existing membership state; 12 concurrent adds created exactly one membership.
- Live notebook and shared-library browser checks passed using the persistent QA account.
- Mocked desktop and phone library/notebook flows passed; production frontend image build passed.
- The requested target of 10-20 simultaneous AI chats is not yet proven. Measure provider quotas, first-token latency, end-to-end response time, and worker memory before calling that target accepted.

## Resume

If continuing this work, start with the current code and test results instead of replaying implementation. Remaining work is production deployment review, representative Gemini concurrency measurement, historical duplicate analysis, and an explicit versioned reprocessing workflow. Keep the original port-3003 deployment separate from preview operations.
