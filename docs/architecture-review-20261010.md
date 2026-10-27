# Architecture reassessment, 2026-10-10

Reviewed the Deepread survey at `/tmp/architecture-review-20261009-021158.html`
against current code and `decision.md`. No completed grilling transcript was
provided. Survey proposals are not an agreed implementation specification.
The earlier `015700` report targets an unrelated point-cloud repository and
does not apply here.

## Findings

1. **Asset membership retirement: implemented, with two additional fixes.**
   `services/asset_lifecycle.py` already hides personal child cleanup, shared
   publication handling, active-job retention, and post-commit file removal.
   Keep ingestion and catalog claiming separate. This release protects queued
   embeddings and initial knowledge-graph extraction from premature collection,
   and extends the collector retry budget beyond configured job execution limits.
   Original boundary commit: `d7c825f`.
2. **Chat turns: implemented. Retrieval consolidation: deferred.**
   `services/chat_turns.py` centralizes authorization, scope, history, and
   persistence. `ChatEngine.answer` already hides retrieval and citations.
   Combining it with conversation storage and SSE transport would widen the
   module rather than simplify its callers. A retrieval change needs parity
   tests and a separate design. Original boundary commit: `4ad3326`.
3. **Unified synthesis engine: deferred. Audio lifecycle: addressed separately.**
   Interactive streaming and batch generation have different cancellation,
   retry, and publication requirements. Do not merge them just to reduce files.
   The current release addresses temporary-audio ownership and cleanup without
   replacing those synthesis paths. Audio validation is a separate release gate.
4. **Document chapters: implemented.**
   `services/document_sections.py` owns discovery and private chapter persistence
   for HTTP handlers and summary workers. No further justified rewrite was found.
   Original boundary commit: `20e6d7f`.
5. **Upload-client consolidation: rejected.**
   `uploadApi.ts` already hides concurrency, cancellation, progress, and polling
   behind a subscription interface that survives modal unmounts. Folding it into
   endpoint wrappers would remove a useful boundary.

PDF rendering cancellation remains a separate investigation, not a demonstrated
defect fixed by this release. Existing reader regressions must pass on the built
application; observer cleanup alone does not prove render-task cancellation.

## Asset fixes and evidence

- Embedding dispatch records `queued`; the worker records `running`. Retain
  assets in either state.
- `kg_builder.py` creates an initial extraction row with the model default
  `pending`, commits, then performs extraction. Existing rows use `extracting`.
  Both states therefore require retention in the current producer flow.
- The collector previously retried for one hour. Eighty retries at 180 seconds
  cover four hours, longer than the configured summary, knowledge-graph, and
  embedding single-run limits. Broker backlogs and repeated job retries can
  still exceed this budget and require operational monitoring.
- Four new asset tests cover queued embeddings, pending and extracting graphs,
  and the retry-budget invariant. The asset, chapter, and chat suites contain
  53 passing tests. Full combined validation is recorded in `decision.md`.
