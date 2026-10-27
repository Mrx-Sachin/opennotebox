"""Collect assets after their last personal reference and active jobs finish."""
from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, LibraryEntry
from app.services.asset_lifecycle import delete_unreferenced_asset, remove_asset_files


@celery_app.task(bind=True, max_retries=20, soft_time_limit=30, time_limit=45)
def collect_unreferenced_asset(self, doc_id: int):
    with SessionLocal() as db:
        doc = db.query(Document).filter(Document.id == doc_id).with_for_update().first()
        if doc is None or doc.is_shared or db.query(LibraryEntry.id).filter(
            LibraryEntry.document_id == doc_id
        ).first():
            return {"status": "retained_or_missing"}
        path = delete_unreferenced_asset(db, doc)
        if path is None:
            db.rollback()
            raise self.retry(countdown=180)
        db.commit()
    remove_asset_files(doc_id, path)
    return {"status": "collected"}
