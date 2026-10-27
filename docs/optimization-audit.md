# Optimization Audit — OpenNoteBox

## Context

The application is a self-hosted AI document reader (PDF summarization, chat, podcasts, flashcards, TTS). Three parallel audits covered backend compute/scaling, prompt/token efficiency, and frontend performance/reliability. This document consolidates all findings ordered by impact.

---

## 1. Token Usage & Cost Reduction (Highest ROI)

### 1.1 Page summary sends 40,000 chars per page — most is waste
- **Files:** `backend/app/services/summarizer.py:80,115`
- **Problem:** `content=page_content[:40000]` sends up to ~10,000 tokens per page. A typical page has 2,000-5,000 meaningful chars. The rest is whitespace, headers, footers. For a 300-page book this wastes ~1.875M input tokens.
- **Fix:** Reduce to `[:15000]`. Still provides 3,750+ tokens of context.

### 1.2 Section summary re-reads raw text instead of reusing page summaries
- **File:** `backend/app/tasks/summary_tasks.py:314-321`
- **Problem:** Concatenates raw `ExtractedText` for the whole section. If page summaries already exist, they are 3-5x smaller and carry the same signal.
- **Fix:** Query for existing page summaries first. If >=80% of pages have summaries, use those. Fall back to raw text otherwise.

### 1.3 Duplicate/conflicting system prompts in summarizer
- **Files:** `summarizer.py:74-77,109-112` (system messages) + `prompts/summary/page.jinja` + `prompts.py:27-41` (defaults)
- **Problem:** Three different personas for the same task. The system message says "brilliant teacher", the template says "expert document analyst", and the default says "encyclopedic teacher". The model gets conflicting signals. The system messages add ~35 redundant tokens per call.
- **Fix:** Remove system messages from summarizer.py. Put all persona and formatting instructions in a single, unified Jinja template.

### 1.4 On-disk templates vs hardcoded defaults are inconsistent
- **Files:** `prompts/*.jinja` vs `backend/app/services/prompts.py:25-161`
- **Problem:** On-disk `summary/page.jinja` hardcodes "under 300 words" and does NOT have a `{{ max_words }}` variable. The `render_template()` call passes `max_words=450` which is silently ignored. On-disk flashcards only produces "basic" cards; the default supports "cloze" cards too. On-disk podcast has no multi-segment support.
- **Fix:** Either update on-disk templates to match defaults (recommended) or delete on-disk templates and use only defaults.

### 1.5 Unused on-disk templates
- **Files:** `prompts/chat/qa.jinja` (chat engine uses a completely different hardcoded prompt in `chat_engine.py:536-548`), `prompts/extraction/structure.jinja` (no code calls this template at all)
- **Fix:** Delete both. They are dead code that confuse the prompt inventory.

### 1.6 `<system>` tags in Jinja templates are meaningless noise
- **Files:** All `prompts/*.jinja` (first line of each)
- **Problem:** `<system>...</system>` tags are rendered into the `HumanMessage` body, not the actual system message. They cost ~20-40 tokens per call and serve no purpose — models don't treat HTML-like tags in user messages as system instructions.
- **Fix:** Remove `<system>`/`</system>` tags from all Jinja templates. Move the persona text to the actual `SystemMessage` objects in the calling code.

### 1.7 Retry `max_tokens` too high for structured calls
- **File:** `backend/app/services/chat_engine.py:132`
- **Problem:** On `BadRequestError`, retry sets `max_tokens=2048`. For the reranker (returns 8 numbers) and rewrite call (returns a short JSON object), 2048 is 10-20x what's needed.
- **Fix:** Cap retry `max_tokens` per call type: rerank=128, rewrite=512, answer=2048.

---

## 2. Backend Compute & Response Speed

### 2.1 Chat holds a DB connection for the entire LLM call (30+ seconds)
- **Files:** `backend/app/routers/chat.py:122-207`, `backend/app/services/chat_engine.py:151-234`
- **Problem:** The DB session from `Depends(get_db)` is held open during the entire LLM inference. `chat_engine.answer()` opens its own `SessionLocal()` and holds it while running `index_document()` (loads ALL extracted text) and `ensure_embeddings()` (makes HTTP calls to Ollama with 120s timeout). Two DB connections tied up per chat request.
- **Fix:** Save user message → close session → run LLM pipeline → open new session → save AI response.

