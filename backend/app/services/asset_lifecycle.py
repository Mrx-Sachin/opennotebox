"""Reference-aware deletion of reusable document assets."""
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import (
    Bookmark,
    Document,
    EmbeddingBackfill,
    Flashcard,
    KgExtractionState,
    LibraryEntry,
    Note,
    Podcast,
    Summary,
    TTSCache,
)
from app.services import retrieval
from app.services.document_preview import preview_path
from app.services.library_access import get_document_for_user, get_library_entry

logger = logging.getLogger(__name__)


def asset_has_active_jobs(db: Session, doc: Document) -> bool:
    if doc.processing_status not in {"completed", "extracted", "failed"}:
        return True
    if db.query(EmbeddingBackfill.id).filter(
        EmbeddingBackfill.document_id == doc.id,
        EmbeddingBackfill.status.in_(["queued", "running"]),
    ).first():
        return True
    if db.query(KgExtractionState.id).filter(
        KgExtractionState.document_id == doc.id,
        KgExtractionState.status.in_(["pending", "extracting"]),
    ).first():
        return True
    return db.query(Summary.id).filter(
        Summary.document_id == doc.id, Summary.status == "generating"
    ).first() is not None


def delete_unreferenced_asset(db: Session, doc: Document) -> str | None:
    """Delete database state under the caller's asset row lock, without committing."""
    if doc.is_shared or db.query(LibraryEntry.id).filter(
        LibraryEntry.document_id == doc.id
    ).first() or asset_has_active_jobs(db, doc):
        return None
    path = doc.file_path
    retrieval.delete_document_chunks(db, doc.id)
    db.delete(doc)
    return path


def remove_asset_files(doc_id: int, path: str) -> None:
    """Remove unreferenced artifacts only after their database deletion commits."""
    if path:
        Path(path).unlink(missing_ok=True)
    preview_path(0, doc_id).unlink(missing_ok=True)


def _cleanup_user_document_children(
    db: Session, user_id: int, doc_id: int
) -> list[str]:
    removable_audio = []
    for model in (TTSCache, Podcast):
        for row in db.query(model).filter(
            model.document_id == doc_id, model.user_id == user_id
        ).all():
            if row.audio_path:
                removable_audio.append(row.audio_path)
            db.delete(row)
    for model in (Note, Bookmark, Flashcard, Summary):
        db.query(model).filter(
            model.document_id == doc_id, model.user_id == user_id
        ).delete(synchronize_session=False)
    return removable_audio


def retire_document_membership(db: Session, user_id: int, doc_id: int) -> None:
    """Retire a user membership and orchestrate unreferenced asset cleanup."""
    get_document_for_user(db, user_id, doc_id)
    doc = db.query(Document).filter(Document.id == doc_id).with_for_update().one()
    entry = get_library_entry(db, user_id, doc_id)
    had_active_jobs = asset_has_active_jobs(db, doc)
    removable_audio = _cleanup_user_document_children(db, user_id, doc_id)
    db.delete(entry)
    if doc.shared_by_user_id == user_id:
        doc.is_shared = False
        doc.shared_by_user_id = None
    db.flush()
    remaining = (
        db.query(LibraryEntry.id)
        .filter(LibraryEntry.document_id == doc_id)
        .count()
    )
    asset_path = None
    if remaining == 0 and not doc.is_shared and not had_active_jobs:
        asset_path = delete_unreferenced_asset(db, doc)
    db.commit()
    for path in removable_audio:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove personal audio artifact", exc_info=True)
    if asset_path:
        remove_asset_files(doc_id, asset_path)
    elif remaining == 0 and not doc.is_shared:
        from app.tasks.asset_tasks import collect_unreferenced_asset

        try:
            collect_unreferenced_asset.apply_async(args=[doc_id], countdown=300)
        except Exception:
            logger.warning(
                "Could not queue asset collection for %s", doc_id, exc_info=True
            )
