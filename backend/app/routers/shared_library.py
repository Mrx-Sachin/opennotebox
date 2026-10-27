"""Authenticated discovery and membership management for shared PDF assets."""
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, DocumentTopic, LibraryEntry, Notebook, User
from app.routers.auth import get_current_user_from_token
from app.schemas import (
    SharedDocumentUpdate,
    SharedLibraryAddRequest,
    SharedLibraryAddResponse,
    SharedLibraryResponse,
)
from app.services.library_access import document_response


router = APIRouter(prefix="/api/shared-library", tags=["shared library"])
_READY_STATUSES = (
    "extracted",
    "completed",
    "summarizing_pages",
    "summarizing_sections",
)


def _catalog_query(db: Session):
    return db.query(Document).filter(
        Document.is_shared.is_(True),
        Document.format == "pdf",
        Document.processing_status.in_(_READY_STATUSES),
    )


def _topic_match(topic: str):
    return Document.document_topics.any(DocumentTopic.topic == topic)


def _catalog_card(doc: Document, added: bool, reason: Optional[str] = None) -> dict:
    return {
        "id": doc.id,
        "title": doc.shared_title or "Shared document",
        "author": doc.author,
        "description": doc.description,
        "topics": list(doc.topics or []),
        "document_type": doc.document_type,
        "language": doc.language,
        "num_pages": doc.num_pages,
        "file_size": doc.file_size,
        "shared_at": doc.shared_at,
        "added": added,
        "reason": reason,
    }


def _membership_map(db: Session, user_id: int, document_ids: list[int]) -> dict[int, LibraryEntry]:
    if not document_ids:
        return {}
    rows = db.query(LibraryEntry).filter(
        LibraryEntry.user_id == user_id,
        LibraryEntry.document_id.in_(document_ids),
    ).all()
    return {entry.document_id: entry for entry in rows}


