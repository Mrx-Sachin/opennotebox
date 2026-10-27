# OpenNotebox - Progress & Handoff Doc

Last updated: 2026-07-23. Pass this doc back to Claude to resume work.

## 2026-07-23: Research-paper column reading order

User reported "not reading in correct order" for research papers - root
cause: pdfplumber's `extract_text()` (the active parser; OpenDataLoader
isn't installed, `_fallback_parse` always runs) groups words into lines by
vertical position only, so on a genuine 2-column layout it interleaves the
columns line by line (left col line 1, right col line 1, left col line 2,
...) instead of reading the whole left column before the right one.

Implemented automatically (no manual "mode" toggle - it detects per-page
via `services/pdf_parser.py`'s `extract_reading_order_text`, so single-
column docs and non-paper PDFs are untouched, verified as a strict no-op on
those via diff-against-old-output regression tests). Algorithm: group words
into visual rows, find a page's column gutter as a *consensus* position
across many rows' own internal word-gaps (NOT one global page-wide empty-
band scan - that gets fooled by any wide table/figure sharing the same
x-range anywhere on the page), split any row that plausibly spans both
columns at that gutter, then emit the whole left column before the whole
right column between full-width breaks (titles/headings/tables flush the
buffered columns and print as their own line).

Tuning notes if this needs revisiting:
- `MIN_COLUMN_ROWS = 8` is deliberately conservative. Verified empirically:
  lowering it to 4 catches more real 2-column pages but also produces false
  positives on data tables and math formulas whose internal column
  alignment coincidentally looks like a text gutter - those are WORSE than
  doing nothing (actively garble previously-fine text), whereas missing a
  real 2-column page just leaves it as before this feature existed. Bias
  towards the safer failure mode if retuning.
- Known unfixed edge case: an indented heading label (e.g. a bold
  "Abstract" line) immediately followed by body text can still merge with
  a neighboring column's first line if the heading's indent is far enough
  from the normal column margin - affected exactly one line out of a
  16-page test paper. Not worth chasing further without a concrete new
  regression.
- Verified against real PDFs: a genuinely single-column NeurIPS paper (11
  pages, near-zero diff vs the old extraction) and BERT (arxiv 1810.04805,
  genuine 2-column ACL-style layout, 12/16 pages correctly detected and
  reordered) - fetched live via httpx from inside the api container during
  testing, not committed to the repo.
- Self-check: `backend/test_pdf_column_order.py` (synthetic word-dict
  fixtures, no PDF needed) - run with `python3 test_pdf_column_order.py`
  inside the api container or with `app` on the path.

### Follow-up: verified against the user's actual document + a real 2nd bug fixed

User reported live "jumping between sections" while clicking to read
`whisper.pdf` (doc id 17, OpenAI Whisper paper, genuine 2-column). First
finding: **parsing only happens once at upload time** - doc 17 was parsed
before the fix above landed, so its stored `ExtractedText` rows were still
the old scrambled+glued-words content no matter what the code said. There
is currently NO way to re-parse an existing document from the UI; I did it
manually (delete its `ExtractedText` rows, re-run
`parse_pdf_document.run(doc_id)`). **Any document uploaded before this
session's pdf_parser.py fixes needs the same manual re-parse to benefit** -
worth adding a real "re-parse document" button/endpoint if this comes up
again; wasn't built this session (out of the scope actually asked for).

Testing against the real re-parsed doc 17 surfaced a second, genuine bug in
`_detect_gutter`: it clustered consensus on the gap's LEFT edge (`gap_x0`),
which is unreliable - a short last-line-of-paragraph row ends wherever its
last word happens to end, producing a wide but non-gutter-shaped gap that
doesn't match other rows' true gutter position, silently dropping otherwise-
good candidates below the row-count threshold. Fixed: cluster on the gap's
RIGHT edge (`gap_x1`, where the right column starts - empirically rock
solid, varies <1pt across every row in a real 2-column region) instead, and
use median instead of mean when averaging both `gutter_x0`/`gutter_x1` and
the column margins, so a minority of still-contaminated left-edges can't
drag the result off (verified: whisper.pdf page 1 had 3 of 7 candidate rows
with a badly wrong gap_x0 but a correct gap_x1 - median tolerates that,
mean does not).

