"""Narration helpers for filtering and extracting text from parsed regions."""

import math
import re
from typing import Any, Dict, List, Optional

from loguru import logger

EXCLUDED_LABELS = {
    "page_header",
    "page_footer",
    "caption",
    "footnote",
    "picture",
    "chart",
    "table",
    "code",
    "formula",
}


def is_narratable_label(label: Optional[str]) -> bool:
    """Return True if label should be included in TTS narration.

    Titles, section headings, list items, body text, and unknown labels are
    kept. Running headers/footers, captions, footnotes, pictures, charts,
    tables, code, and formulas are excluded.
    """
    if not label:
        return True
    return label.strip().lower() not in EXCLUDED_LABELS


def utf16_len(text: str) -> int:
    """Return the length of text in UTF-16 code units (for JavaScript offset compatibility)."""
    if not text:
        return 0
    return len(text.encode("utf-16-le")) // 2


def char_to_utf16_offset(text: str, char_idx: int) -> int:
    """Convert Python string character index to UTF-16 code unit offset."""
    if char_idx <= 0:
        return 0
    if char_idx >= len(text):
        return utf16_len(text)
    return len(text[:char_idx].encode("utf-16-le")) // 2


def utf16_to_char_offset(text: str, utf16_offset: int) -> int:
    """Convert UTF-16 code unit offset to Python string character index."""
    if utf16_offset <= 0:
        return 0
    total_utf16 = utf16_len(text)
    if utf16_offset >= total_utf16:
        return len(text)

    # Fast path: if all characters up to utf16_offset are BMP (1 code unit)
    candidate = min(utf16_offset, len(text))
    if utf16_len(text[:candidate]) == utf16_offset:
        return candidate

    # Binary search for exact character index
    low = 0
    high = len(text)
    while low < high:
        mid = (low + high) // 2
        curr = utf16_len(text[:mid])
        if curr < utf16_offset:
            low = mid + 1
        else:
            high = mid
    return low


def get_narratable_text(
    content: Optional[str] = None,
    narration_exclusions: Optional[List[Dict[str, int]]] = None,
    layout_regions: Optional[List[Dict[str, Any]]] = None,
    bounding_boxes: Optional[List[Dict[str, Any]]] = None,
    fallback_content: Optional[str] = None,
) -> str:
    """Extract narratable text by slicing content around narration exclusions.

    Legacy content is sliced directly so that spoken text matches original
    words and line identity without Docling reconstructed text.
    If all narratable regions are excluded, returns empty string.
    If no exclusions are present, returns full content.
    """
    raw_text = content if content is not None else (fallback_content or "")
    if not raw_text or not raw_text.strip():
        return ""

    # Check if page has exclusions
    if narration_exclusions:
        # Sort and deduplicate / merge overlapping exclusions
        sorted_excl = sorted(
            narration_exclusions,
            key=lambda x: (x.get("start", 0), x.get("end", 0)),
        )
        merged_excl = []
        for exc in sorted_excl:
            start = max(0, exc.get("start", 0))
            end = max(start, exc.get("end", 0))
            if start == end:
                continue
            if not merged_excl:
                merged_excl.append((start, end))
            else:
                prev_start, prev_end = merged_excl[-1]
                if start <= prev_end:
                    merged_excl[-1] = (prev_start, max(prev_end, end))
                else:
                    merged_excl.append((start, end))

        # Build non-excluded slices
        slices: List[str] = []
        curr_utf16 = 0
        for exc_start, exc_end in merged_excl:
            if exc_start > curr_utf16:
                c_start = utf16_to_char_offset(raw_text, curr_utf16)
                c_end = utf16_to_char_offset(raw_text, exc_start)
                piece = raw_text[c_start:c_end]
                if piece.strip():
                    slices.append(piece.strip())
            curr_utf16 = max(curr_utf16, exc_end)

        total_utf16 = utf16_len(raw_text)
        if curr_utf16 < total_utf16:
            c_start = utf16_to_char_offset(raw_text, curr_utf16)
            piece = raw_text[c_start:]
            if piece.strip():
                slices.append(piece.strip())

        return "\n\n".join(slices)

    return raw_text


