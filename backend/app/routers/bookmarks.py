"""Bookmarks router: lightweight page/position markers within a document."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Document, Bookmark
from app.schemas import BookmarkCreate, BookmarkResponse, BookmarkUpdate
from app.routers.auth import get_current_user_from_token

router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])


def _default_label(page_number: int) -> str:
    return f"Page {page_number}"


def _normalize_label(label: Optional[str], page_number: int) -> str:
    """Use the provided name when non-empty; otherwise default to the page number."""
    cleaned = (label or "").strip()
    return cleaned or _default_label(page_number)


@router.post("", response_model=BookmarkResponse)
def create_bookmark(
    body: BookmarkCreate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Create a bookmark at a page (and optional intra-page position) of a document.

    Label defaults to \"Page {n}\" when omitted or blank so every bookmark has
    a display name without the client having to invent one.
    """
    doc = db.query(Document).filter(
        Document.id == body.document_id,
        Document.user_id == user.id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    bookmark = Bookmark(
        user_id=user.id,
        document_id=doc.id,
        page_number=body.page_number,
        position=body.position,
        label=_normalize_label(body.label, body.page_number),
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)
    return bookmark


@router.get("", response_model=List[BookmarkResponse])
def list_bookmarks(
    document_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """List bookmarks for a document, ordered by page then position."""
    bookmarks = db.query(Bookmark).filter(
        Bookmark.document_id == document_id,
        Bookmark.user_id == user.id,
    ).order_by(Bookmark.page_number, Bookmark.position).all()
    return bookmarks


@router.patch("/{bookmark_id}", response_model=BookmarkResponse)
def update_bookmark(
    bookmark_id: int,
    body: BookmarkUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Rename a bookmark (label only)."""
    bookmark = db.query(Bookmark).filter(
        Bookmark.id == bookmark_id,
        Bookmark.user_id == user.id,
    ).first()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")

    bookmark.label = _normalize_label(body.label, bookmark.page_number)
    db.commit()
    db.refresh(bookmark)
    return bookmark


@router.delete("/{bookmark_id}")
def delete_bookmark(
    bookmark_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Delete a bookmark."""
    bookmark = db.query(Bookmark).filter(
        Bookmark.id == bookmark_id,
        Bookmark.user_id == user.id,
    ).first()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    db.delete(bookmark)
    db.commit()
    return {"detail": "Deleted"}
