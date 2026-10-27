"""AI summarizer service.

Reuses LangGraph patterns and prompt system from open-notebook's architecture.
Handles page-level and section-level summarization via remote Ollama.
"""
import asyncio
import re
from typing import List, Optional

from loguru import logger
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.services.prompts import render_jinja
from app.services.llm_semaphore import background_llm_semaphore

# Phrases that mean the model refused/misunderstood the summarization request
# rather than producing an actual summary. Content matching these must never be
# persisted as a summary - it used to leak through verbatim, e.g. a real stored
# row: "I cannot fulfill this request because the provided text does not
# contain a section titled 'Remaining'".
REFUSAL_PATTERNS = (
    "i cannot fulfill",
    "i can't fulfill",
    "i cannot fulfil",
    "i am sorry",
    "i'm sorry",
    "the provided text does not contain",
)


def is_refusal(text: Optional[str]) -> bool:
    """True if text looks like an LLM refusal/non-answer rather than a summary."""
    if not text:
        return False
    lowered = text.strip().lower()
    return any(pattern in lowered for pattern in REFUSAL_PATTERNS)


class SummarizerService:
    """Generates page-level and section-level summaries via Ollama."""

    def _make_model(self, user_id: Optional[int] = None) -> ChatOpenAI:
        """Build a client for user_id's currently active LLM backend (see
        services/llm_settings.py). Built fresh per call, not cached on self,
        so switching the active backend in Settings takes effect within the
        ~15s llm_settings cache window without restarting the process.

        user_id must come from the document being summarized (its owner),
        threaded in by every caller here. Omitting it does not fall back to
        "the deployment default" - it falls back to _seed_user_prefs()'s
        hardcoded default, silently ignoring whatever backend the user
        actually selected in Settings. This was the same bug fixed in
        chat_engine.py; summaries/podcasts/flashcards had it too.
        """
        from app.services.llm_settings import get_active_llm

        active = get_active_llm(user_id)
        return ChatOpenAI(
            base_url=active["base_url"],
            model=active["model"],
            api_key=active["api_key"],
            temperature=0.3,
        )

    async def summarize_page(
        self, page_content: str, max_output_words: int = 450, user_id: Optional[int] = None
    ) -> str:
        """Generate a guided-teaching explanation of a single page (see
        prompts.py summary/page: plain-language, rich, Markdown-formatted -
        not a compression)."""
        if not page_content or len(page_content.strip()) < 20:
            return ""

        system_prompt = (
            "You are a brilliant, experienced teacher who knows this book deeply and teaches it "
            "page by page. Respond in clean Markdown."
        )
        user_prompt = render_jinja(
            "summary/page",
            # ~15k chars is still thousands of tokens of real page text; the
            # old 40k cap mostly shipped whitespace, headers and footers on
            # dense books and burned input budget for no quality gain.
            content=page_content[:15000],
            max_words=max_output_words,
        )

        try:
            async with background_llm_semaphore():
                response = await self._make_model(user_id).ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt)
                ])
            if is_refusal(response.content):
                logger.warning(f"Refusal-shaped page summary discarded: {response.content[:160]!r}")
                return ""
            return response.content
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            raise

    async def summarize_section(
        self,
        section_content: str,
        section_title: str,
        max_output_words: int = 800,
        user_id: Optional[int] = None,
    ) -> str:
        """Generate a guided-teaching explanation of a section spanning
        multiple pages (see prompts.py summary/section)."""
        if not section_content or len(section_content.strip()) < 20:
            return ""

        system_prompt = (
            "You are a brilliant, experienced teacher who knows this book deeply and teaches it "
            "section by section. Respond in clean Markdown."
        )
        user_prompt = render_jinja(
            "summary/section",
            content=section_content[:15000],
            section_title=section_title or "this section",
            max_words=max_output_words,
        )

        try:
            async with background_llm_semaphore():
                response = await self._make_model(user_id).ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt)
                ])
            if is_refusal(response.content):
                logger.warning(
                    f"Refusal-shaped section summary discarded for '{section_title}': "
                    f"{response.content[:160]!r}"
                )
                return ""
            return response.content
        except Exception as e:
            logger.error(f"Section summarization failed: {e}")
            raise

    async def summarize_pages_batch(
        self, pages: List[str], batch_size: int = 10, user_id: Optional[int] = None
    ) -> List[str]:
        """Summarize multiple pages in parallel batches."""
        results = [""] * len(pages)

        for start in range(0, len(pages), batch_size):
            batch = pages[start:start + batch_size]
            tasks = [self.summarize_page(page, user_id=user_id) for page in batch]
            batch_results = await asyncio.gather(*tasks)
            for i, result in enumerate(batch_results):
                results[start + i] = result

        return results

    async def detect_sections(self, pages_text: List[str], pages_headings: List[Optional[list]]) -> List[dict]:
        """Detect document sections based on headings. Kept for backward
        compatibility; delegates to the synchronous heuristic below."""
        return self._detect_sections_heuristic(pages_text, pages_headings)

    def _detect_sections_heuristic(
        self,
        pages_text: List[str],
        pages_headings: List[Optional[list]],
        page_numbers: Optional[List[int]] = None,
    ) -> List[dict]:
        """Detect document sections from per-page heading metadata produced by
        extraction. This is a coarse fallback: for documents whose extractor
        didn't tag headings well (e.g. a 462-page scanned/typeset book), this
        collapses to a single "Full Document" section - use the PDF outline/TOC
        via detect_pdf_outline_sections first when available, it is far more
        reliable for real books.

        page_numbers, if given, maps loop index i -> actual 1-based page_number
        (pages aren't guaranteed to be a contiguous 0..N-1 range).
        """
        def pn(i: int) -> int:
            return page_numbers[i] if page_numbers else i

        sections = []
        current_section = None
        current_content = []

        for i, (text, headings) in enumerate(zip(pages_text, pages_headings)):
            page_headings = headings or []

            for heading in page_headings:
                title = getattr(heading, "text", str(heading)) if hasattr(heading, "text") else str(heading)
                level = getattr(heading, "level", 1) if hasattr(heading, "level") else 1

                if current_section and level <= getattr(current_section, "level", 1):
                    if current_content:
                        sections.append({
                            "title": current_section.get("title", str(current_section)),
                            "content": "\n".join(current_content),
                            "start_page": current_section.get("start_page", pn(i)),
                            "end_page": pn(i),
                        })
                    current_content = []

                current_section = heading
                if hasattr(current_section, "get"):
                    current_section_data = {"title": title, "level": level, "start_page": pn(i)}
                else:
                    current_section_data = {"title": str(heading), "level": 1, "start_page": pn(i)}
                    current_section = current_section_data

            current_content.append(text)

        if current_content:
            if current_section is None:
                # No heading was ever detected before this trailing content -
                # it's the whole document, not a named section. Using a
                # placeholder like "Remaining" here used to leak into the LLM
                # prompt as if it were a real section title, confusing the model.
                title = "Full Document"
            else:
                title = current_section.get("title", str(current_section)) if hasattr(current_section, "get") else "Full Document"
            sections.append({
                "title": title,
                "content": "\n".join(current_content),
                "start_page": current_section.get("start_page", pn(0)) if hasattr(current_section, "get") else pn(0),
                "end_page": pn(len(pages_text) - 1),
            })

        if not sections and pages_text:
            sections.append({
                "title": "Full Document",
                "content": "\n".join(pages_text),
                "start_page": pn(0),
                "end_page": pn(len(pages_text) - 1),
            })

        return sections

    def detect_pdf_outline_sections(self, file_path: str, num_pages: int) -> List[dict]:
        """Detect chapter-level sections from a PDF's embedded outline/TOC
        (bookmarks), using level-0 AND level-1 entries as section boundaries.
        Level 0 entries are typically "Parts" (each spanning many chapters);
        level 1 entries are the chapters within them. Far more reliable than
        heading heuristics for typeset books, where extraction heading
        detection often collapses hundreds of pages into a single section.
        Returns [] if the PDF has no usable outline, so callers can fall back
        to the heuristic.

        Each returned dict has a "level" key (0 or 1) so callers/consumers
        can indent chapters under their parent part. A level-0 entry's
        end_page still spans its whole part (until the next level-0 entry);
        a level-1 entry's end_page ends at the next entry of level <= 1
        (i.e. the next chapter or the next part, whichever comes first).
        """
        try:
            import pypdfium2 as pdfium
        except ImportError:
            logger.warning("pypdfium2 not installed, cannot read PDF outline")
            return []

        entries = []
        try:
            pdf = pdfium.PdfDocument(file_path)
            try:
                bookmarks = list(pdf.get_toc())
                # Title/dest must be read while the underlying PdfDocument is
                # still open - each PdfBookmark holds a reference into it, and
                # reading from one after pdf.close() segfaults the native lib.
                for bm in bookmarks:
                    level = getattr(bm, "level", None)
                    if level not in (0, 1):
                        continue
                    try:
                        title = (bm.get_title() or "").strip()
                    except Exception:
                        continue
                    if not title:
                        continue
                    try:
                        dest = bm.get_dest()
                        page_index = dest.get_index() if dest else None
                    except Exception:
                        page_index = None
                    if page_index is None:
                        continue
                    entries.append({"title": title, "level": level, "start_page": page_index + 1})
            finally:
                pdf.close()
        except Exception as e:
            logger.warning(f"PDF outline extraction failed for {file_path}: {e}")
            return []

        top_level_count = sum(1 for e in entries if e["level"] == 0)
        if top_level_count < 2:
            # A single top-level entry (or none) isn't a useful chapter list -
            # let the caller fall back to the heading heuristic.
            return []

        # Entries as returned by get_toc() are already in document order.
        sections = []
        for i, entry in enumerate(entries):
            end_page = num_pages
            for later in entries[i + 1:]:
                if later["level"] <= entry["level"]:
                    end_page = max(later["start_page"] - 1, entry["start_page"])
                    break
            sections.append({
                "title": entry["title"],
                "level": entry["level"],
                "start_page": entry["start_page"],
                "end_page": end_page,
            })
        return sections

    # Research-paper section headings that appear without a number prefix.
    # Matched case-insensitively against a full line (optionally ending in ':').
    _NAMED_SECTION_RE = re.compile(
        r"^(?P<title>"
        r"Abstract|Introduction|Related\s+Work|Background|Motivation|"
        r"Method(?:s|ology)?|Approach|Model(?:s)?|Architecture|"
        r"Experiment(?:s)?|Evaluation|Results?|Discussion|"
        r"Conclusion(?:s)?|Future\s+Work|Acknowledg(?:e)?ments?|"
        r"References|Bibliography|Appendix(?:\s+[A-Z0-9]+)?"
        r")\s*:?\s*$",
        re.IGNORECASE,
    )
    # Numbered sections: "1 Introduction", "1. Introduction", "2.1 Background",
    # "A. Appendix Title". Rejects chart debris ("80 TG MT") and table rows
    # ("28 Serbian") via post-match filters below.
    _NUMBERED_SECTION_RE = re.compile(
        r"^(?P<num>(?:\d{1,2}(?:\.\d{1,2}){0,2}|[A-Z])\.?)\s+"
        r"(?P<title>[A-Z][A-Za-z][\w\s\-/,&:]{0,60})$"
    )
    # Single-word titles that are real paper sections (not language-table rows).
    _KNOWN_SECTION_WORDS = frozenset({
        "abstract", "introduction", "background", "motivation", "approach",
        "method", "methods", "methodology", "model", "models", "architecture",
        "experiments", "experiment", "evaluation", "results", "result",
        "discussion", "conclusion", "conclusions", "references", "bibliography",
        "acknowledgments", "acknowledgements", "appendix", "overview",
        "analysis", "ablations", "limitations", "related", "future",
        "preliminaries", "setup", "implementation", "baseline", "baselines",
    })

    def detect_research_paper_sections(
        self,
        pages_text: List[str],
        page_numbers: Optional[List[int]] = None,
    ) -> List[dict]:
        """Detect sections in research papers that lack a PDF outline.

        Scans each page's text for:
          - Named headings (Abstract, Introduction, References, ...)
          - Numbered headings (1 Introduction, 2.1 Background, A Appendix)

        Returns [] if fewer than 2 sections are found so callers can fall back.
        """
        def pn(i: int) -> int:
            return page_numbers[i] if page_numbers else i + 1

        hits: List[dict] = []
        seen_titles: set = set()

        for i, text in enumerate(pages_text):
            if not text:
                continue
            for raw_line in text.split("\n"):
                line = raw_line.strip()
                if not line or len(line) > 100:
                    continue

                title = None
                level = 0

                named = self._NAMED_SECTION_RE.match(line)
                if named:
                    title = named.group("title").strip()
                    # Normalize casing: "ABSTRACT" / "abstract" -> "Abstract"
                    title = title.title() if title.lower() != "abstract" else "Abstract"
                    if title.lower() == "acknowledgements":
                        title = "Acknowledgments"
                    level = 0
                else:
                    numbered = self._NUMBERED_SECTION_RE.match(line)
                    if numbered:
                        num = numbered.group("num").rstrip(".")
                        rest = numbered.group("title").strip()
                        words = rest.split()
                        if not (1 <= len(words) <= 10):
                            continue
                        if rest.endswith(".") and len(words) > 4:
                            continue

                        alpha_words = [
                            w for w in words if re.search(r"[A-Za-z]", w)
                        ]
                        if not alpha_words:
                            continue
                        # Chart labels are ALL-CAPS codes ("EL TRAR", "TG MT").
                        if all(w.isupper() for w in alpha_words):
                            continue
                        # Need at least one word with 3+ letters.
                        if not any(
                            len(re.sub(r"[^A-Za-z]", "", w)) >= 3
                            for w in alpha_words
                        ):
                            continue

                        parts = num.split(".")
                        major = parts[0]
                        # Papers almost never have top-level section 15+;
                        # those are nearly always table/figure numbers.
                        # Section 0 / 0.x is never used - it's table debris.
                        if major.isdigit() and (int(major) > 12 or int(major) == 0):
                            continue
                        # "1.0 Haitian Creole" style table rows use .0 minor.
                        if len(parts) >= 2 and parts[1] == "0":
                            continue
                        # Bibliography lines: "N Artie bias corpus: An open..."
                        if ":" in rest and len(rest) > 40:
                            continue

                        # Single unknown word ("28 Serbian", "5 SD") is table
                        # debris unless it's a known section name.
                        if len(words) == 1:
                            w0 = re.sub(r"[^A-Za-z]", "", words[0]).lower()
                            if w0 not in self._KNOWN_SECTION_WORDS:
                                continue

                        # Letter-only markers are appendices; require an
                        # appendix-ish keyword or a short descriptive title
                        # (2-4 words), e.g. "B Compared Models".
                        if major.isalpha() and len(major) == 1:
                            lower_words = {w.lower() for w in words}
                            appendix_ok = lower_words & {
                                "appendix", "supplementary", "supplement",
                                "proof", "derivation", "details", "compared",
                                "models", "tables", "hyperparameters",
                                "statistics", "standardization", "raw",
                                "performance", "training", "dataset",
                                "additional", "results", "analysis", "text",
                            }
                            if not appendix_ok and not (2 <= len(words) <= 4):
                                continue
                            if not appendix_ok and len(words) == 1:
                                continue

                        title = f"{num} {rest}"
                        level = 1 if "." in num else 0

                if not title:
                    continue

                key = title.lower()
                # Skip near-duplicates (e.g. "1 Introduction" then "Introduction")
                if key in seen_titles:
                    continue
                # Also skip if we already have a numbered form of the same name
                bare = re.sub(r"^[\dA-Z.]+\s+", "", key)
                if bare and any(bare == re.sub(r"^[\dA-Z.]+\s+", "", t) for t in seen_titles):
                    continue

                seen_titles.add(key)
                hits.append({
                    "title": title,
                    "level": level,
                    "start_page": pn(i),
                })

        if len(hits) < 2:
            return []

        num_pages = page_numbers[-1] if page_numbers else len(pages_text)
        sections = []
        for i, entry in enumerate(hits):
            end_page = num_pages
            for later in hits[i + 1:]:
                if later["level"] <= entry["level"]:
                    end_page = max(later["start_page"] - 1, entry["start_page"])
                    break
            sections.append({
                "title": entry["title"],
                "level": entry["level"],
                "start_page": entry["start_page"],
                "end_page": end_page,
            })
        return sections

    def detect_sections_metadata(
        self,
        pages_text: List[str],
        pages_headings: List[Optional[list]],
        page_numbers: Optional[List[int]] = None,
        file_path: Optional[str] = None,
        fmt: Optional[str] = None,
    ) -> List[dict]:
        """Detect (title, start_page, end_page) for a document's sections
        without generating any content, so the chapter list can be shown
        immediately after extraction. Tries, in order:
          1. The PDF's embedded outline/TOC (only for fmt == "pdf")
          2. Research-paper regex (Abstract / 1. Intro / References)
          3. Heading-heuristic detection from extraction
          4. A single "Full Document" fallback covering everything
        """
        num_pages = (page_numbers[-1] if page_numbers else len(pages_text))

        if fmt == "pdf" and file_path:
            outline_sections = self.detect_pdf_outline_sections(file_path, num_pages)
            if outline_sections:
                return outline_sections

        # Regex heading scan over raw page text is tuned for PDF papers with
        # no embedded outline. For formats that already carry real per-page
        # headings (epub chapter titles from the TOC, docx Heading styles),
        # it does more harm than good: chapter body prose routinely contains
        # a bare "Introduction" or "1 Some Subsection" line that isn't a
        # navigable heading at all, and those false positives were shadowing
        # the correct heuristic below entirely.
        if fmt == "pdf":
            paper_sections = self.detect_research_paper_sections(pages_text, page_numbers)
            if paper_sections:
                return paper_sections

        heuristic = self._detect_sections_heuristic(pages_text, pages_headings, page_numbers)
        if heuristic and not (len(heuristic) == 1 and heuristic[0]["title"] == "Full Document"):
            return [
                {"title": s["title"], "level": 0, "start_page": s["start_page"], "end_page": s["end_page"]}
                for s in heuristic
            ]

        if pages_text:
            return [{
                "title": "Full Document",
                "level": 0,
                "start_page": page_numbers[0] if page_numbers else 0,
                "end_page": num_pages,
            }]

        return []

    def generate_outline(self, headings: List[dict]) -> str:
        """Generate a document outline from detected headings."""
        if not headings:
            return "No structure detected"
        lines = []
        for h in headings:
            title = h.get("title", str(h))
            level = h.get("level", 1)
            indent = "  " * (level - 1)
            lines.append(f"{indent}- {title}")
        return "\n".join(lines)


summarizer = SummarizerService()
