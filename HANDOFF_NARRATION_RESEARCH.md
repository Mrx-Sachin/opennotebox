# Narration research: making TTS sound like an audiobook, not a text dump

Scope: research feed for a follow-up implementation decision. No code changed.

## What's already handled (do not re-propose)

- **`backend/app/services/pdf_parser.py`** (extraction-time, feeds chat/search too, so it stays conservative):
  running headers/footers, bare page numbers, rotated chart-axis labels, words inside
  dense vector-drawing regions (keeps `Figure`/`Table`/`Algorithm`/`Listing` captions),
  axis-tick/legend-code row junk, reversed rotated tokens. Two-column reading-order
  reconstruction via gutter detection (histogram + row-gap consensus). Philosophy is
  explicit in the `MIN_COLUMN_ROWS` comment: false positives that eat real content are
  worse than false negatives that leave noise in.
- **`frontend/src/lib/narrationFilter.ts`** (narration-only, sentence-list filter right
  before TTS, safe to be more aggressive): drops bare page-number lines, TOC dot-leader
  lines (`Chapter 4 .......... 57`), numeric-table-row-shaped sentences (mostly
  numbers/currency/percent tokens with no real word).
- **`backend/app/services/summarizer.py`**: `detect_research_paper_sections` already
  regex-detects Abstract/Introduction/.../References/Acknowledgments/Appendix headings
  and returns page ranges. Not currently consumed by narration at all - this is the key
  building block for section-aware skipping (item 1 below).

None of the above currently touch: inline citations, footnote markers, running paper
titles baked into a Table row rather than the header band, DOIs/arXiv IDs, author
affiliation blocks, or math-shaped text. Those are the real gaps.

---

## Industry practice (what existing tools do)

