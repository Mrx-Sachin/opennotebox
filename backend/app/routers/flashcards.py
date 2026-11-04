"""Flashcard authoring, AI generation, and FSRS-scheduled review.

Route order matters here: FastAPI/Starlette matches routes in registration
order and "/{flashcard_id}" has no int type converter in the path itself
(the int-ness is only enforced by the handler's parameter type, which is
checked AFTER a route already matched). So literal-path routes like
"/queue", "/stats" and "/settings" must be registered before the
"/{flashcard_id}" routes for the same HTTP method, or a request for e.g.
PUT /api/flashcards/settings would match PUT /{flashcard_id} first and
fail trying to parse "settings" as an int.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, Flashcard, FlashcardReviewLog, User, UserSettings
from app.schemas import (
    FlashcardCreate,
    FlashcardGenerateRequest,
    FlashcardQueueItem,
    FlashcardResponse,
    FlashcardReviewRequest,
    FlashcardSettings,
    FlashcardStats,
    FlashcardUpdate,
)
from app.routers.auth import get_current_user_from_token
from app.services.cloze import cloze_indices, parse_cloze_spans, render_cloze
from app.services.flashcard_cards import create_basic_card, create_cloze_cards, create_reversed_pair
from app.services.fsrs_scheduler import apply_review, preview_intervals
from app.tasks.flashcard_tasks import generate_flashcards as generate_flashcards_task
from app.services.library_access import accessible_document_query, get_document_for_user

router = APIRouter(prefix="/api/flashcards", tags=["flashcards"])

# Daily review/new-card limits are a personal pacing preference (someone
# reviewing 5 documents a day wants a very different cap than someone doing
# one), not shared deployment config - stored per-user in UserSettings, not
# the deployment-wide AppSettings table. See models/__init__.py.
FLASHCARD_SETTINGS_KEY = "flashcard_limits"


# --- generation -------------------------------------------------------------

@router.post("/generate")
def generate_flashcards(
    body: FlashcardGenerateRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Kick off flashcard generation for a document. Replaces this
    document's AI-generated cards; hand-authored cards are left alone (a
    "Regenerate" click shouldn't be able to destroy cards the user wrote
    themselves)."""
    doc = get_document_for_user(db, user.id, body.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.processing_status != "completed":
        raise HTTPException(status_code=400, detail="Document is not yet fully processed")

    db.query(Flashcard).filter(
        Flashcard.document_id == doc.id,
        Flashcard.user_id == user.id,
        Flashcard.source == "ai",
    ).delete()
    db.commit()

    generate_flashcards_task.delay(user_id=user.id, document_id=doc.id)

    return {"status": "generating"}


# --- listing / queue / stats / settings (literal paths - see module docstring) ---



def _resolve_hint(card: Flashcard) -> Optional[str]:
    """Extract hint: prefer card.hint, fallback to parsing cloze markup if cloze card."""
    if card.hint:
        return card.hint
    if card.card_type == "cloze" and card.cloze_text:
        try:
            spans = parse_cloze_spans(card.cloze_text)
            return next((s.hint for s in spans if s.index == card.cloze_index and s.hint), None)
        except Exception:
            return None
    return None


def _to_card_response(card: Flashcard) -> FlashcardResponse:
    resp = FlashcardResponse.model_validate(card)
    if not resp.hint:
        resp.hint = _resolve_hint(card)
    return resp


def _to_queue_item(card: Flashcard) -> FlashcardQueueItem:
    item = FlashcardQueueItem.model_validate(card)
    if not item.hint:
        item.hint = _resolve_hint(card)
    item.preview_intervals = preview_intervals(card)
    return item

@router.get("", response_model=List[FlashcardResponse])
def list_flashcards(
    document_id: Optional[int] = None,
    limit: int = Query(default=200, ge=1, le=500),  # cards are small objects; higher default than documents
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """List flashcards, optionally scoped to one document."""
    q = db.query(Flashcard).filter(Flashcard.user_id == user.id)
    if document_id is not None:
        if not get_document_for_user(db, user.id, document_id):
            raise HTTPException(status_code=404, detail="Document not found")
        q = q.filter(Flashcard.document_id == document_id)
    else:
        q = q.filter(
            Flashcard.document_id.in_(
                accessible_document_query(db, user.id).with_entities(Document.id).subquery()
            )
        )
    cards = q.order_by(Flashcard.id).offset(offset).limit(limit).all()
    return [_to_card_response(card) for card in cards]


def _scope(db: Session, user_id: int, document_id: Optional[int]):
    q = db.query(Flashcard).filter(Flashcard.user_id == user_id)
    if document_id is not None:
        if not get_document_for_user(db, user_id, document_id):
            raise HTTPException(status_code=404, detail="Document not found")
        q = q.filter(Flashcard.document_id == document_id)
    else:
        q = q.filter(
            Flashcard.document_id.in_(
                accessible_document_query(db, user_id).with_entities(Document.id).subquery()
            )
        )
    return q


def _owned_flashcard(db: Session, user_id: int, flashcard_id: int) -> Flashcard:
    card = db.query(Flashcard).filter(
        Flashcard.id == flashcard_id,
        Flashcard.user_id == user_id,
    ).first()
    if not card or not get_document_for_user(db, user_id, card.document_id):
        raise HTTPException(status_code=404, detail="Flashcard not found")
    return card


def _today_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day)


def _new_introduced_today(db: Session, user_id: int, document_id: Optional[int], today_start: datetime) -> int:
    q = _scope(db, user_id, document_id).filter(Flashcard.introduced_at >= today_start)
    return q.count()


def _reviews_done_today(db: Session, user_id: int, document_id: Optional[int], today_start: datetime) -> int:
    """Grading actions today that were NOT a card's first-ever review (those
    count against the new-card quota instead)."""
    ids_q = _scope(db, user_id, document_id).with_entities(Flashcard.id)
    total_today = (
        db.query(FlashcardReviewLog)
        .filter(
            FlashcardReviewLog.flashcard_id.in_(ids_q),
            FlashcardReviewLog.reviewed_at >= today_start,
        )
        .count()
    )
    return max(0, total_today - _new_introduced_today(db, user_id, document_id, today_start))


def _get_flashcard_settings(db: Session, user_id: int) -> FlashcardSettings:
    row = (
        db.query(UserSettings)
        .filter(UserSettings.user_id == user_id, UserSettings.key == FLASHCARD_SETTINGS_KEY)
        .first()
    )
    if row is None:
        return FlashcardSettings()
    return FlashcardSettings(**row.value)


@router.get("/settings", response_model=FlashcardSettings)
def get_flashcard_settings(
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    return _get_flashcard_settings(db, user.id)


@router.put("/settings", response_model=FlashcardSettings)
def update_flashcard_settings(
    body: FlashcardSettings,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    row = (
        db.query(UserSettings)
        .filter(UserSettings.user_id == user.id, UserSettings.key == FLASHCARD_SETTINGS_KEY)
        .first()
    )
    if row is None:
        row = UserSettings(user_id=user.id, key=FLASHCARD_SETTINGS_KEY, value=body.model_dump())
        db.add(row)
    else:
        row.value = body.model_dump()
    db.commit()
    return body


@router.get("/stats", response_model=FlashcardStats)
def get_flashcard_stats(
    document_id: Optional[int] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    today_start = _today_start(now)
    base = _scope(db, user.id, document_id)
    settings_row = _get_flashcard_settings(db, user.id)

    new_today = _new_introduced_today(db, user.id, document_id, today_start)
    reviews_today = _reviews_done_today(db, user.id, document_id, today_start)

    return FlashcardStats(
        due=base.filter(Flashcard.due <= now, Flashcard.reps > 0).count(),
        new=base.filter(Flashcard.reps == 0).count(),
        total=base.count(),
        new_remaining_today=max(0, settings_row.daily_new_limit - new_today),
        review_remaining_today=max(0, settings_row.daily_review_limit - reviews_today),
    )


@router.get("/queue", response_model=List[FlashcardQueueItem])
def get_review_queue(
    document_id: Optional[int] = None,
    limit: int = Query(default=200, ge=1, le=500),
    mode: str = Query(default="due", pattern="^(due|all)$"),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Cards ready to review right now.

    When mode == "all", returns all flashcards for the scope as queue items
    (ordered by due, id), computing preview_intervals for each.
    When mode == "due" (default), maintains the due and new quota logic.
    """
    base = _scope(db, user.id, document_id)

    if mode == "all":
        cards = (
            base.order_by(Flashcard.due, Flashcard.id)
            .limit(limit)
            .all()
        )
        return [_to_queue_item(card) for card in cards]

    now = datetime.now(timezone.utc)
    today_start = _today_start(now)
    settings_row = _get_flashcard_settings(db, user.id)

    review_remaining = max(
        0, settings_row.daily_review_limit - _reviews_done_today(db, user.id, document_id, today_start)
    )
    new_remaining = max(
        0, settings_row.daily_new_limit - _new_introduced_today(db, user.id, document_id, today_start)
    )

    due_cards = (
        base.filter(Flashcard.due <= now, Flashcard.reps > 0)
        .order_by(Flashcard.due)
        .limit(review_remaining)
        .all()
    )
    new_cards = (
        base.filter(Flashcard.reps == 0)
        .order_by(Flashcard.id)
        .limit(new_remaining)
        .all()
    )

    queue = (due_cards + new_cards)[:limit]
    return [_to_queue_item(card) for card in queue]


@router.post("/reset")
def reset_deck(
    document_id: Optional[int] = Query(default=None),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Reset the scheduling state for all flashcards of the user (or filtered by document_id)."""
    cards = _scope(db, user.id, document_id).all()
    now = datetime.now(timezone.utc)
    for card in cards:
        card.reps = 0
        card.lapses = 0
        card.fsrs_state = 1
        card.fsrs_step = None
        card.stability = None
        card.difficulty = None
        card.due = now
        card.last_review = None
        card.introduced_at = None
    db.commit()
    return {"message": "Deck reset successfully", "count": len(cards)}


# --- manual authoring --------------------------------------------------------

@router.post("", response_model=List[FlashcardResponse])
def create_flashcard(
    body: FlashcardCreate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    doc = get_document_for_user(db, user.id, body.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if body.card_type == "cloze":
        if not body.cloze_text or not cloze_indices(body.cloze_text):
            raise HTTPException(
                status_code=400,
                detail="Cloze cards need at least one {{c1::...}} span",
            )
        cards = create_cloze_cards(
            db,
            user_id=user.id,
            document_id=doc.id,
            cloze_text=body.cloze_text,
            page_number=body.page_number,
            source="manual",
        )
        if body.hint:
            for card in cards:
                if not card.hint:
                    card.hint = body.hint.strip()
    elif body.card_type == "reversed":
        if not body.question or not body.answer:
            raise HTTPException(status_code=400, detail="Reversed cards need both question and answer")
        cards = create_reversed_pair(
            db,
            user_id=user.id,
            document_id=doc.id,
            front=body.question,
            back=body.answer,
            hint=body.hint,
            page_number=body.page_number,
            source="manual",
        )
    else:
        if not body.question or not body.answer:
            raise HTTPException(status_code=400, detail="Basic cards need both question and answer")
        cards = [
            create_basic_card(
                db,
                user_id=user.id,
                document_id=doc.id,
                question=body.question,
                answer=body.answer,
                hint=body.hint,
                page_number=body.page_number,
                source="manual",
            )
        ]

    db.commit()
    for card in cards:
        db.refresh(card)
    return [_to_card_response(card) for card in cards]


@router.put("/{flashcard_id}", response_model=FlashcardResponse)
def update_flashcard(
    flashcard_id: int,
    body: FlashcardUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    card = _owned_flashcard(db, user.id, flashcard_id)

    if card.card_type == "cloze" and body.cloze_text is not None:
        if card.cloze_index not in cloze_indices(body.cloze_text):
            raise HTTPException(
                status_code=400,
                detail=f"Edited text no longer contains a {{{{c{card.cloze_index}::...}}}} span",
            )
        # Re-render only THIS card's own index. Sibling cards for other
        # {{cN::}} indices in the same note are left untouched - regenerating
        # them here could silently delete or duplicate cards the user
        # already has independent review history on.
        question, answer = render_cloze(body.cloze_text, card.cloze_index)
        card.cloze_text = body.cloze_text
        card.question = question
        card.answer = answer
        spans = parse_cloze_spans(body.cloze_text)
        card.hint = next((s.hint for s in spans if s.index == card.cloze_index and s.hint), None)
    else:
        if body.question is not None:
            card.question = body.question.strip()
        if body.answer is not None:
            card.answer = body.answer.strip()

    if body.hint is not None:
        card.hint = body.hint.strip() if body.hint else None

    if body.page_number is not None:
        card.page_number = body.page_number

    card.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(card)
    return _to_card_response(card)


@router.delete("/{flashcard_id}")
def delete_flashcard(
    flashcard_id: int,
    group: bool = False,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Delete a single flashcard, or with ?group=true every sibling sharing
    its group_id (both directions of a reversed pair, or every blank of one
    cloze note)."""
    card = _owned_flashcard(db, user.id, flashcard_id)

    if group and card.group_id:
        db.query(Flashcard).filter(
            Flashcard.group_id == card.group_id, Flashcard.user_id == user.id,
        ).delete()
    else:
        db.delete(card)
    db.commit()
    return {"detail": "Deleted"}


# --- review -------------------------------------------------------------

@router.post("/{flashcard_id}/reset", response_model=FlashcardResponse)
def reset_flashcard(
    flashcard_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Reset a single flashcard's scheduling state to new/due."""
    card = _owned_flashcard(db, user.id, flashcard_id)
    card.reps = 0
    card.lapses = 0
    card.fsrs_state = 1
    card.fsrs_step = None
    card.stability = None
    card.difficulty = None
    card.due = datetime.now(timezone.utc)
    card.last_review = None
    card.introduced_at = None
    db.commit()
    db.refresh(card)
    return _to_card_response(card)


@router.post("/{flashcard_id}/review", response_model=FlashcardResponse)
def review_flashcard(
    flashcard_id: int,
    body: FlashcardReviewRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    card = _owned_flashcard(db, user.id, flashcard_id)

    was_new = card.reps == 0
    log = apply_review(card, body.rating, body.duration_ms)
    if was_new:
        card.introduced_at = card.last_review
    db.add(log)
    db.commit()
    db.refresh(card)
    return _to_card_response(card)
