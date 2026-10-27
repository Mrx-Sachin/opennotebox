"""Reference-aware deletion of reusable document assets."""
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Document, EmbeddingBackfill, KgExtractionState, LibraryEntry, Summary
from app.services import retrieval
from app.services.document_preview import preview_path


def asset_has_active_jobs(db: Session, doc: Document) -> bool:
    if doc.processing_status not in {"completed", "extracted", "failed"}:
        return True
    if db.query(EmbeddingBackfill.id).filter(
        EmbeddingBackfill.document_id == doc.id, EmbeddingBackfill.status == "running"
    ).first():
        return True
    if db.query(KgExtractionState.id).filter(
        KgExtractionState.document_id == doc.id, KgExtractionState.status == "extracting"
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