def compute_word_center(
    bbox: List[float],
    page_width: float = 612.0,
    page_height: float = 792.0,
) -> Optional[tuple[float, float]]:
    """Compute normalized (cx, cy) in [0, 1] range for a bounding box."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in bbox):
        return None
    x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    # Detect if coordinates are in points (> 1.0) or normalized (<= 1.0)
    if x1 > 1.0 or y1 > 1.0:
        pw = page_width if page_width > 0 else 612.0
        ph = page_height if page_height > 0 else 792.0
        cx = (x0 + x1) / (2.0 * pw)
        cy = (y0 + y1) / (2.0 * ph)
    else:
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
    return max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy))


def is_center_in_rects(
    cx: float,
    cy: float,
    rects: List[List[float]],
) -> bool:
    """Return True if (cx, cy) is strictly or boundary-inside any rectangle [l, t, r, b]."""
    for r in rects:
        if len(r) >= 4:
            rl, rt, rr, rb = r[0], r[1], r[2], r[3]
            # Ensure proper ordering of rect bounds
            left = min(rl, rr)
            right = max(rl, rr)
            top = min(rt, rb)
            bottom = max(rt, rb)
            if left <= cx <= right and top <= cy <= bottom:
                return True
    return False


def compute_narration_exclusions(
    content: str,
    words: Optional[List[Dict[str, Any]]],
    layout_regions: Optional[List[Dict[str, Any]]],
    page_width: float = 612.0,
    page_height: float = 792.0,
) -> List[Dict[str, int]]:
    """Compute UTF-16 character exclusion spans in content from excluded layout rectangles."""
    if not content or not content.strip() or not layout_regions:
        return []

    excluded_rects = [
        r["bbox"]
        for r in layout_regions
        if isinstance(r, dict)
        and r.get("narrate") is False
        and isinstance(r.get("bbox"), (list, tuple))
        and len(r["bbox"]) >= 4
    ]

    if not excluded_rects:
        return []

    if not words:
        return []

    # Find which words are inside excluded regions
    # Check if words already have known start/end offsets in content
    has_precomputed_spans = all(
        "start" in w and "end" in w for w in words
    )

    if has_precomputed_spans:
        excluded_spans: List[tuple[int, int]] = []
        for w in words:
            bbox = w.get("bbox", [])
            center = compute_word_center(bbox, page_width, page_height)
            if center is not None and is_center_in_rects(*center, excluded_rects):
                excluded_spans.append((int(w["start"]), int(w["end"])))
    else:
        # Align content tokens with words
        tokens = [
            (m.start(), m.end(), m.group(0))
            for m in re.finditer(r"\S+", content)
        ]
        excluded_spans = []

        if [token[2] for token in tokens] == [word.get("text") for word in words]:
            # 1:1 token sequence alignment
            for token, w in zip(tokens, words):
                bbox = w.get("bbox", [])
                center = compute_word_center(bbox, page_width, page_height)
                if center is not None and is_center_in_rects(*center, excluded_rects):
                    excluded_spans.append((token[0], token[1]))
        else:
            excluded_spans = _match_visual_lines(
                content, words, excluded_rects, page_width, page_height
            )

    if not excluded_spans:
        return []

    # Merge adjacent excluded spans separated only by whitespace
    merged: List[tuple[int, int]] = []
    excluded_spans.sort(key=lambda s: (s[0], s[1]))

    for s_start, s_end in excluded_spans:
        if not merged:
            merged.append((s_start, s_end))
        else:
            prev_start, prev_end = merged[-1]
            gap = content[prev_end:s_start]
            if s_start <= prev_end or not gap.strip():
                # Merge across whitespace
                merged[-1] = (prev_start, max(prev_end, s_end))
            else:
                merged.append((s_start, s_end))

    # Convert char offsets to UTF-16 code units
    exclusions: List[Dict[str, int]] = []
    for c_start, c_end in merged:
        u_start = char_to_utf16_offset(content, c_start)
        u_end = char_to_utf16_offset(content, c_end)
        if u_end > u_start:
            exclusions.append({"start": u_start, "end": u_end})

    return exclusions


def _match_visual_lines(content, words, excluded_rects, page_width, page_height):
    positioned = []
    for word_index, word in enumerate(words):
        bbox = word.get("bbox") or []
        center = compute_word_center(bbox, page_width, page_height)
        if center is None:
            continue
        scale_x = page_width if max(bbox) > 1 else 1
        scale_y = page_height if max(bbox) > 1 else 1
        positioned.append((bbox[1] / scale_y, bbox[0] / scale_x, word_index,
                           word.get("text", "").split(),
                           is_center_in_rects(*center, excluded_rects)))
    rows = []
    for top, left, word_index, text_tokens, excluded in sorted(positioned):
        if not rows or abs(top - rows[-1][0]) > 3 / page_height:
            rows.append((top, []))
        rows[-1][1].append((left, word_index, text_tokens, excluded))
    row_tokens = []
    for _, row in rows:
        row_tokens.append([
            (text, (word_index, token_index), excluded)
            for _, word_index, texts, excluded in sorted(row)
            for token_index, text in enumerate(texts)
        ])
    used = set()
    excluded_spans = []
    unmatched = 0
    for line in re.finditer(r"[^\r\n]+", content):
        tokens = list(re.finditer(r"\S+", line.group()))
        if not tokens:
            continue
        needle = [token.group() for token in tokens]
        matches = []
        for row in row_tokens:
            for start in range(len(row) - len(needle) + 1):
                candidate = row[start:start + len(needle)]
                if [item[0] for item in candidate] == needle and not any(
                    item[1] in used for item in candidate
                ):
                    matches.append(candidate)
        if not matches or len({tuple(item[2] for item in match) for match in matches}) > 1:
            unmatched += 1
            continue
        for token, (_, identity, excluded) in zip(tokens, matches[0]):
            used.add(identity)
            if excluded:
                excluded_spans.append((line.start() + token.start(), line.start() + token.end()))
    if unmatched:
        logger.warning("Region mask could not align {} source lines; leaving them readable", unmatched)
    return excluded_spans
