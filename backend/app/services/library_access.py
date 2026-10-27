"""Membership-scoped document access and response serialization."""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Query, Session, object_session
from sqlalchemy import func

from app.models import Document, LibraryEntry


def accessible_document_query(db: Session, user_id: int) -> Query[Document]:
    """Return only assets that have a membership for ``user_id``."""
    return db.query(Document).join(LibraryEntry).filter(LibraryEntry.user_id == user_id)


def get_library_entry(db: Session, user_id: int, doc_id: int) -> LibraryEntry:
    """Return the user's membership or hide the asset behind a 404."""
    entry = db.query(LibraryEntry).filter(
        LibraryEntry.user_id == user_id,
        LibraryEntry.document_id == doc_id,
    ).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return entry


def get_document_for_user(db: Session, user_id: int, doc_id: int) -> Document:
    """Return a document only when the requester has a library membership."""
    document = accessible_document_query(db, user_id).filter(Document.id == doc_id).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def visible_document_title(doc: Document, entry: LibraryEntry) -> str:
    """Resolve a title without exposing another member's private filename."""
    if entry.title:
        return entry.title
    if doc.user_id == entry.user_id:
        return doc.title
    if doc.shared_title:
        return doc.shared_title
    return f"Document {doc.id}"


def document_response(
    doc: Document,
    entry: LibraryEntry,
    member_count: Optional[int] = None,
) -> dict:
    """Serialize safe asset details with membership-private reading state."""
    if member_count is None:
        session = object_session(doc)
        if session is None:
            member_count = len(doc.library_entries)
        else:
            member_count = session.query(func.count(LibraryEntry.id)).filter(
                LibraryEntry.document_id == doc.id
            ).scalar() or 0
    is_uploader = doc.user_id == entry.user_id
    title = visible_document_title(doc, entry)
    can_share = bool(
        entry.can_publish
        and (not doc.is_shared or doc.shared_by_user_id == entry.user_id)
    )
    return {
        "id": doc.id,
        "user_id": entry.user_id,
        "title": title,
        "format": doc.format,
        "file_size": doc.file_size,
        "notebook_id": entry.notebook_id,
        "num_pages": doc.num_pages,
        "processing_status": doc.processing_status,
        "original_url": entry.original_url,
        "layout_mode": doc.layout_mode,
        "created_at": entry.added_at,
        "last_read_page": entry.last_read_page,
        "last_read_position": entry.last_read_position,
        "last_read_at": entry.last_read_at,
        "can_share": can_share,
        "is_shared": bool(doc.is_shared),
        "shared_at": doc.shared_at,
        "shared_title": doc.shared_title,
        "author": doc.author,
        "description": doc.description,
        "topics": list(doc.topics or []),
        "document_type": doc.document_type,
        "language": doc.language,
        "read_only": bool(doc.is_shared or member_count > 1 or not is_uploader),
    }
