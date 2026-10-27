"""Wraps py-fsrs so the actual FSRS math (difficulty/stability/
retrievability, next-interval calculation) is entirely the maintained
library, not a hand-rolled reimplementation. This module only translates
between our Flashcard/FlashcardReviewLog ORM rows and fsrs.Card/fsrs.Rating.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fsrs import Card, Rating, Scheduler, State

from app.models import Flashcard, FlashcardReviewLog

# One shared Scheduler using py-fsrs's published default parameters (fit on
# Anki's large aggregate review dataset). Per-user parameter optimization
# (fsrs.Optimizer) needs on the order of 1,000 reviews per user before it
# beats the defaults, and pulls in torch - not worth it for a single-user
# app. FlashcardReviewLog exists specifically so this can be added later
# without any schema change.
_scheduler = Scheduler()


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """DB datetimes are naive UTC (project convention); fsrs requires
    timezone-aware UTC datetimes."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _to_fsrs_card(flashcard: Flashcard) -> Card:
    return Card(
        card_id=flashcard.id,
        state=State(flashcard.fsrs_state),
        step=flashcard.fsrs_step,
        stability=flashcard.stability,
        difficulty=flashcard.difficulty,
        due=_aware(flashcard.due),
        last_review=_aware(flashcard.last_review),
    )


def apply_review(
    flashcard: Flashcard, rating: int, duration_ms: Optional[int] = None
) -> FlashcardReviewLog:
    """Grade a card. Mutates the Flashcard's FSRS columns in place and
    returns a new (uncommitted) FlashcardReviewLog - the caller owns the
    transaction so it can also update introduced_at/commit atomically."""
    state_before = flashcard.fsrs_state
    fsrs_card = _to_fsrs_card(flashcard)
    updated, _log = _scheduler.review_card(
        fsrs_card, Rating(rating), review_duration=duration_ms
    )

    flashcard.fsrs_state = int(updated.state)
    flashcard.fsrs_step = updated.step
    flashcard.stability = updated.stability
    flashcard.difficulty = updated.difficulty
    flashcard.due = _naive_utc(updated.due)
    flashcard.last_review = _naive_utc(updated.last_review)
    flashcard.reps += 1
    # A "lapse" is forgetting a card that had already graduated out of
    # initial learning - grading a brand-new or still-learning card Again
    # doesn't count.
    if rating == Rating.Again and state_before != State.Learning:
        flashcard.lapses += 1

    return FlashcardReviewLog(
        flashcard_id=flashcard.id,
        rating=rating,
        state_before=state_before,
        reviewed_at=flashcard.last_review,
        review_duration_ms=duration_ms,
        stability_after=flashcard.stability,
        difficulty_after=flashcard.difficulty,
    )


def _format_interval(delta: timedelta) -> str:
    """Compact human string ("10m", "3d", "2mo"), matching the style Anki
    prints under its own grade buttons."""
    seconds = delta.total_seconds()
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))}m"
    if seconds < 86400:
        return f"{round(seconds / 3600)}h"
    days = seconds / 86400
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{round(days / 30)}mo"
    return f"{round(days / 365, 1)}y"


def preview_intervals(flashcard: Flashcard) -> Dict[str, str]:
    """The next interval each of the four grades would produce, without
    mutating the card. Anki shows this under its grade buttons and it's one
    of the most-used signals in the whole review UI - it's what tells you
    "Hard" really only buys a few extra hours versus "Good".

    Scheduler.review_card() copies its Card argument internally, so calling
    it four times on the same fsrs_card is safe and leaves it untouched.
    """
    fsrs_card = _to_fsrs_card(flashcard)
    now = datetime.now(timezone.utc)
    out: Dict[str, str] = {}
    for rating in Rating:
        updated, _log = _scheduler.review_card(fsrs_card, rating, review_datetime=now)
        out[rating.name.lower()] = _format_interval(updated.due - now)
    return out