Verified on doc 17 directly (not isolated fixtures): page 3 and page 9 (real
body prose) now read completely coherently in correct order. Page 1 (title
+ authors + abstract + intro crammed onto one page) still has ONE remaining
known issue - the bold "Abstract" heading label is indented far enough from
the normal column margin that it doesn't pass the "spans full column width"
guard, so it stays merged with the right column's first line. Tried
lowering `MIN_COLUMN_ROWS` to 6 specifically to catch this page - it
immediately regressed elsewhere (split the author byline mid-name, "Greg" /
"Brockman" ended up on different output lines) with zero net improvement on
page 1 itself, so reverted to 8. Not chasing further without a concrete new
regression - same false positives caught earlier (data tables, math
formulas) also reappeared as soon as the threshold dropped.

Also confirmed doc 17's pages with rotated chart axis labels (pages 4, 6,
10, 12 - `hceeps` = "speech" backwards, `noitingocer` = "recognition"
backwards) show garbled reversed text - this is a SEPARATE, pre-existing
pdfplumber limitation with rotated glyphs (same root cause as the arXiv
sidebar-watermark reversal seen during BERT testing), not a column-order
bug and not something this session's fix touches or worsens. Out of scope
unless explicitly asked for.

## 2026-07-22: PDF2Audio/pdf-to-podcast research + podcast "deep dive" length

Cloned both repos into untracked `_research/` for reference (their audio is
paid OpenAI cloud TTS, not portable - decision was to stay Piper-only).
Ported the genuinely reusable part: scratchpad-first structured dialogue
generation in `services/prompts.py`'s podcast/dialogue in-code default -
the model brainstorms (topics, analogies, jargon to define) before writing
the transcript, in one JSON call `{scratchpad, title, lines}`. Verified real
quality jump on doc 16 (Hyrum's Law, Beyoncé Rule, Bus Factor, natural
non-recap ending). `podcast_tasks.py` now persists `scratchpad` on the
transcript for QA (not shown in the player UI).

Then added a `deep_dive` podcast length tier (`PodcastLength` type, frontend
length picker) targeting a real 35-40 min episode. A single LLM call for that
much dialogue is unreliable (slow under shared-GPU contention, and one
~100k-char content slice can't cover a 600-page book anyway), so it's
generated as **7 sequential continuation-aware segments** instead:
`_split_content_segments` divides the gathered content on page boundaries;
`_generate_dialogue_segments` in `podcast_tasks.py` renders each segment with
the prior segment's last 3 lines as `continuation_tail` (new Jinja
conditionals in the podcast/dialogue template: `segment_index`,
`segment_count`, `continuation_tail` - non-final segments are told not to
wrap up, the final one gets the real recap) and stitches all lines together
before TTS synthesis. `LENGTH_CONFIG["deep_dive"]` exchange/segment counts
were empirically calibrated from a real run (measured **~12.3s of
synthesized audio per exchange**, not a guessed ~24s) to actually land in the
35-40 min range - do not retune this without a real measured run, guessed
pacing was off by ~2x the first time. `_generate_script`'s ChatOpenAI call
now has an explicit 480s timeout (previously unset, a real risk once
generation is long-running under GPU contention).

Verification: first deep_dive run (4 segments, un-calibrated) completed at
18.6 min / 91 lines - confirmed the segmenting/stitching mechanism works
with zero repetition. Second run (7 segments, recalibrated) landed at
**32.6 min / 203 lines** (doc 16, podcast id 16) - close to the 35-40 min
target; per-line pacing varies by run (9.6s/line this time vs 12.3s in the
calibration run, natural variance in sentence length), so don't expect exact
minute precision, just "genuinely long-form." Spot-checked the segment
boundary (exchange ~29, where segment 1 hands off to segment 2) and the
ending: no re-greeting, no repeated topics, natural continuous arc, and the
final line is a real "wrap up this deep dive" conclusion, not a dead stop.
This tier legitimately takes a long time to generate (7 sequential LLM
calls + ~200 sequential per-line Piper TTS synth calls) - the existing
async/poll UI already handles that fine, no changes needed there. If you
want it noticeably faster later, the TTS synthesis loop in `generate_podcast`
is sequential (`for line in cleaned_lines: ... _synthesize_line(...)`), not
parallelized like the LLM segments are - batching that with a thread pool
would cut wall time substantially, but wasn't needed for correctness so it
was left alone (scope discipline).

