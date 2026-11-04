"""Notes and highlights: text-anchored marks on a document, plus the
per-user colour palette used to tag them.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Document, ExtractedText, Note, Flashcard, LibraryEntry
from app.routers.auth import get_current_user_from_token
from app.schemas import (
    NoteCreate,
    NoteUpdate,
    NoteResponse,
    NoteColorCreate,
    NoteColorUpdate,
    NoteColorResponse,
    FlashcardResponse,
)
from app.services import note_colors
from app.services.note_flashcard import existing_card_for_note, make_flashcard_from_note
from app.services.library_access import (
    get_document_for_user,
    get_library_entry,
)

router = APIRouter(prefix="/api/notes", tags=["notes"])

_CONTEXT_WINDOW = 80
_MAX_TAGS = 8
_TAG_LEN = 32


def _owned_document(db: Session, user: User, document_id: int) -> Document:
    doc = get_document_for_user(db, user.id, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _owned_note(db: Session, user: User, note_id: int) -> Note:
    note = db.query(Note).filter(Note.id == note_id, Note.user_id == user.id).first()
    if not note or not get_document_for_user(db, user.id, note.document_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return note


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_tags(tags: Optional[List[str]]) -> List[str]:
    if not tags:
        return []
    out: List[str] = []
    seen = set()
    for raw in tags:
        tag = " ".join((raw or "").split())
        if not tag:
            continue
        if len(tag) > _TAG_LEN:
            raise HTTPException(status_code=400, detail=f"Tags must be {_TAG_LEN} characters or fewer")
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) > _MAX_TAGS:
            raise HTTPException(status_code=400, detail=f"At most {_MAX_TAGS} tags")
    return out


def _flashcard_map(db: Session, user_id: int, note_ids: List[int]) -> dict[int, int]:
    if not note_ids:
        return {}
    rows = (
        db.query(Flashcard.source_note_id, Flashcard.id)
        .filter(Flashcard.source_note_id.in_(note_ids), Flashcard.user_id == user_id)
        .all()
    )
    return {nid: fid for nid, fid in rows if nid is not None}


def _note_out(
    note: Note,
    flashcard_id: Optional[int] = None,
    document_title: Optional[str] = None,
) -> NoteResponse:
    return NoteResponse(
        id=note.id,
        document_id=note.document_id,
        page_number=note.page_number,
        end_page=note.end_page,
        quote=note.quote,
        context_before=note.context_before,
        context_after=note.context_after,
        note=note.note,
        color=note.color,
        word_range=note.word_range,
        tags=list(note.tags or []),
        flashcard_id=flashcard_id,
        document_title=document_title,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _document_title(
    document_id: int,
    entry_title: Optional[str],
    shared_title: Optional[str],
    is_shared: bool,
) -> str:
    return entry_title or (shared_title if is_shared else None) or f"Document {document_id}"


def _validate_word_range(word_range: Optional[List[int]]) -> Optional[List[int]]:
    if word_range is None:
        return None
    if len(word_range) != 2:
        raise HTTPException(status_code=400, detail="word_range must be [start, end]")
    start, end = word_range
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
        raise HTTPException(status_code=400, detail="word_range must be [start, end] with end >= start >= 0")
    return [start, end]


def _fill_anchor(
    db: Session,
    document_id: int,
    page_number: int,
    quote: Optional[str],
    context_before: Optional[str],
    context_after: Optional[str],
    word_range: Optional[List[int]],
) -> tuple[Optional[str], Optional[str], Optional[List[int]]]:
    """Fill missing context/offsets from the extracted page when the quote is found."""
    if not quote:
        return context_before, context_after, word_range
    if context_before and context_after and word_range:
        return context_before, context_after, word_range
    page = (
        db.query(ExtractedText)
        .filter(
            ExtractedText.document_id == document_id,
            ExtractedText.page_number == page_number,
        )
        .first()
    )
    if not page or not page.content:
        return context_before, context_after, word_range
    content = page.content
    idx = content.lower().find(quote.lower())
    if idx < 0:
        return context_before, context_after, word_range
    end = idx + len(quote)
    if not context_before:
        context_before = content[max(0, idx - _CONTEXT_WINDOW) : idx]
    if not context_after:
        context_after = content[end : end + _CONTEXT_WINDOW]
    if not word_range:
        word_range = [idx, end]
    return context_before, context_after, word_range


@router.get("/colors", response_model=List[NoteColorResponse])
def list_colors(
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    return note_colors.list_palette(db, user.id)


@router.post("/colors", response_model=NoteColorResponse)
def create_color(
    body: NoteColorCreate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    return note_colors.add_color(db, user.id, body.name, body.hex, body.hint)


@router.patch("/colors/{color_id}", response_model=NoteColorResponse)
def update_color(
    color_id: str,
    body: NoteColorUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    if body.name is None and body.hex is None and body.hint is None:
        raise HTTPException(status_code=400, detail="Nothing to update")
    return note_colors.update_color(db, user.id, color_id, body.name, body.hex, body.hint)


@router.delete("/colors/{color_id}")
def delete_color(
    color_id: str,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    in_use = (
        db.query(Note)
        .filter(Note.user_id == user.id, Note.color == color_id)
        .first()
        is not None
    )
    note_colors.delete_color(db, user.id, color_id, in_use)
    return {"detail": "Deleted"}


@router.post("", response_model=NoteResponse)
def create_note(
    body: NoteCreate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    _owned_document(db, user, body.document_id)
    quote = _clean_text(body.quote)
    text = _clean_text(body.note)
    if not quote and not text:
        raise HTTPException(status_code=400, detail="Provide a quote to highlight or a note body")
    if body.color not in note_colors.known_color_ids(db, user.id):
        raise HTTPException(status_code=400, detail="Unknown highlight colour")
    end_page = body.end_page
    if end_page is not None and end_page < body.page_number:
        raise HTTPException(status_code=400, detail="end_page must be on or after page_number")
    word_range = _validate_word_range(body.word_range)
    context_before, context_after, word_range = _fill_anchor(
        db,
        body.document_id,
        body.page_number,
        quote,
        _clean_text(body.context_before),
        _clean_text(body.context_after),
        word_range,
    )
    note = Note(
        user_id=user.id,
        document_id=body.document_id,
        page_number=body.page_number,
        end_page=end_page,
        quote=quote,
        context_before=context_before,
        context_after=context_after,
        note=text,
        color=body.color,
        word_range=word_range,
        tags=_clean_tags(body.tags),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _note_out(note)


@router.get("/export")
def export_notes(
    document_id: Optional[int] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    q = db.query(
        Note,
        Document.id,
        LibraryEntry.title,
        Document.shared_title,
        Document.is_shared,
    ).join(Document, Document.id == Note.document_id).join(
        LibraryEntry,
        (LibraryEntry.document_id == Document.id) & (LibraryEntry.user_id == user.id),
    ).filter(
        Note.user_id == user.id,
        Note.document_id.in_(select(LibraryEntry.document_id).where(
            LibraryEntry.user_id == user.id
        )),
    )
    title = "All documents"
    if document_id is not None:
        doc = _owned_document(db, user, document_id)
        entry = get_library_entry(db, user.id, doc.id)
        title = _document_title(doc.id, entry.title, doc.shared_title, doc.is_shared)
        q = q.filter(Note.document_id == document_id)
    rows = q.order_by(LibraryEntry.title, Note.page_number, Note.id).all()
    lines = [f"# Notes: {title}", "", f"Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d')}", ""]
    current_doc = None
    for note, row_doc_id, entry_title, shared_title, is_shared in rows:
        doc_title = _document_title(row_doc_id, entry_title, shared_title, is_shared)
        if document_id is None and doc_title != current_doc:
            current_doc = doc_title
            lines.extend([f"## {doc_title}", ""])
        if note.quote:
            lines.append(f"> {note.quote}")
            lines.append("")
        if note.note:
            lines.append(note.note)
            lines.append("")
        meta = [f"p.{note.page_number}", note.color]
        if note.tags:
            meta.append("tags: " + ", ".join(note.tags))
        lines.append("_" + " · ".join(meta) + "_")
        lines.append("")
        lines.append("---")
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    filename = "notes.md" if document_id is None else f"notes-{document_id}.md"
    return PlainTextResponse(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", response_model=List[NoteResponse])
def list_notes(
    document_id: Optional[int] = None,
    q: Optional[str] = Query(default=None, max_length=200),
    tag: Optional[str] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    if document_id is None and not (q or "").strip():
        raise HTTPException(status_code=400, detail="document_id or q is required")
    query = db.query(Note).filter(
        Note.user_id == user.id,
        Note.document_id.in_(select(LibraryEntry.document_id).where(
            LibraryEntry.user_id == user.id
        )),
    )
    titles: dict[int, str] = {}
    if document_id is not None:
        _owned_document(db, user, document_id)
        query = query.filter(Note.document_id == document_id)
    else:
        docs = db.query(
            Document.id,
            LibraryEntry.title,
            Document.shared_title,
            Document.is_shared,
        ).join(LibraryEntry, LibraryEntry.document_id == Document.id).filter(
            LibraryEntry.user_id == user.id
        ).all()
        titles = {
            doc_id: _document_title(doc_id, entry_title, shared_title, is_shared)
            for doc_id, entry_title, shared_title, is_shared in docs
        }
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        query = query.filter(or_(Note.quote.ilike(like), Note.note.ilike(like)))
    notes = query.order_by(Note.page_number, Note.id).all()
    if tag:
        tag_l = tag.lower()
        notes = [
            n for n in notes
            if any(str(t).lower() == tag_l for t in (n.tags or []))
        ]
    fmap = _flashcard_map(db, user.id, [n.id for n in notes])
    return [
        _note_out(n, flashcard_id=fmap.get(n.id), document_title=titles.get(n.document_id))
        for n in notes
    ]


@router.get("/{note_id}", response_model=NoteResponse)
def get_note(
    note_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    note = _owned_note(db, user, note_id)
    card = existing_card_for_note(db, note.id, user.id)
    return _note_out(note, flashcard_id=card.id if card else None)


@router.patch("/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: int,
    body: NoteUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    note = _owned_note(db, user, note_id)
    if body.color is not None:
        if body.color not in note_colors.known_color_ids(db, user.id):
            raise HTTPException(status_code=400, detail="Unknown highlight colour")
        note.color = body.color
    if body.note is not None:
        # Empty string clears a written note and leaves a highlight-only mark
        # when a quote is present. A page note (no quote) cannot be emptied.
        cleaned = body.note.strip()
        if not cleaned and not note.quote:
            raise HTTPException(status_code=400, detail="A page note needs a body")
        note.note = cleaned or None
    if body.tags is not None:
        note.tags = _clean_tags(body.tags)
    db.commit()
    db.refresh(note)
    card = existing_card_for_note(db, note.id, user.id)
    return _note_out(note, flashcard_id=card.id if card else None)


@router.post("/{note_id}/flashcard", response_model=FlashcardResponse)
def create_flashcard_from_note(
    note_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    note = _owned_note(db, user, note_id)
    card = make_flashcard_from_note(db, user.id, note)
    db.commit()
    db.refresh(card)
    return card


@router.delete("/{note_id}")
def delete_note(
    note_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    note = _owned_note(db, user, note_id)
    db.delete(note)
    db.commit()
    return {"detail": "Deleted"}