### 2.2 `summarize_pages_batch` can fire 40 concurrent LLM calls
- **Files:** `backend/app/tasks/summary_tasks.py:85-86`, `backend/app/services/summarizer.py:136-148`
- **Problem:** 10 pages via `asyncio.gather()` per batch, 4 Celery worker processes = 40 simultaneous LLM calls. A single-GPU Ollama instance will queue all 40, each with 40s timeout, causing cascading timeouts.
- **Fix:** Add `asyncio.Semaphore(4-8)` to cap concurrent LLM calls globally.

### 2.3 Dual sync+async DB engines waste connections
- **File:** `backend/app/database.py:27-43`
- **Problem:** Both `create_engine` (sync) and `create_async_engine` (async) are created with `pool_size=5`. The async engine is never used. Every process opens 10 connections instead of 5.
- **Fix:** Remove the async engine entirely.

### 2.4 `_ensure_section_stubs` blocks GET requests with synchronous PDF parsing
- **File:** `backend/app/routers/documents.py:85-136,520`
- **Problem:** `GET /summaries` opens the PDF with `pypdfium2` synchronously if section stubs don't exist yet. Blocks the request handler until PDF outline is detected.
- **Fix:** Move outline detection to a background task. Return existing stubs immediately, dispatch async detection if none exist.

### 2.5 N+1 queries in export service
- **Files:** `backend/app/services/export_service.py:113-147` (chat, flashcards), `backend/app/tasks/export_tasks.py:38-47`
- **Problem:** Loops through conversations/documents, firing one query per item inside the loop.
- **Fix:** Use `joinedload`/`subqueryload` or restructure queries to load all related data upfront.

### 2.6 Unbounded queries — no pagination
- **Files:** `chat.py:217-227` (all messages), `flashcards.py:81-91` (all flashcards), `documents.py:460-477` (all pages)
- **Problem:** Returns everything with no LIMIT. Large conversations/documents send huge JSON responses.
- **Fix:** Add `skip`/`limit` query parameters. Default to reasonable page sizes (50-100).

### 2.7 Missing composite database indexes
- **File:** `backend/app/models/__init__.py`
- **Missing indexes:**
  - `ExtractedText`: `(document_id, page_number)` — queries always filter+order by both
  - `Summary`: `(document_id, mode)` — queries filter by both
  - `Flashcard`: `(user_id, due)`, `(user_id, reps)` — common query patterns
- **Fix:** Add composite indexes via `op.create_index()` in Alembic migrations.

### 2.8 Race condition on section summary generation
- **File:** `backend/app/routers/documents.py:560-567`
- **Problem:** Two concurrent requests can both see `status != "generating"`, both dispatch Celery tasks. Double LLM cost.
- **Fix:** Use database-level advisory lock or UNIQUE partial index on `(section_title, status='generating')`.

### 2.9 Flashcard task loads ALL content before truncating
- **File:** `backend/app/tasks/flashcard_tasks.py:83-91`
- **Problem:** Loads all page summaries or extracted text into memory, joins into one giant string, then truncates to 60k chars. Very wasteful for large documents.
- **Fix:** Stream content with a running char counter, stop when 60k is reached.

### 2.10 PDF parser loads entire document into memory
- **File:** `backend/app/services/pdf_parser.py:18-65`
- **Problem:** All page data (text, bounding boxes, headings, tables, images) accumulated in a Python list before DB commit. 500-page scanned PDF = several GB RAM.
- **Fix:** Process pages in batches of 10-20, commit each batch, and release memory.

### 2.11 Redundant per-page JSON files written to disk
- **File:** `backend/app/services/pdf_parser.py:52-55`
- **Problem:** `page_1.json`, `page_2.json`, etc. written to disk but never read. Redundant with DB storage.
- **Fix:** Delete this code. The data is already in `ExtractedText` rows.