---

## TASK BOARD - hand this section to any agent, check off what you finish

Rules for any agent: NEVER open Chrome or use browser automation (the user
tests visually and reports back). Verify with curl / in-container python /
`npx tsc --noEmit` / docker logs only. Smallest correct diff, reuse existing
patterns, no em dashes anywhere, do not commit unless asked. Stack cheat
sheet is at the bottom of this file.

### [x] T1. Backend endpoint POST /api/tts/play/text  (CRITICAL, blocks reading)
Body {"text": str, "voice": optional str}; same contract as POST
/api/tts/play/page/{doc}/{page} in backend/app/routers/tts.py (cached ->
{status:"cached",cache_id,audio_url,text_hash,duration}; else queue
generate_tts -> {status:"generating",task_id,text_hash}). Auth required,
422 if text > 5000 chars. The frontend chunked TTS engine already calls it
(playText in frontend/src/services/api.ts).
Verify: curl play -> poll /api/tts/cache/{hash} -> GET audio = 200 WAV.

### [x] T2. Nested chapter list (doc 16 shows only "Part I/II..." headers)
detect_pdf_outline_sections in backend/app/services/summarizer.py must emit
level-0 AND level-1 outline entries (title, start_page, end_page, level).
Add `level` INTEGER col to summaries (live ALTER TABLE + model + schema).
Backfill doc 16 (delete old stubs, re-detect); make _ensure_section_stubs in
routers/documents.py self-heal docs whose stubs all have level NULL.
Verify: GET /api/documents/16/summaries?mode=section lists Parts AND chapters.

### [x] T3. Podcast fixes
(a) "No content available": _gather_content in tasks/podcast_tasks.py,
scope=="section" must join ExtractedText pages start_page..end_page of the
section row (summary content only as fallback). (b) Optional "length":
"short"|"medium"|"long" on POST /api/podcasts (col + schema + prompt exchange
counts 8-12/16-24/30-45). Frontend already sends length.
Verify: in-container generate_podcast.run() on a doc-16 chapter -> completed,
on-topic transcript.

### [x] T4. Voice quality upgrade (user: voices "very robotic")
tts/main.py registry: default -> en_US-libritts_r-medium, host_a ->
en_US-ryan-high, host_b -> en_US-lessac-high (HF rhasspy/piper-voices;
startup downloader must fetch new .onnx+.json). Bump httpx timeouts in
podcast_tasks._synthesize_line + tasks/tts_tasks.py (high models slower).
Verify: POST localhost:5111/tts per voice -> 200 WAV; report synth seconds
for ~280 chars (flag if default > 5s).

### [x] T5. Teacher-style summary prompts
services/prompts.py _get_default (prompts/ dir NOT mounted - in-code defaults
are live): rewrite summary/page and summary/section as guided teaching -
plain-language explanation, key terms defined, examples/details preserved,
clean Markdown (headings, bold terms, bullets), page ~400-500 words, section
~800. No meta-commentary ("This page discusses"). Keep is_refusal guard.
Verify: generate 1 page + 1 section summary on doc 16, judge quality.

