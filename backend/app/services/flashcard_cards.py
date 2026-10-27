"""Card-authoring helpers shared by manual creation (routers/flashcards.py)
and AI generation (tasks/flashcard_tasks.py), so both paths produce
identically-shaped Flashcard rows and correctly link sibling cards (the two
directions of a reversed pair, or the several blanks of one cloze note) via
group_id.

Cards are flushed (to get ids for group_id) but never committed here -
callers own the transaction so several cards, or a card plus other work,
can be committed together atomically.
"""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import Flashcard
from app.services.cloze import cloze_indices, render_cloze


def _grouped(db: Session, cards: List[Flashcard]) -> List[Flashcard]:
    db.add_all(cards)
    db.flush()  # assigns ids
    group_id = cards[0].id
    for card in cards:
        card.group_id = group_id
    return cards


def create_basic_card(
    db: Session,
    *,
    user_id: int,
    document_id: int,
    question: str,
    answer: str,
    page_number: Optional[int] = None,
    source: str = "manual",
    source_note_id: Optional[int] = None,
) -> Flashcard:
    card = Flashcard(
        user_id=user_id,
        document_id=document_id,
        card_type="basic",
        question=question.strip(),
        answer=answer.strip(),
        page_number=page_number,
        source=source,
        source_note_id=source_note_id,
    )
    return _grouped(db, [card])[0]


def create_reversed_pair(
    db: Session,
    *,
    user_id: int,
    document_id: int,
    front: str,
    back: str,
    page_number: Optional[int] = None,
    source: str = "manual",
) -> List[Flashcard]:
    """Anki's "Basic and Reversed Card" type, minus the note abstraction:
    two independent basic cards, front->back and back->front, sharing a
    group_id so the UI can offer to edit/delete both together."""
    forward = Flashcard(
        user_id=user_id, document_id=document_id, card_type="basic",
        question=front.strip(), answer=back.strip(),
        page_number=page_number, source=source,
    )
    backward = Flashcard(
        user_id=user_id, document_id=document_id, card_type="basic",
        question=back.strip(), answer=front.strip(),
        page_number=page_number, source=source,
    )
    return _grouped(db, [forward, backward])


def create_cloze_cards(
    db: Session,
    *,
    user_id: int,
    document_id: int,
    cloze_text: str,
    page_number: Optional[int] = None,
    source: str = "manual",
) -> List[Flashcard]:
    """One Flashcard per distinct {{cN::...}} index in cloze_text, matching
    Anki's cloze note -> multiple cards behavior."""
    indices = cloze_indices(cloze_text)
    cards = []
    for idx in indices:
        question, answer = render_cloze(cloze_text, idx)
        cards.append(Flashcard(
            user_id=user_id, document_id=document_id, card_type="cloze",
            question=question, answer=answer, cloze_text=cloze_text,
            cloze_index=idx, page_number=page_number, source=source,
        ))
    return _grouped(db, cards)