### 2.12 Unnecessary full-document OCR
- **File:** `backend/app/services/pdf_parser.py:41`, `backend/app/tasks/pdf_tasks.py:48-49`
- **Problem:** `use_ocr=True` by default. Every PDF gets OCR'd even if it's already digital text. OCR is extremely CPU/GPU intensive.
- **Fix:** Auto-detect: try text extraction first, fall back to OCR only if pages are empty.

### 2.10 Podcast TTS holds all WAV bytes in memory
- **File:** `backend/app/tasks/podcast_tasks.py:310-313`
- **Problem:** All synthesized WAV clips accumulated in a list before stitching. For deep_dive (200 exchanges), hundreds of WAV blobs in memory.
- **Fix:** Write each clip to a temp file as it's synthesized, then stitch from disk.

---

## 3. Frontend Performance & Memory

### 3.1 All PDF pages mounted as React components (largest frontend issue)
- **File:** `frontend/src/components/pdf/PdfViewer.tsx:548-563`
- **Problem:** `Array.from({ length: pdfDoc.numPages }).map()` creates N `PdfPage` components. A 1000-page PDF = 1000 component instances with state, refs, and effect subscriptions in the React tree.
- **Fix:** Virtualized rendering — only mount `PdfPage` for pages within a window of the viewport. Use placeholder divs for off-screen pages.

### 3.2 Unbounded module-level caches never evict
- **Files:** `PdfViewer.tsx:51` (`pageTextCache`), `ttsEngine.ts:226` (`pageSentencesCache`)
- **Problem:** These Maps grow without bound during a session. For large documents, they accumulate tens of MB.
- **Fix:** LRU cache (max 50 entries) or age-based eviction for pages far from current position.

### 3.3 Dead dependencies — `react-pdf`, `framer-motion`
- **File:** `frontend/package.json`
- **Problem:** `react-pdf` (~1MB with pdfjs-dist dependency) and `framer-motion` (~60KB gzipped) are installed but never imported. `@tanstack/react-query` is provided but unused.
- **Fix:** Remove `react-pdf` and `framer-motion`. Keep `@tanstack/react-query` for future migration.

### 3.4 `flashcardsApi.ts` bypasses the 401 token refresh interceptor
- **File:** `frontend/src/services/flashcardsApi.ts:11-22`
- **Problem:** Creates its own axios client without the shared 401-refresh interceptor. Expired token = raw 401 errors instead of transparent refresh.
- **Fix:** Import the shared `api` client from `api.ts`.

### 3.5 WebSocket has no reconnection logic
- **File:** `frontend/src/hooks/useWebSocket.ts:26`
- **Problem:** When WebSocket closes, progress state is set to `null`. No reconnection attempt. User loses progress updates permanently until doc switch.
- **Fix:** Add exponential backoff reconnect (1s, 2s, 4s, max 30s, max 10 attempts).

### 3.6 Dashboard polls `getDocuments` every 5 seconds unconditionally
- **File:** `frontend/src/app/dashboard/page.tsx:79-88`
- **Problem:** Continuous polling even when nothing is being processed. Wastes bandwidth and server load.
- **Fix:** Only poll when documents are in non-terminal states. Use WebSocket progress events to trigger refetches.

---

## 4. Reliability & Frontend-Backend Communication

### 4.1 No request retry for transient failures
- **File:** `frontend/src/services/api.ts:6-9`
- **Problem:** A transient network blip causes immediate failure. Only 401 gets special retry handling.
- **Fix:** Add retry (2 attempts, 1s/2s backoff) for idempotent GET requests only.

### 4.2 No rate limiting on any endpoint
- **Problem:** An authenticated user can hammer `/api/chat` with hundreds of concurrent requests, each consuming DB connections and queuing LLM calls.
- **Fix:** Add simple rate limiting (e.g., 10 requests/minute per user for chat, 30/minute for general API).

### 4.3 WebSocket state is in-memory only — no multi-replica scaling
- **File:** `backend/app/routers/websocket.py:13-14`
- **Problem:** `active_connections` is a Python dict. Scaling API to multiple replicas breaks WebSocket delivery.
- **Fix:** Use Redis Pub/Sub for cross-process message delivery.

