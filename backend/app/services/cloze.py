"""Cloze-deletion parsing and rendering.

Uses Anki's own markup, {{c1::answer}} or {{c1::answer::hint}}, so anyone
who already knows Anki (or pastes cards copied from Anki/RemNote) can write
cloze cards without learning a new syntax. Anki turns one cloze note into
one card per distinct index (c1, c2, ...); we do the same in
services/flashcard_cards.py using the helpers below.
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Non-greedy match up to the next "}}" - deliberately simple (no nested
# braces support) since nested cloze markup isn't something Anki itself
# supports either.
_CLOZE_RE = re.compile(r"\{\{c(\d+)::((?:(?!\}\}).)*?)\}\}", re.DOTALL)

# Wraps the target span's answer text on the answer side so the frontend can
# highlight it without needing a full markdown renderer for the rest of the
# card. Chosen to be extremely unlikely to appear in ordinary study text.
ANSWER_MARK = "%%"


@dataclass
class ClozeSpan:
    index: int
    text: str
    hint: Optional[str]


def _split_inner(inner: str) -> Tuple[str, Optional[str]]:
    if "::" in inner:
        text, hint = inner.split("::", 1)
        return text, hint
    return inner, None


def parse_cloze_spans(source: str) -> List[ClozeSpan]:
    spans = []
    for m in _CLOZE_RE.finditer(source):
        text, hint = _split_inner(m.group(2))
        spans.append(ClozeSpan(index=int(m.group(1)), text=text, hint=hint))
    return spans


def cloze_indices(source: str) -> List[int]:
    """Unique cloze indices present in source, ascending - one flashcard is
    generated per index, same as Anki."""
    return sorted({s.index for s in parse_cloze_spans(source)})


def render_cloze(source: str, target_index: int) -> Tuple[str, str]:
    """Render the (question, answer) pair for one cloze index.

    Question: the target index's span becomes a blank (its hint, if any, or
    "..."); every other index in the note is revealed as plain text - those
    belong to sibling cards, not this one.
    Answer: every index is revealed as plain text, with the target span
    wrapped in ANSWER_MARK so the frontend can highlight what was hidden.
    """

    def _question_sub(m: re.Match) -> str:
        index = int(m.group(1))
        text, hint = _split_inner(m.group(2))
        if index != target_index:
            return text
        return f"[{hint}]" if hint else "[...]"

    def _answer_sub(m: re.Match) -> str:
        index = int(m.group(1))
        text, _hint = _split_inner(m.group(2))
        if index == target_index:
            return f"{ANSWER_MARK}{text}{ANSWER_MARK}"
        return text

    question = _CLOZE_RE.sub(_question_sub, source).strip()
    answer = _CLOZE_RE.sub(_answer_sub, source).strip()
    return question, answer