- **NaturalReader** ships an "AI Text Filter" that explicitly skips citations, reference
  numbers, and other distracting text for academic listening. ([naturalreaders.com](https://www.naturalreaders.com/usecase/researcher.html))
- **Speechify** lets users configure skipping of headers, footers, brackets, braces,
  parentheses, and URLs, but reviewers note it lacks a clean footnote/sidebar filter
  compared to academic-focused tools. ([speechify.com](https://speechify.com/blog/how-to-skip-content-such-as-headers-footers-brackets-etc-on-the-computer/), [fahimai.com](https://www.fahimai.com/speechify-vs-natural-reader))
- **Listening.com** (research-paper-focused TTS) describes its detection approach in
  prose: recognize author-date parentheticals like `(Smith, 2020)`, spot bracketed or
  superscript numbers tied to a reference list, and use surrounding context to
  distinguish citations from prose. Citations are **silently dropped**, not spoken or
  rewritten - the stated goal is "distraction-free" listening, not fidelity. It also
  supports skipping footnotes and the references section, and offers header/footer
  skipping as a toggle. No mention of math/equation handling at all. ([listening.com](https://www.listening.com/blog/skipping-research-citations-in-audio-using-text-to-speech), [listening.com](https://www.listening.com/blog/skip-academic-references-in-text-to-speech))
- **CastReader** takes the blunt approach of just recommending the user manually stop
  playback once they hit the bibliography, since the last several pages of a paper are
  "nothing but citations." ([castreader.com](https://castreader.com/blog/turn-pdfs-and-research-papers-into-audio-2026))
- **pdf2speech** (open-source, Kokoro TTS backend) runs a regex cleaning pipeline before
  synthesis that strips citations, math expressions, figure references, page numbers,
  and email addresses, fixes hyphen-broken line wraps, and announces section headings
  when it crosses one. It also exposes a `--no-references` flag to stop entirely before
  the bibliography when fed an arXiv URL. The specific regexes aren't published, but the
  category list (citations / math / figure-refs / page numbers / emails) matches what
  this report proposes below. ([github.com/soumyasj/pdf2speech](https://github.com/soumyasj/pdf2speech))
- **PaperWave** (CHI 2025 research prototype) goes a level further: it uses an LLM to
  *rewrite* the paper into a conversational podcast script rather than filtering the
  literal extracted text. That implies citations there are paraphrased away entirely
  ("as shown by prior work...") rather than spoken verbatim or silently dropped - but
  the public materials don't give the citation-handling prompt specifically, and this
  approach requires an LLM pass per page/section, not a regex filter. ([arxiv.org/abs/2410.15023](https://arxiv.org/abs/2410.15023), [dl.acm.org](https://dl.acm.org/doi/10.1145/3706599.3706664))
- **Consensus across every product surveyed**: nobody rewrites `[12]` into "reference
  twelve" or `(Smith et al., 2023)` into "as shown by Smith and colleagues in 2023" via
  rules. Silent dropping is the universal default for rule-based tools. Spoken-form
  rewriting only shows up in LLM-scripted approaches (PaperWave), which is a different
  and much heavier architecture than anything else in this codebase.

## Academic/open-source structural parsing prior art

- **GROBID**: mature ML pipeline (cascading CRF/sequence-labeling models), turns a PDF
  into structured TEI-XML with title/authors/abstract/sections/references separated out,
  and does full reference *parsing* (not just detection) at ~0.87 F1 on PubMed Central.
  Runs as a Java service, 2-5 sec/page. ([grobid.readthedocs.io via medevel.com](https://medevel.com/grobid-ai/), [github.com/grobidOrg/grobid](https://github.com/grobidOrg/grobid))
- **Nougat**: vision-transformer OCR-style model, does screenshot-to-Markdown for
  academic documents including math -> LaTeX. Complementary to GROBID: strong on math
  and layout, weaker/not focused on bibliographic metadata. ([arxiv.org/abs/2308.13418](https://arxiv.org/pdf/2308.13418))
- **Verdict for this codebase**: overkill for now. Standing up GROBID means running a
  second JVM service alongside the current pdfplumber/OpenDataLoader pipeline, adding a
  new failure mode (service down -> no narration filtering at all, or a hard dependency),
  and re-plumbing the extraction pipeline that already works well enough for chat/search.
  The realistic near-term path is deepening the two purpose-built regex layers already in
  the codebase (`pdf_parser.py` word filtering, `narrationFilter.ts` sentence filtering),
  which is exactly the same "structured skip-list, not full re-parse" strategy every
  commercial rule-based tool above uses. Revisit GROBID only if regex citation/footnote
  stripping proves to have an unacceptable false-positive rate in practice - a real risk
  given citation styles vary a lot (numeric `[12]`, superscript, author-year, footnote
  markers `¹`) and there's no existing corpus of real-world failures to tune against yet.

---

## Prioritized gaps - general books/EPUB

Books/EPUB are the easy case; the user didn't flag it as a problem, but two items apply
generally.

### 1. Page-number-only artifacts that slip past word-level filtering
Already handled at both layers (pdf_parser.py header/footer band + narrationFilter.ts
`PAGE_NUMBER_RE`). No further work needed here for books.

### 2. Footnote markers mid-sentence (e.g. "the theory¹ was later revised")
- **What**: superscript footnote/endnote reference numbers embedded inline in body
  prose. Rare in novels, more common in nonfiction/annotated editions.
- **Detection**: pdfplumber word dicts don't reliably expose font-size deltas after
  the current extraction path discards font metadata early; a real fix would need the
  `size`/`top`-offset per char to detect superscript, which the row-grouping step
  currently doesn't preserve down to `narrationFilter.ts`. At the sentence-string layer,
  a cheap regex catches the common case: a single digit or `[0-9]{1,3}` immediately
  after a word with no space and no sentence-ending punctuation before it, e.g.
  `/([a-z])(\d{1,3})\b/g` -> strip the trailing digits only when not preceded by
  a currency/unit context. This is narration-only text surgery (not full-sentence
  drop), so it's a different shape from the rest of `narrationFilter.ts`'s filters.
- **Effort**: medium (regex is small, but distinguishing "word2" footnote refs from
  legitimate alphanumeric tokens like model names "GPT4" or chemical formulas needs
  care).
- **Failure mode**: under-aggressive (miss some footnote digits) is safe - a few stray
  digits read aloud occasionally is mildly annoying, not content-destroying. Over-
  aggressive (strip a real trailing digit, e.g. "Room 12" -> "Room") would mangle
  content. Given the risk asymmetry, ship this narration-side only and keep the
  regex narrow (single 1-2 digit trailing number, word must be 4+ letters).
- **Location**: `narrationFilter.ts` only.

### 3. EPUB endnote/footnote link markers ("[1]", "↩") rendered as literal text
- **What**: some EPUB footnote implementations render the anchor text (bracket number
  or return arrow) inline in the extracted content string.
- **Detection**: same bracket-number regex as citation stripping below (item 4), so no
  separate work if that's implemented - EPUB and PDF converge on the same sentence-level
  filter.
- **Effort**: small (covered by item 4).
- **Failure mode**: shared with item 4.
- **Location**: `narrationFilter.ts`.

---

## Prioritized gaps - research papers specifically (the hard case)

Ranked by effort-vs-payoff, highest payoff first.

### 1. Skip the References/Bibliography section entirely (and Acknowledgments)
- **What**: once `detect_research_paper_sections` has located "References" (and
  "Bibliography"/"Acknowledgments"), narration for "read whole document" / "read
  section" should stop before it, or the section should be excluded from the played
  queue. This is the single highest-payoff change: a typical 10-page paper has 1-2 pages
  of pure bibliography that is unlistenable and currently read verbatim end to end.
  Every product surveyed treats this as table-stakes (Listening.com and CastReader both
  call it out explicitly).
- **Detection**: already done - `summarizer.detect_research_paper_sections` returns
  `start_page`/`end_page` for a "References" section with `level: 0`. Narration needs to
  consume this: when queuing pages for playback in "read document"/"read from here"
  mode, exclude the page range whose section title matches
  `/^(references|bibliography)$/i`, and optionally also `/^acknowledg(e)?ments?$/i`
  (real products differ here - Listening.com bundles acknowledgments with references as
  skippable; it's genuinely low-value narration content, mostly names and grant numbers).
  This is a queue-construction change, not a text filter - it belongs wherever pages are
  assembled into the sentence list for playback (likely `ttsEngine.ts` or wherever it
  calls into `narrationFilter.ts`), consuming section metadata that already exists.
- **Effort**: small-medium. The detection is done; the work is wiring section boundaries
  into the playback queue and (probably) a UI affordance ("skip references" toggle, or
  just default-on since the user explicitly wants audiobook-mode).
- **Failure mode**: if `detect_research_paper_sections` misdetects the References
  boundary (it can - regex heading detection, not ground truth), the risk is skipping
  actual body content that happens to follow a false "References" match, or reading
  bibliography that wasn't caught. Given the section detector already gates on "if fewer
  than 2 sections found, return []" and falls back safely, the practical fix is: only
  auto-skip when `detect_research_paper_sections` succeeded (non-empty) for this
  document, never guess independently in the narration layer. Under-skipping (still
  reading some bibliography) is the safe failure mode to bias toward if the boundary is
  fuzzy - trim a page early rather than risk cutting off a live Conclusion page.
- **Location**: consumes `summarizer.py` output; new logic lives in the frontend
  narration/playback queue construction (not `narrationFilter.ts`'s per-sentence filter,
  and not `pdf_parser.py` since chat/search must still be able to answer "what's citation
  12" - this must stay narration-only).

### 2. Inline citation stripping
- **What**: numeric citation markers `[12]`, `[12, 14]`, `[3-7]` and author-year
  parentheticals `(Smith et al., 2023)`, `(Smith & Jones, 2023)`, `(Smith, 2023; Jones,
  2024)` read aloud as literal bracket/paren noise ("bracket one two bracket") or, worse,
  pdfplumber-extracted glyphs turn `et al.` into a stray "et al" mid-sentence.
- **Detection** (regex, sentence-string level):
  - Numeric: `/\[\d{1,3}(?:\s*[-,]\s*\d{1,3})*\]/g` -> strip.
  - Author-year: `/\([A-Z][a-zA-Z-]+(?:\s+(?:et al\.?|&|and)\s+[A-Za-z.-]+)?,?\s*\d{4}[a-z]?\)/g`
    -> strip (also handle multi-citation `(Smith, 2023; Jones, 2024)` by matching
    semicolon-separated repeats inside one paren group).
  - Superscript-numeral citation markers rendered as plain digits immediately after a
    word (same pattern as EPUB footnote item 3 above) - only when isolated 1-2 digit
    clusters with no surrounding sentence structure.
  - After stripping, collapse resulting double-spaces and dangling punctuation
    (`"the result , as shown"` -> `"the result, as shown"`).
- **Effort**: medium. The regexes themselves are small, but tuning against false
  positives takes iteration - author-year patterns can collide with legitimate parenthetical
  asides that happen to contain a capitalized word and a number ("(version 2, released
  2023)" is NOT a citation but matches loosely). Needs a corpus of real paper pages to
  tune against before shipping.
- **Failure mode**: over-aggressive stripping deletes a real parenthetical aside from
  the narrated audio (annoying, not content-destroying since it's narration-only and
  the underlying stored text is untouched for chat/search). Under-aggressive leaves
  "bracket twelve" noise in, which is the status quo - acceptable fallback. Given the
  established philosophy (false positive on prose < false positive on stored content,
  but still costs trust), keep the author-year pattern conservative: require the
  4-digit year AND a capitalized name token, not just any parenthetical with a number.
- **Location**: `narrationFilter.ts` only - never `pdf_parser.py`. Chat/search and the
  citation-hover/lookup UX (if any exists) need the literal citation text intact.

### 3. DOIs, arXiv IDs, and URL strings
- **What**: `doi.org/10.1145/...`, `arXiv:2308.13418`, raw URLs in text (common in
  paper headers/footers and footnote text) read as unbearable character-by-character
  noise if TTS doesn't special-case them, and even a "smart" TTS pronunciation is still
  clutter nobody wants narrated in an audiobook-style pass.
  Speechify explicitly lists URL-skipping as one of its filters, confirming this is a
  recognized standalone win, not lumped into citation handling.
- **Detection**: `/\bhttps?:\/\/\S+/g`, `/\bdoi\.org\/\S+/gi`, `/\b10\.\d{4,9}\/\S+/g`
  (bare DOI), `/\barXiv:\d{4}\.\d{4,5}(v\d+)?/gi`. Straightforward, low collision risk -
  these patterns essentially never occur inside legitimate prose.
- **Effort**: small.
- **Failure mode**: very low risk either direction - these patterns are unambiguous.
  Worst case is a rare false match inside a footnote URL that was actually meaningful
  context, but URLs are never narration-worthy content regardless.
- **Location**: `narrationFilter.ts`. (Could also be pre-stripped in `pdf_parser.py`
  since a bare DOI/URL is arguably never useful for chat context either, but since the
  ask here is narration-specific and the existing architecture keeps a hard line between
  the two files, default to narration-only unless a chat-quality complaint surfaces
  independently.)

### 4. Author affiliation / title-page blocks (page 1 header clutter)
- **What**: the block of author names, university affiliations, email addresses, and
  sometimes a running "correspondence to:" line that sits below the paper title on page
  1. Currently only the *running header in the top margin band* is filtered
  (`HEADER_MARGIN_PT` in `pdf_parser.py`); a title-page affiliation block usually starts
  lower on the page and reads as body text.
- **Detection**: heuristic, page-1-specific, sentence-level: a short line (<80 chars)
  containing an `@`-email pattern, or a line matching
  `/university|institute|department|laboratory|corresponding author/i` combined with
  being within the first ~15 sentences of the document, or a line that's mostly
  comma-separated capitalized name tokens (`"Jane Smith, John Doe, Amit Patel"`) with no
  verb - similar shape to the existing `looksLikeTableRow` heuristic but for names
  instead of numbers.
- **Effort**: medium - name-block detection is fuzzier than numeric-table-row detection
  because there's no strong lexical signal like `NUMERIC_TOKEN_RE`; risk of matching a
  legitimate list-heavy sentence early in an Introduction.
- **Failure mode**: over-aggressive risks eating the actual paper title or abstract
  opening line if the heuristic is too loose about "first N sentences." Because this is
  fuzzier than the other regexes, ship it gated to only the literal page-1 content
  before the Abstract heading is detected (available since `detect_research_paper_sections`
  usually finds "Abstract" as the first hit) - i.e. only apply within the "no known
  section yet" window, which bounds the blast radius.
- **Location**: `narrationFilter.ts`, and it should consume section-boundary info from
  `summarizer.py` (same wiring as item 1) rather than guessing independently.

### 5. Math/equations - be honest, this is the hard one
- **What**: inline LaTeX-adjacent glyph soup and standalone equation blocks. pdfplumber
  extracts math as a jumble of single-letter tokens, operator glyphs, and misplaced
  Unicode math symbols (∑, ∂, ×, superscript/subscript numerals) with no semantic
  structure - there's no "this span is an equation" signal in the current word-level
  data, unlike the figure-region detection which has real geometric signal (vector
  curve density) to key off.
- **Detection - what's plausible vs not**:
  - **Plausible (heuristic, imprecise)**: flag a *row* as equation-shaped narration
    noise using signals similar to the existing `_is_figure_junk_row` machinery: high
    density of single-character tokens, high density of math/operator symbols
    (`/[=+\-*/<>≤≥∑∏∫∂∇±×÷√∞θαβγλμσΣΠ]/`), very few real 4+ letter words, tight
    horizontal packing typical of formula typesetting. This can be built into
    `pdf_parser.py`'s row filter following the exact template `_is_figure_junk_row`
    already uses, or as a narration-only sentence check similar to `looksLikeTableRow`.
  - **Not plausible without a much bigger investment**: recovering the actual
    mathematical meaning to narrate ("the integral of f of x with respect to x") -
    that requires OCR/structure models like Nougat or MathReader-style pipelines
    (arXiv:2501.07088 is literally a paper on TTS-for-math specifically because it's
    a hard, separate problem, not a regex-solvable one), or a math-aware layout parser.
    This codebase's pdfplumber-based extraction has no path to that without adding a
    new heavyweight dependency.
  - **Recommendation**: don't try to narrate math meaningfully. Detect equation-shaped
    rows/blocks and either (a) silently skip them narration-side, or (b) replace with a
    short spoken placeholder like "[equation omitted]" so the listener isn't confused by
    a sudden jump in the narrative (products like Listening.com don't mention doing this
    at all, suggesting even they just drop math silently as part of general noise
    filtering - no strong industry precedent for placeholder announcements, but it's a
    plausible product decision if the omitted content is substantial e.g. a whole
    equation block spanning several lines).
- **Effort**: medium for detection-and-skip; large (and arguably out of scope) for
  actually narrating math meaningfully.
- **Failure mode**: over-aggressive equation detection risks eating dense technical
  prose that's legitimately symbol-heavy (e.g. a sentence defining notation, "let x
  denote the input and y the output"), which reads fine as prose but might trip a
  naive symbol-density check. Keep the bar high (multiple math-symbol hits, not just
  one `=` in a sentence) and prefer narration-side (`narrationFilter.ts` or the
  pdf_parser.py row-level equivalent of `_is_figure_junk_row`) so a false positive
  doesn't touch stored/chat text. Given papers' math blocks are usually visually
  distinct rows (centered, isolated, preceded/followed by blank lines), the row-level
  geometric signal available in `pdf_parser.py` (position, isolation, symbol density)
  is actually a *better* fit than a pure sentence-string regex in `narrationFilter.ts`,
  even though it's narration-motivated - consider adding it as an opt-in aggressive rule
  gated by `layout_mode == "research_paper"` (same aggressive flag already used for
  page-number stripping) rather than sentence-string surgery after the fact, since row
  geometry is lost by the time text reaches `narrationFilter.ts`.

### 6. Figure/table caption over-reading
- **What**: not a gap exactly - captions are deliberately *kept* today (`_drop_words_
  in_figure_regions` explicitly preserves `Figure N. ...` / `Table N. ...` lines). But
  for audiobook-style narration, a listener arguably wants to *hear* "Figure 2 shows the
  model architecture" (context) without the full multi-sentence caption body that's only
  useful when looking at the image. This is a smaller, more subjective win - flagging for
  awareness, not proposing a specific regex, since "how much of a caption to keep" is a
  product judgment call, not a noise-detection problem like the others.
- **Effort/Location**: n/a - product decision, not immediately actionable; revisit only
  if user feedback specifically flags captions as a narration annoyance after the above
  ship.

---

## Suggested build order

1. Wire References/Acknowledgments section-skip into the playback queue (item 1) -
   biggest listening-time win, detection already exists, smallest net-new code.
2. Inline citation stripping + DOI/arXiv/URL stripping (items 2+3) - the two together
   cover the bulk of "reads like a robot" complaints on paper pages, both narration-only
   regex, no new dependency.
3. Footnote-digit / EPUB endnote-marker stripping (items 2-3 in the books section) -
   small, shared regex shape with citation stripping.
4. Author affiliation block skip (item 4) - medium effort, gate tightly to pre-Abstract
   page-1 content only.
5. Equation-row detection-and-skip (item 5) - do this last; it's the least precedented
   in the industry, needs the most tuning, and has the highest risk of collateral damage
   on symbol-dense technical prose. Ship only after the above are stable and you have
   real user feedback on false-positive tolerance.

Do not pursue GROBID/Nougat-style structural re-parsing unless the regex layer proves
insufficient in practice - it's a different architecture tier (standing service, new
failure modes) than anything else in this stack.