### 4.4 Embedding search runs dot products in Python
- **File:** `backend/app/services/retrieval.py:230-233`
- **Problem:** For every chat query, Python computes dot products for all chunks. 900 chunks x 3 queries x 768 dimensions = heavy CPU.
- **Fix:** Use `pgvector` for ANN search in PostgreSQL. Load vectors to DB, use `<=>` operator.

### 4.5 `get_current_user_from_token` fires DB query on every request
- **File:** `backend/app/routers/auth.py:76-86`
- **Problem:** Every authenticated API call loads the full User object from DB. Most endpoints only need the user_id (already in JWT).
- **Fix:** Cache User objects in-memory with short TTL (5s). Only load from DB when `is_admin` or profile fields are needed.

### 4.6 `ensure_embeddings` embeds ALL chunks on first chat
- **File:** `backend/app/services/retrieval.py:179-202`
- **Problem:** On first chat for a document, ALL chunks are embedded in batches of 32. For 900 chunks, that's 29 HTTP roundtrips to Ollama before the chat can even start.
- **Fix:** Lazy-embed: only embed chunks within the BM25 retrieval window on first search. Embed remaining chunks in the background.

---

## 5. Infrastructure & Scaling

### 5.1 Redis underutilized
- **File:** `docker-compose.yml:74-80`
- **Problem:** Redis runs but only serves as Celery broker. Could be a shared cache layer.
- **Fix:** Use Redis for: shared cache across processes (page content, LLM config), WebSocket message bus, rate limiting counters.

### 5.2 Single Redis instance — no high availability
- **Problem:** If Redis goes down, all task queuing stops. No Sentinel/Cluster.
- **Fix:** For self-hosted, Redis persistence (AOF + RDB) is sufficient. For multi-host, add Sentinel.

### 5.3 File-based document storage — no horizontal scaling
- **Problem:** Documents, TTS audio, and extracted text stored in local volume. Cannot scale horizontally without shared filesystem.
- **Fix:** For future scaling, migrate to S3-compatible storage. For now, document the limitation.

### 5.4 Celery `_run_async` duplicated 3 times
- **Files:** `summary_tasks.py:14-25`, `podcast_tasks.py:77-86`, `flashcard_tasks.py:20-29`
- **Problem:** Three identical copies of async-from-sync bridging code.
- **Fix:** Extract to a shared utility in `backend/app/utils/async_compat.py`.

---

## 6. Quick Wins (Low Effort, High Impact)

| # | Change | Effort | Impact |
|---|--------|--------|--------|
| Q1 | Reduce page content from 40k to 15k chars (`summarizer.py:80,115`) | 2 min | Huge token savings |
| Q2 | Remove `_run_async` duplication (extract to shared util) | 15 min | Maintainability |
| Q3 | Delete dead dependencies (`react-pdf`, `framer-motion`) | 5 min | Bundle size |
| Q4 | Delete unused templates (`chat/qa.jinja`, `extraction/structure.jinja`) | 2 min | Cleanliness |
| Q5 | Remove `<system>` tags from Jinja templates | 5 min | Token savings |
| Q6 | Fix `flashcardsApi.ts` to use shared api client | 10 min | Reliability |
| Q7 | Add composite DB indexes | 20 min | Query speed |
| Q8 | Remove async DB engine (`database.py`) | 10 min | Connection savings |
| Q9 | Delete redundant per-page JSON files from pdf_parser | 5 min | Disk savings |
| Q10 | Add WebSocket reconnect with backoff | 15 min | Reliability |

---

## Verification Plan

After implementing changes:

1. **Token reduction**: Check LLM API logs to measure input/output tokens before and after for page summary, section summary, podcast, and chat flows.
2. **Response speed**: Time `GET /summaries`, `POST /chat`, and document upload with a large PDF (500+ pages). Compare before/after.
3. **Memory**: Monitor Celery worker memory with `docker stats` during heavy workloads. Verify `worker_max_memory_per_child` still triggers correctly.
4. **Frontend**: Open a 500-page PDF, scroll through all pages. Verify no performance regression, check browser memory usage.
5. **Reliability**: Kill WebSocket connection mid-task, verify reconnect. Simulate token expiry during flashcard operations.
6. **Tests**: Run `pytest` to ensure no regressions. Run frontend build to verify no type errors.