@router.get("", response_model=SharedLibraryResponse)
def list_shared_library(
    search: str = Query(default="", max_length=200),
    topic: Optional[str] = Query(default=None, max_length=40),
    document_type: Optional[str] = Query(default=None, max_length=80),
    notebook_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Search opt-in, extraction-ready PDF assets with bounded pagination."""
    query = _catalog_query(db)
    if search.strip():
        needle = search.strip()
        query = query.filter(or_(
            Document.shared_title.icontains(needle, autoescape=True),
            Document.author.icontains(needle, autoescape=True),
            Document.description.icontains(needle, autoescape=True),
            Document.document_topics.any(
                DocumentTopic.topic.icontains(needle, autoescape=True)
            ),
        ))
    normalized_topic = topic.strip().lower() if topic and topic.strip() else None
    if normalized_topic:
        query = query.filter(_topic_match(normalized_topic))
    if document_type:
        query = query.filter(Document.document_type == document_type.strip())

    interest_words: list[str] = []
    interest_topics: list[str] = []
    notebook = None
    if notebook_id is not None:
        notebook = db.query(Notebook).filter(
            Notebook.id == notebook_id,
            Notebook.user_id == user.id,
        ).first()
        if notebook is None:
            raise HTTPException(status_code=404, detail="Notebook not found")

        interest_topics = [notebook.name.strip().lower()]
        notebook_topics = db.query(DocumentTopic.topic).join(
            Document, Document.id == DocumentTopic.document_id
        ).join(LibraryEntry, LibraryEntry.document_id == Document.id).filter(
            LibraryEntry.user_id == user.id,
            LibraryEntry.notebook_id == notebook.id,
            Document.is_shared.is_(True),
            Document.format == "pdf",
            Document.processing_status.in_(_READY_STATUSES),
        ).distinct().order_by(DocumentTopic.topic).limit(23).all()
        interest_topics.extend(topic_value for (topic_value,) in notebook_topics)

        interest_words = list(dict.fromkeys(
            word.lower()
            for word in re.findall(r"[\w]{3,}", f"{notebook.name} {notebook.description or ''}")
        ))[:12]
        matches = []
        if interest_topics:
            matches.append(Document.document_topics.any(
                DocumentTopic.topic.in_(interest_topics)
            ))
        for word in interest_words:
            matches.append(or_(
                Document.document_topics.any(DocumentTopic.topic == word),
                Document.shared_title.icontains(word, autoescape=True),
                Document.author.icontains(word, autoescape=True),
                Document.description.icontains(word, autoescape=True),
            ))
        if matches:
            query = query.filter(or_(*matches))
        else:
            query = query.filter(text("1 = 0"))
        query = query.filter(~db.query(LibraryEntry.id).filter(
            LibraryEntry.user_id == user.id,
            LibraryEntry.document_id == Document.id,
        ).exists())

    total = query.count()
    ordered = query.order_by(Document.shared_at.desc(), Document.id.desc())
    rows = ordered.offset(offset).limit(limit).all()
    memberships = _membership_map(db, user.id, [doc.id for doc in rows])

    reasons: dict[int, str] = {}
    if notebook is not None:
        for doc in rows:
            matched_topic = next(
                (value for value in interest_topics if value in (doc.topics or [])),
                None,
            )
            if matched_topic:
                reasons[doc.id] = f"Shares the notebook topic {matched_topic}"
                continue
            searchable = " ".join(filter(None, [doc.shared_title, doc.author, doc.description])).lower()
            matched_word = next((word for word in interest_words if word in searchable), None)
            if matched_word:
                reasons[doc.id] = f"Matches the notebook term {matched_word}"

    cards = [
        _catalog_card(doc, doc.id in memberships, reasons.get(doc.id))
        for doc in rows
    ]

    # Facets read only indexed shared metadata and remain bounded as the
    # public catalog grows.
    all_topics = [row[0] for row in db.query(DocumentTopic.topic).join(
        Document, Document.id == DocumentTopic.document_id
    ).filter(
        Document.is_shared.is_(True),
        Document.format == "pdf",
        Document.processing_status.in_(_READY_STATUSES),
    ).distinct().order_by(DocumentTopic.topic).limit(200).all()]
    all_types = [row[0] for row in db.query(Document.document_type).filter(
        Document.is_shared.is_(True),
        Document.format == "pdf",
        Document.processing_status.in_(_READY_STATUSES),
        Document.document_type.isnot(None),
    ).distinct().order_by(Document.document_type).limit(100).all()]
    return {
        "documents": cards,
        "total": total,
        "topics": all_topics,
        "document_types": all_types,
    }


@router.patch("/{doc_id}")
def update_shared_metadata(
    doc_id: int,
    payload: SharedDocumentUpdate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Publish, update, or unpublish a PDF from its eligible library entry."""
    # Lock the shared asset first, matching add/remove/account-deletion paths.
    doc = db.query(Document).filter(Document.id == doc_id).with_for_update().first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    entry = db.query(LibraryEntry).filter(
        LibraryEntry.user_id == user.id,
        LibraryEntry.document_id == doc_id,
    ).with_for_update().first()
    if entry is None or not entry.can_publish:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.is_shared and doc.shared_by_user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if payload.is_shared:
        if doc.format != "pdf" or doc.processing_status not in _READY_STATUSES:
            raise HTTPException(status_code=409, detail="Only extraction-ready PDFs can be shared")
        if not doc.is_shared and not payload.title:
            raise HTTPException(status_code=422, detail="A shared title is required when publishing")

    fields = payload.model_fields_set
    if "title" in fields:
        doc.shared_title = payload.title
    if "author" in fields:
        doc.author = payload.author
    if "description" in fields:
        doc.description = payload.description
    if "topics" in fields:
        doc.topics = payload.topics
    if "document_type" in fields:
        doc.document_type = payload.document_type
    if "language" in fields:
        doc.language = payload.language

    if payload.is_shared:
        if not doc.is_shared:
            doc.shared_by_user_id = user.id
            doc.shared_at = datetime.now(timezone.utc)
        doc.is_shared = True
    else:
        doc.is_shared = False
        doc.shared_at = None
    db.commit()
    db.refresh(doc)
    return document_response(doc, entry)


@router.post("/add", response_model=SharedLibraryAddResponse)
def add_shared_documents(
    payload: SharedLibraryAddRequest,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Add an atomic batch of published PDF assets to the user's library."""
    document_ids = list(dict.fromkeys(payload.document_ids))
    if payload.notebook_id is not None:
        notebook = db.query(Notebook).filter(
            Notebook.id == payload.notebook_id,
            Notebook.user_id == user.id,
        ).with_for_update().first()
        if notebook is None:
            raise HTTPException(status_code=404, detail="Notebook not found")

    docs = db.query(Document).filter(
        Document.id.in_(document_ids),
    ).order_by(Document.id).with_for_update().all()
    docs_by_id = {doc.id: doc for doc in docs}
    if len(docs_by_id) != len(document_ids) or any(
        not doc.is_shared or doc.format != "pdf" or doc.processing_status not in _READY_STATUSES
        for doc in docs
    ):
        raise HTTPException(status_code=404, detail="One or more shared documents were not found")

    existing = _membership_map(db, user.id, document_ids)
    missing_ids = [doc_id for doc_id in document_ids if doc_id not in existing]
    inserted_ids: list[int] = []
    if missing_ids:
        now = datetime.now(timezone.utc)
        rows = [
            {
                "user_id": user.id,
                "document_id": doc_id,
                "notebook_id": payload.notebook_id,
                "added_at": now,
                "title": docs_by_id[doc_id].shared_title or "Shared document",
                "original_url": None,
                "can_publish": False,
            }
            for doc_id in missing_ids
        ]
        table = LibraryEntry.__table__
        dialect = db.get_bind().dialect.name
        if dialect == "postgresql":
            statement = pg_insert(table).values(rows).on_conflict_do_nothing(
                index_elements=["user_id", "document_id"]
            ).returning(table.c.document_id)
            inserted_set = {row[0] for row in db.execute(statement).all()}
            inserted_ids = [doc_id for doc_id in missing_ids if doc_id in inserted_set]
        elif dialect == "sqlite":
            statement = sqlite_insert(table).values(rows).on_conflict_do_nothing(
                index_elements=["user_id", "document_id"]
            ).returning(table.c.document_id)
            inserted_set = {row[0] for row in db.execute(statement).all()}
            inserted_ids = [doc_id for doc_id in missing_ids if doc_id in inserted_set]
        else:
            db.execute(table.insert(), rows)
            inserted_ids = missing_ids
    db.commit()
    return {
        "added": len(inserted_ids),
        "already_added": len(document_ids) - len(inserted_ids),
        "document_ids": inserted_ids,
    }


def _get_active_shared_pdf(db: Session, doc_id: int) -> Document:
    doc = _catalog_query(db).filter(Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Shared document not found")
    return doc


@router.get("/{doc_id}/file")
def get_shared_file(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Serve only an actively shared PDF with a non-identifying filename."""
    doc = _get_active_shared_pdf(db, doc_id)
    if not Path(doc.file_path).is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        doc.file_path,
        media_type="application/pdf",
        filename="shared-document.pdf",
    )


@router.get("/{doc_id}/preview")
def get_shared_preview(
    doc_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Serve or queue the asset's shared cover after publication access check."""
    doc = _get_active_shared_pdf(db, doc_id)
    from app.services.document_preview import serve_document_preview

    return serve_document_preview(doc)