### [x] T6. TTS must not read markdown symbols
Strip md (#, *, _, backticks, links->text, list markers) to speakable text in
routers/tts.py SUMMARY play path only (page path is not markdown). stdlib re.

### [x] T7. On-demand page summaries
Stop auto-queuing generate_page_summaries at upload (trace trigger in
tasks/pdf_tasks.py / documents router). Add POST
/api/documents/{id}/summaries/page {page_number} (idempotent,
generating/poll like the section endpoint) + POST .../summaries/pages/all.
Append to END of api.ts: generatePageSummary, generateAllPageSummaries.

### [x] T8. Settings API + model switching
app_settings table (single JSON row). GET /api/settings ->
{"llm":{"backends":[{"name","base_url","model","api_key_set"}],"active":"ninja"},
"tts":{"default_voice":"default"}} seeded from config.py; PUT accepts partial
(backends entries {name,base_url,model,api_key?}); never echo api_key back.
New app/services/llm_settings.py get_active_llm() (15s cache) used by
summarizer.py, podcast_tasks.py, flashcard_tasks.py (NOT chat_engine.py -
leave TODO). tts play endpoints honor tts.default_voice. GET /api/tts/voices
-> {"voices":["default","host_a","host_b"]}. api.ts (END, no duplicates):
getSettings, updateSettings.

### [x] T9. DONE (SettingsModal.tsx complete, wired to TopBar gear, verified)
Gear in components/layout/TopBar.tsx -> glass modal (copy UploadModal.tsx
pattern): backend rows from GET /api/settings (select -> PUT llm.active,
delete custom rows, "Add endpoint" form name/base_url/model/api_key password
field), voice select from /api/tts/voices -> PUT tts.default_voice. Esc closes
(capture-phase like FlashcardPanel). Handle 404 gracefully.

### [x] T10. DONE (packages pinned, MessageBody renders md with+without citations, SummaryPanel md, tsc clean)
Install react-markdown remark-gfm unist-util-visit (host `npm install` in
frontend/ AND confirm container sees them: docker compose exec frontend ls
node_modules/react-markdown || exec npm install). MessageBody.tsx already has
the remark citation-chip integration (do not restructure; make the
no-citations path also render markdown). SummaryPanel.tsx: render s.content
via ReactMarkdown+gfm, dark-theme classes (headings text-cream font-semibold,
body text-cream-dim text-[13px], strong text-cream, code bg-void px-1,
ul list-disc pl-4).
Verify: npx tsc --noEmit clean (currently fails ONLY on these missing pkgs).

### [x] T11. DONE (exit pill in dashboard/page.tsx, sidebar rail has expand chevron)
Floating pill fixed top-right visible only in focusMode ("Exit focus", glass,
opacity-40 hover:100) -> toggleFocusMode; place in app/dashboard/page.tsx.
Check Sidebar.tsx collapsed 64px rail always shows an expand chevron.

### DO NOT TOUCH (owned by main Claude session, already done)
PdfViewer.tsx, ttsEngine.ts, BottomBar.tsx, PodcastPanel.tsx,
useKeyboardShortcuts.ts, stores/reader.ts, textAlign.ts, sentences.ts,
services/chat_engine.py, services/retrieval.py.

### T1-T8 landed by the backend agent (2026-07-17, continuation session)

The dead agent's mid-work edits were ~95% complete and correct on inspection.
What the continuation session actually had to do:

- **Live ALTER TABLE**: `summaries.level` and `podcasts.length` columns did
  not exist on disk yet (code referenced them, DB didn't have them) - added
  both live, `app_settings` table came for free via `Base.metadata.create_all`
  on next api startup.
- **Bug fix (T1)**: `POST /api/tts/play/text` was dead code - registered
  *after* `POST /api/tts/play/{summary_id}` in routers/tts.py, so FastAPI's
  route-matching-by-registration-order sent every `/play/text` request into
  `/play/{summary_id}` with `summary_id="text"`, a guaranteed 422. Moved the
  `/play/text` route (and its `PlayTextRequest` model) above `/play/{summary_id}`.
  Round-trip now verified: POST -> generating -> poll cache -> GET audio 200 WAV.
- **Bug fix (T2)**: `persist_section_stubs` deduped new outline entries by
  title only, so a title that already had a *generated* (non-empty) summary
  from before the `level` column existed (e.g. doc 16's "Preface" and "Part I.
  Thesis") would never get its `level` backfilled, even after `_ensure_section_stubs`
  ran - it kept looking "not fully healed" forever on those two rows. Fixed to
  backfill `level` in place on existing matching rows without touching their
  content.
- **Missing pieces (T7)**: `generatePageSummary`/`generateAllPageSummaries`
  were never added to api.ts despite the endpoints existing - added at the end
  of the file per the task spec.
- **Timeout bump (T4)**: bumped `_synthesize_line` (podcast_tasks.py) and
  `generate_tts` (tts_tasks.py) httpx timeouts 120s -> 180s for headroom with
  the new "high" quality voices (empirically ~59 chars/sec, so still generous).
- Rebuilt the `tts` image (`docker compose build tts`) - it's not volume
  mounted, a bare restart does not pick up Dockerfile/main.py changes. The
  `/models` named volume pre-dated the new voices, so the startup
  download-missing-models path in main.py was exercised and verified: all 5
  files present, all 3 voice names return 200 + WAV.

All verified in-container/curl only (no browser). See per-task detail below.

- **T1**: curl POST /api/tts/play/text -> generating -> poll
  /api/tts/cache/{hash} -> ready -> GET /api/tts/audio/{id} = 200,
  `audio/wav`, valid RIFF/WAVE file.
- **T2**: doc 16 self-healed from 14 level-0-only stubs to 47 rows (5 top
  Parts + 8 front-matter + 25 chapters, correct nesting, 0 NULL levels). Doc
  12 also self-healed on a normal GET (27 -> 172 rows) as a side effect of
  visiting the endpoint, confirming the self-heal trigger works generically,
  not just for doc 16.
- **T3**: created a real Podcast row for doc 16 (`section`, "Chapter 2. How
  to Work Well on Teams", `length="short"`), ran `generate_podcast.run()`
  in-container - completed in 140s, 12 lines (within the 8-12 short range),
  transcript is entirely on-topic for that chapter (Genius Myth, Bus Factor,
  Three Pillars, blameless postmortems - all real chapter content, no
  hallucinated tangents).
- **T4**: `docker compose build tts` + recreate; all 5 onnx+json pairs
  present; default/host_a/host_b each returned 200 + valid WAV for a
  265-char sample. Timings: default (libritts_r-medium) 1.7s, host_a
  (ryan-high) 4.5s, host_b (lessac-high) 4.4s. Default is well under the 5s/
  280-char flag threshold - nothing to escalate. host_a/host_b are
  noticeably slower than the old "medium" tier but still fast enough for
  interactive use.
- **T5**: generated a fresh page summary (doc 16 p.131, "The Art of
  Reversible Decisions" / "People Are Like Plants") - clean Markdown,
  bolded key terms, no meta-commentary, teacher voice, right length.
- **T6**: verified via the T1 round-trip above (text included `##`/`**`
  markdown, audio generated without incident - stripping happens before
  hashing so this alone doesn't prove symbols were dropped from speech, but
  the code path is a straight regex strip with no external dependency risk).
- **T7**: on-demand single-page generation verified end to end for a
  previously-unsummarized page (doc 16 p.131): stub created with
  `status="generating"`, worker completed it, content persisted. Also
  confirmed the *absence* of the old auto-queue - pdf_tasks.py's
  `_chain_summaries` no longer calls `generate_page_summaries.delay()`.
- **T8**: GET /api/settings returns the config.py-seeded `ninja` backend;
  PUT with a partial `{"tts":{"default_voice":"host_a"}}` round-tripped
  correctly and didn't disturb the `llm` block; GET /api/tts/voices returns
  the 3 names.

`docker compose restart api worker` done at the end (twice, once after the
column/route fixes and once more after the timeout bump). Api and worker
both started clean, no import errors.

**Not done, left as noted TODOs already in this file**: `chat_engine.py` is
still not wired to `get_active_llm()` - out of scope, flagged in T8 and in
`llm_settings.py`'s own docstring.

---

## Current state: what works (verified E2E via curl)

- **PDF viewer + click-to-read TTS**: working (user-confirmed). Do not touch
  `PdfViewer.tsx`, `textAlign.ts`, `sentences.ts`, `ttsEngine.ts` without reason.
- **Podcasts**: two-host dialogue generation verified end to end. Full-doc scope
  (Clean Code, 150s WAV) and section scope via HTTP API (92s WAV). Transcript
  quality good. Auth verified (401 unauthenticated, 404 cross-user).
- **Flashcards**: 20 cards generated for doc 12; deck UI works. Fixed the
  mirrored-text flip bug (Chromium: backdrop-filter breaks backface-visibility;
  card faces now use solid `bg-panel` instead of `.glass`).
- **Chapter/section summaries (on-demand)**: doc 12 shows 27 real chapters
  (from PDF outline via pypdfium2). Each has a "Generate" button in the
  SummaryPanel Sections tab; generation persists; idempotent
  (`POST /api/documents/{id}/summaries/section` body `{section_title}`).
  Stale "Remaining" refusal-text summaries purged; refusal guard added so
  LLM refusals are never persisted.
- **Summary TTS**: verified working (play -> poll `/api/tts/cache/{hash}` ->
  fetch audio blob). No changes were needed.
- **Page summaries background job**: fixed silent death (Celery 15-min hard
  time limit killed the hours-long job invisibly). Now has 3h limit,
  idempotent per-page commits, acks_late. Backfill for doc 12 (462 pages) was
  mid-run - check `SELECT COUNT(*) FROM summaries WHERE document_id=12 AND
  mode='page'` to see progress.

## Chat rewrite (in progress - LAST ACTIVE TASK)

Old chat stuffed 40k tokens of page summaries in document order -> >60s
latency, fake citations. Fully rewritten:

### Backend pipeline (`backend/app/services/chat_engine.py` + `retrieval.py`)
1. **Query rewrite** (1 small LLM call): produces `keywords` (BM25 phrases in
   the text's own vocabulary) + `rephrasings` (alternative phrasings of the
   question, for embeddings). Keywords must NOT go to semantic search - they
   drag it toward guessed answer topics.
2. **Lexical retrieval**: SQLite FTS5 (`chunk` table + `chunk_fts`
   external-content index, porter stemming, BM25). Paragraph chunks
   ~1600 chars, page-anchored, built lazily on first chat per doc
   (`index_document`). Junk filter skips TOC/index pages (dot leaders,
   digit-ratio > 0.15, Index/Contents running headers). Tiered queries:
   exact phrase > all-terms AND > OR. 24 candidates.
3. **Semantic retrieval**: `nomic-embed-text` pulled onto ninja Ollama
   (`ollama rm nomic-embed-text` to undo). Embeddings stored as packed-float
   BLOBs in `chunk.emb` (no numpy in container - pure Python dot products,
   vectors L2-normalized at store time). Query = raw question + rephrasings,
   max-sim per chunk, top 12. Snippets for semantic hits are extracted via
   FTS `snippet()` against the query terms (chunk heads mislead the reranker).
4. **LLM rerank** (1 small call): picks up to 8 from ~35 candidate snippets;
   backfills to >=5 sources if the pick is thin. Falls back to BM25 order.
5. **Answer** (1 call): numbered source list `[n] (title, p. X)`, strict
   inline-citation system prompt, last 3 turns of history included. Prompt
   ~4-6k tokens total.
6. **Citation filter**: only sources whose `[n]` actually appears in the
   answer are returned: `citations: [{n, doc_id, doc_title, page, excerpt}]`.

### Frontend
- `MessageBody.tsx` (new): renders inline `[n]`/`[1, 2]` markers as indigo
  chips; hover shows fixed-position source card (doc title, page badge,
  excerpt, "Jump to page N"); click jumps. Fallback: plain text + old
  "N sources" button when no inline markers resolve.
- `ChatPanel.tsx`: uses MessageBody for assistant messages.
- `api.ts` `sendMessage`: timeout raised to 180s.
- Backward compatible with old messages (old citation shape lacks `n`).

### Verification status of chat
- Staged trace CONFIRMED on the hard test question ("Which parts of the book
  are irrelevant now because of the advancement in AI?", doc 12): the
  "There Will Be Code" passage (p.33) enters candidates via rephrasings and
  the reranker now picks it (`RERANKED pages: [33]`).
- **NOT yet re-verified**: the final full HTTP E2E after the last two edits
  (rerank backfill + rephrasings split). The previous full E2E (before those
  edits) worked end to end and returned a correct inline-cited answer
  (`[1] p.52` modern-IDE passage) in ~3m10s with the GPU quiet.
- Latency: ~1-3 min per chat on doc 12 while the page-summary backfill
  competes for the single GPU (model swap gemma<->qwen each call). Quiet GPU
  should be well under 90s. Frontend timeout is 180s - may still be tight
  under contention.

## ACTIVE SPRINT (2026-07-17, session ended mid-flight - READ THIS FIRST)

User requests being implemented (all from one big feedback round on doc 16,
"Software Engineering at Google.pdf", 602 pages, user 2 / sachin):

### My work - DONE, code-complete, tsc-clean for my files, NOT browser-tested
1. **Chunked gapless TTS** (frontend/src/services/ttsEngine.ts REWRITTEN):
   page sentences grouped into ~280-char chunks (buildChunks); first chunk
   plays fast, prefetchAhead prepares next 2 chunks + next page's first chunk;
   transitions = src swap. DEPENDS ON backend POST /api/tts/play/text
   (delegated to backend agent - VERIFY IT EXISTS; client fn playText in
   api.ts). Sentence highlight preserved (global idx = chunk.from + local).
   Summary/podcast playback = playAudioUrl/playSummaryAudio (direct audio,
   docId/page null, seekTo/skipSeconds work).
2. **Scroll-pause + return pill** (PdfViewer.tsx + stores/reader.ts):
   followReading store flag; wheel/touchmove on the scroll container turns it
   off during playback; "Back to reading" pill (fixed bottom-14, LocateFixed
   icon) scrolls to .tts-sentence-active and re-enables. Highlight effect only
   scrolls when followReading.
3. **Citation flash-highlight** (reader.ts jumpToCitation(page, excerpt) sets
   currentPage + pendingHighlight; PdfPage effect finds excerpt via
   findRange(normLayer, excerpt[:120]), adds .citation-flash (globals.css,
   2.6s fade), clears store). Wired in ChatPanel + CitationPopup (onJump now
   takes the citation object).
4. **PDF invert** (reader.ts invertPdf, default ON, localStorage onb.invertPdf;
   PdfViewer toolbar Moon/Sun toggle; canvas filter invert(0.94)
   hue-rotate(180deg), container bg #101014; text-layer highlights stay true).
5. **Podcast via bottom player** (BottomBar.tsx directAudio mode: time seek on
   track click, +/-15s skips incl. arrow keys in useKeyboardShortcuts;
   PodcastPanel.tsx rewritten to play through ttsEngine.playAudioUrl + length
   picker short/medium/long; api.ts createPodcast takes length).

### Backend agent (running at session end; task list in its prompt)
Nested chapter sections (level 0+1, `level` col on Summary, doc 16 backfill);
podcast section scope reads ExtractedText page ranges (fixes "No content
available"); podcast `length` option; voice upgrade (default ->
en_US-libritts_r-medium, host_a -> ryan-high, host_b -> lessac-high, bump
tts-call timeouts); teacher-style rich markdown prompts (page ~450w, section
~800w, no meta-commentary); TTS markdown stripping on summary play; POST
/api/tts/play/text (CRITICAL - my chunked engine calls it); on-demand page
summaries (no auto whole-book; POST .../summaries/page {page_number} + a
/pages/all bulk endpoint); settings API (app_settings table, GET/PUT
/api/settings with llm.backends[]/active + tts.default_voice, GET
/api/tts/voices, services/llm_settings.py get_active_llm() used by
summarizer/podcast/flashcards; chat_engine still binds at import - wire it
later). User will paste their H100/"BLM" OpenAI-compatible endpoint into
Settings themselves.

### Frontend agent (running at session end)
Settings modal (TopBar gear; backend picker + add-endpoint form + voice
select, contract above); react-markdown+remark-gfm+unist-util-visit install
(host node_modules AND check container) - MessageBody.tsx markdown+citation
chips mostly done by a predecessor (remark plugin approach), needs packages
installed + no-citation path to also render markdown; SummaryPanel markdown;
focus-mode exit pill; collapsed-sidebar expand affordance.

### Known state / gotchas
- tsc errors remaining are ONLY missing react-markdown packages (frontend
  agent installs them). My files compile clean.
- NEVER use Chrome/browser automation - user tests everything themselves,
  give them a checklist instead.
- Chat verified working on doc 16 (587s first run = one-time 602-page chunk
  index + embeddings + GPU contention; later chats much faster). The user's
  "something went wrong" chat error was the ninja-box-down window; fast-fail
  503 + error bubble now exist.
- gnhf/opencode agent runs: user sometimes runs external agents; check
  git status before assuming state.
- A WIP snapshot commit protects this sprint; commit again when agents land.
- After agents finish: run npx tsc --noEmit, curl dashboard 200, verify
  /api/tts/play/text round trip, then FINAL COMMIT and hand the user the
  combined manual test checklist.

## CHAT E2E: DONE (2026-07-17)

Verified on doc 12: single-turn answers are grounded with inline [n]
citations; multi-turn follow-up ("What does the author say about that
prediction?") resolved the pronoun via history, retrieved the p.33 "There
Will Be Code" passage, and produced a fully cited NotebookLM-quality answer
in ~1.5 min. Additions since the first handoff: HyDE hypothetical-passage
field in the rewrite call (embeds near the real passage), per-query semantic
quotas, per-query top-1 semantic hits bypass the reranker (sem_top),
retrieval debug log line, terminology-bridging in answer + rerank prompts,
fast-fail (~10s, HTTP 503 + UI error bubble) when Ollama is unreachable,
SummaryPanel page-poll now survives the long backfill. Committed on master.

Known ceiling: on the vaguest single-turn phrasing the answer can be
conservative (cites the closest passage it kept but hedges) because the
book never says "AI" - a follow-up or more specific phrasing lands it.
Acceptable; do not chase without new evidence.

## SPRINT COMPLETE (2026-07-17, Grok continuation)

T1-T11 all landed. Uncommitted backend-agent leftovers (TTS 180s timeouts,
`persist_section_stubs` level backfill, HANDOFF task board) committed.
Post-land verify (no browser):

- `npx tsc --noEmit` clean (frontend)
- dashboard http://localhost:3003/dashboard -> 200
- api /health 200, tts /health 200
- GET /api/settings + /api/tts/voices OK (auth)
- POST /api/tts/play/text -> generating -> cache ready -> GET audio 200 WAV (RIFF)
- doc 16 section stubs: 47 rows, levels {0:14, 1:33}, 0 NULL
- api + worker restarted after land

### Deferred (not blocking)
- `chat_engine.py` still uses config.py LLM binding at import (not
  `get_active_llm()`). Settings modal switches model for summaries /
  podcasts / flashcards only. Wire chat later if user switches chat model.
- docker healthcheck / Ollama keep_alive pinning - only if they bite.

### Manual browser checklist (USER - no agent browser)
App: http://localhost:3003  (login as your user; doc 16 = SE at Google)

1. **Click-to-read TTS**: open a PDF page, click a sentence - audio starts
   quickly, next chunks prefetch gaplessly, active sentence highlights.
2. **Scroll-pause**: while reading aloud, scroll the PDF - highlight should
   stop following; "Back to reading" pill appears; click it to re-follow.
3. **Summary headphones**: Sections tab -> open a generated summary ->
   headphones icon plays via bottom bar (no `#`/`**` spoken).
4. **Page summary on-demand**: Pages tab -> Generate on a page without
   content; poll until text appears (Markdown headings/bullets rendered).
5. **Nested chapters**: Sections tab on doc 16 shows Parts AND indented
   chapters (not only Part I/II headers).
6. **Podcast**: generate short for a chapter -> plays in bottom bar;
   seek + +/-15s work; transcript on-topic.
7. **Flashcards**: open deck, flip card - front/back not mirrored.
8. **Chat citations**: ask a grounded question; hover [n] chips for source
   card; click jumps PDF + brief flash-highlight on excerpt.
9. **Settings gear (TopBar)**: switch default voice; add/select LLM backend
   (api key never echoed). Esc closes modal.
10. **Focus mode**: enter focus, confirm floating "Exit focus" pill top-right;
    collapsed sidebar shows expand chevron.
11. **PDF invert**: Moon/Sun toolbar toggle; canvas inverted, highlights ok.

## NEXT STEPS

1. **User runs the browser checklist above** and reports any regressions.
2. **Consider** (only if they bite): docker healthcheck/restart-wait for the
   api-restart race in tests; Ollama `keep_alive` pinning; wire chat_engine
   to `get_active_llm()`.

## Environment cheat sheet

- Stack: `docker compose` in `open-notebox/` - api:8001, frontend:3003,
  tts:5111, redis, worker. Backend code volume-mounted (`restart api worker`
  to reload); frontend hot-reloads.
- Ollama: remote at `ninja:11434` (inside containers only). Models:
  qwen3.6:27b (chat), gemma4:31b (summaries), nomic-embed-text (embeddings).
  Single GPU - jobs contend; stop worker for clean latency tests
  (`docker compose stop worker` / `start worker`).
- Test users: `user_f_test`/`testpass123` (owns doc 12, the 462-page Clean
  Code PDF - password was reset by an agent, tell the user);
  `podcast_e2e_test`/`TestPass123!` (owns doc 15, small ocean-currents doc).
- DB: SQLite at `data/open_notebox.db` (in-container `/app/data`).
- Design doc: `../PROJECT_DESIGN.md` (decisions locked section). Memory files
  in `~/.claude/projects/-home-mrx-local-github-deep-read/memory/`.
- Rules: no em dashes; no agent co-author lines in commits; YAGNI; reuse
  existing patterns; E2E-verify before declaring done.
