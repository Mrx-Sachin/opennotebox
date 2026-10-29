"""Citation formatting, passage-level source splitting, and evidence provenance.

Provides shared utilities for turning retrieved chunks and fallback summaries into
independently numbered, passage-sized citeable sources, and for extracting citations
with exact evidence provenance from answer text.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

CITATION_MARKER_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

_ABBREV_PAT = re.compile(
    r"(?:"
    r"(?:Fig|Figure|Tab|Table|Eq|Equation|Sec|Section)\s+\d+"
    r"|\b\d+(?:\.\d+)*"
    r"|e\.g|i\.e|et\s+al|vs|cf|w\.r\.t|Mr|Mrs|Ms|Dr|Prof|No|p|pp|vol|dept|univ|al"
    r")\.\s*$",
    re.IGNORECASE,
)

_CAPTION_OR_HEADER_RE = re.compile(
    r"^(?:Fig(?:ure)?|Tab(?:le)?|Eq(?:uation)?|Sec(?:tion)?)\b",
    re.IGNORECASE,
)

MIN_PASSAGE_CHARS = 40
MAX_PASSAGE_CHARS = 1200


def _split_bounded_span(
    text: str,
    start: int,
    end: int,
    min_chars: int,
    max_chars: int,
) -> List[Tuple[int, int, str]]:
    """Split an oversized span into bounded exact slices without dropping non-space characters."""
    slices: List[Tuple[int, int, str]] = []
    curr = start
    while curr < end:
        while curr < end and text[curr].isspace():
            curr += 1
        if curr >= end:
            break

        limit = min(curr + max_chars, end)
        if limit < end:
            sp = -1
            for i in range(limit, curr + min_chars - 1, -1):
                if text[i].isspace():
                    sp = i
                    break
            if sp != -1:
                sub_e = sp
                next_curr = sp + 1
            else:
                sub_e = limit
                next_curr = limit
        else:
            sub_e = limit
            next_curr = limit

        while sub_e > curr and text[sub_e - 1].isspace():
            sub_e -= 1

        if sub_e > curr:
            slices.append((curr, sub_e, text[curr:sub_e]))
        curr = next_curr

    return slices


def find_passage_spans(
    text: str,
    min_chars: int = MIN_PASSAGE_CHARS,
    max_chars: int = MAX_PASSAGE_CHARS,
) -> List[Tuple[int, int, str]]:
    """Find sentence/passage span boundaries directly in the original text.

    Returns a list of (start, end, span_text) tuples where span_text is an exact
    substring slice text[start:end] without manufactured whitespace, handling
    single-newline line-wrapped parser output.
    """
    clean_text = text or ""
    if not clean_text.strip():
        return []

    spans: List[Tuple[int, int, str]] = []
    start = 0
    pattern = re.compile(r"(?<!\.)([.!?][\"\'\)\]]?)(?:\s+|$)")

    for match in pattern.finditer(clean_text):
        end_punct = match.end(1)
        prefix = clean_text[start:end_punct]

        if _ABBREV_PAT.search(prefix):
            continue
        if re.search(r"\b[A-Za-z]\.$", prefix):
            continue

        s = start
        e = end_punct
        while s < e and clean_text[s].isspace():
            s += 1
        while e > s and clean_text[e - 1].isspace():
            e -= 1

        span_candidate = clean_text[s:e].strip()
        prefix_is_caption = bool(_CAPTION_OR_HEADER_RE.match(span_candidate))
        remaining_text = clean_text[match.end() :].lstrip()
        next_is_caption = bool(_CAPTION_OR_HEADER_RE.match(remaining_text))

        if (
            (e - s) < min_chars
            and match.end() < len(clean_text)
            and not prefix_is_caption
            and not next_is_caption
        ):
            continue

        if e > s:
            if (e - s) > max_chars:
                spans.extend(_split_bounded_span(clean_text, s, e, min_chars, max_chars))
            else:
                spans.append((s, e, clean_text[s:e]))
        start = match.end()

    if start < len(clean_text):
        s = start
        e = len(clean_text)
        while s < e and clean_text[s].isspace():
            s += 1
        while e > s and clean_text[e - 1].isspace():
            e -= 1
        if e > s:
            if (e - s) > max_chars:
                spans.extend(_split_bounded_span(clean_text, s, e, min_chars, max_chars))
            else:
                spans.append((s, e, clean_text[s:e]))

    return spans


def build_passage_sources(
    hits: List[Dict[str, Any]],
    titles: Dict[int, str],
    max_budget: int = 14_000,
    fallback_summaries: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """Convert retrieved chunks and summaries into numbered, passage-sized sources.

    Each document chunk is split into independently numbered passages before
    prompting the answer model. Genuine document passages carry verified evidence
    as exact original text slices. Fallback summaries carry no exact evidence.
    Summary fallback decision is based on retrieved chunk coverage (len(hits) < 2).
    """
    sources: List[Dict[str, Any]] = []
    used = 0

    for h in hits:
        doc_id = h.get("doc_id")
        doc_title = titles.get(doc_id, "Unknown")
        page = h.get("page")
        chunk_text = h.get("text", "")
        chunk_id = h.get("chunk_id")
        source_type = h.get("source_type", "document")
        is_document = source_type == "document" and page is not None

        if is_document:
            spans = find_passage_spans(chunk_text)
            passages = [span_text for _, _, span_text in spans]
            if not passages and chunk_text.strip():
                passages = [chunk_text.strip()]
        else:
            passages = [chunk_text.strip()] if chunk_text.strip() else []

        for passage in passages:
            if not passage:
                continue
            if used + len(passage) > max_budget:
                continue
            used += len(passage)
            sources.append(
                {
                    "n": len(sources) + 1,
                    "doc_id": doc_id,
                    "doc_title": doc_title,
                    "page": page,
                    "chunk_id": chunk_id,
                    "text": passage,
                    "source_type": source_type,
                    "evidence_text": passage if is_document else None,
                }
            )

    if len(hits) < 2 and fallback_summaries:
        for s in fallback_summaries:
            text = s.content[:2000].strip() if hasattr(s, "content") else str(s).strip()
            if not text or used + len(text) > max_budget:
                break
            used += len(text)
            doc_id = getattr(s, "document_id", None)
            page_num = getattr(s, "page_number", None)
            sources.append(
                {
                    "n": len(sources) + 1,
                    "doc_id": doc_id,
                    "doc_title": titles.get(doc_id, "Unknown"),
                    "page": page_num,
                    "text": text,
                    "source_type": "summary",
                    "evidence_text": None,
                }
            )

    return sources


def format_source_line(s: Dict[str, Any]) -> str:
    """Render a numbered source for the LLM prompt."""
    if s.get("page") is None:
        return f"[{s['n']}] ({s['doc_title']})\n{s['text']}"
    return f"[{s['n']}] ({s['doc_title']}, p. {s['page']})\n{s['text']}"


def used_citations(content: str, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter sources to those cited as [n] and format backward-compatible citations.

    When a citation corresponds to a verified original document passage, the
    'evidence' field with {'exact': str} is included. For synthetic or unverified
    sources (such as summaries or graph-only facts), 'evidence' is omitted.
    """
    by_n = {s["n"]: s for s in sources}
    cited: List[int] = []

    for match in CITATION_MARKER_RE.finditer(content):
        for raw in match.group(1).split(","):
            raw_clean = raw.strip()
            if raw_clean.isdigit():
                n = int(raw_clean)
                if n in by_n and n not in cited:
                    cited.append(n)

    result: List[Dict[str, Any]] = []
    for n in cited:
        s = by_n[n]
        evidence_text = s.get("evidence_text")

        if evidence_text:
            excerpt = evidence_text
        elif s.get("source_type") == "summary" or s.get("page") is None:
            excerpt = ""
        else:
            excerpt = s.get("text", "")[:120].strip()

        cit: Dict[str, Any] = {
            "n": n,
            "doc_id": s["doc_id"],
            "doc_title": s["doc_title"],
            "page": s.get("page"),
            "excerpt": excerpt,
        }
        if evidence_text:
            cit["evidence"] = {"exact": evidence_text}

        result.append(cit)

    return result
