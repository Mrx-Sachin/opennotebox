"""Podcast generation router: turns document/section summaries into a two-host audio dialogue."""
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Summary, Podcast, Document, LibraryEntry
from app.schemas import PodcastCreate, PodcastResponse
from app.routers.auth import get_current_user_from_token
from app.tasks.podcast_tasks import generate_podcast as generate_podcast_task
from app.services.library_access import (
    get_document_for_user,
    visible_document_title,
)

router = APIRouter(prefix="/api/podcasts", tags=["podcasts"])


def _serialize(podcast: Podcast, document_title: Optional[str] = None) -> dict:
    data = PodcastResponse.model_validate(podcast).model_dump()
    data["audio_url"] = (
        f"/api/podcasts/{podcast.id}/audio"
        if podcast.status == "completed"
        else None
    )
    if document_title is not None:
        data["document_title"] = document_title
    return data


@router.post("", response_model=None)
def create_podcast(
    body: PodcastCreate,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Create a podcast generation job for a document (full document or a single section)."""
    doc = get_document_for_user(db, user.id, body.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.processing_status != "completed":
        raise HTTPException(status_code=400, detail="Document is not yet fully processed")

    if body.scope == "section":
        if not body.section_title:
            raise HTTPException(status_code=400, detail="section_title is required when scope is 'section'")
        section = db.query(Summary).filter(
            Summary.document_id == doc.id,
            Summary.mode == "section",
            Summary.section_title == body.section_title,
            or_(
                Summary.user_id == user.id,
                (Summary.user_id.is_(None) & (Summary.content == "")),
            ),
        ).first()
        if not section:
            raise HTTPException(status_code=404, detail="Section not found in section summaries")

    existing = db.query(Podcast).filter(
        Podcast.user_id == user.id,
        Podcast.document_id == doc.id,
        Podcast.status == "generating",
    ).first()
    if existing:
        raise HTTPException(
            status_code=429,
            detail="A podcast is already being generated for this document. "
                   "Wait for it to complete before starting another.",
        )

    podcast = Podcast(
        user_id=user.id,
        document_id=doc.id,
        scope=body.scope,
        section_title=body.section_title if body.scope == "section" else None,
        length=body.length,
        status="generating",
    )
    db.add(podcast)
    db.commit()
    db.refresh(podcast)

    generate_podcast_task.delay(podcast_id=podcast.id)

    return _serialize(podcast)


@router.get("", response_model=None)
def list_podcasts(
    document_id: Optional[int] = None,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """List a user's podcasts for a document or across all accessible documents, newest first."""
    if document_id is not None:
        get_document_for_user(db, user.id, document_id)

    query = (
        db.query(Podcast, Document, LibraryEntry)
        .join(Document, Podcast.document_id == Document.id)
        .join(
            LibraryEntry,
            (LibraryEntry.document_id == Document.id)
            & (LibraryEntry.user_id == user.id),
        )
        .filter(Podcast.user_id == user.id)
    )

    if document_id is not None:
        query = query.filter(Podcast.document_id == document_id)

    rows = query.order_by(Podcast.created_at.desc()).all()

    return [
        _serialize(
            podcast,
            document_title=visible_document_title(doc, entry)
            if document_id is None
            else None,
        )
        for podcast, doc, entry in rows
    ]


@router.get("/{podcast_id}", response_model=None)
def get_podcast(
    podcast_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Fetch a single podcast row, for polling generation status."""
    podcast = db.query(Podcast).filter(
        Podcast.id == podcast_id,
        Podcast.user_id == user.id,
    ).first()
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if not get_document_for_user(db, user.id, podcast.document_id):
        raise HTTPException(status_code=404, detail="Podcast not found")
    return _serialize(podcast)


@router.get("/{podcast_id}/audio")
def get_podcast_audio(
    podcast_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Stream the generated podcast WAV file."""
    podcast = db.query(Podcast).filter(
        Podcast.id == podcast_id,
        Podcast.user_id == user.id,
    ).first()
    if not podcast or not get_document_for_user(db, user.id, podcast.document_id) or not podcast.audio_path:
        raise HTTPException(status_code=404, detail="Audio not found")
    if not os.path.exists(podcast.audio_path):
        raise HTTPException(status_code=404, detail="Audio file missing")
    return FileResponse(podcast.audio_path, media_type="audio/wav")


@router.delete("/{podcast_id}")
def delete_podcast(
    podcast_id: int,
    user: User = Depends(get_current_user_from_token),
    db: Session = Depends(get_db),
):
    """Delete a podcast and its audio file."""
    podcast = db.query(Podcast).filter(
        Podcast.id == podcast_id,
        Podcast.user_id == user.id,
    ).first()
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if not get_document_for_user(db, user.id, podcast.document_id):
        raise HTTPException(status_code=404, detail="Podcast not found")

    if podcast.audio_path and os.path.exists(podcast.audio_path):
        try:
            os.remove(podcast.audio_path)
        except OSError:
            pass

    db.delete(podcast)
    db.commit()
    return {"detail": "Deleted"}
