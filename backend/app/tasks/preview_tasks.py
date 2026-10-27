"""Generate first-page PDF covers in the worker process."""
import logging

import redis

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models import Document
from app.services.document_preview import preview_job_key, preview_path, render_pdf_cover

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.preview_tasks.generate_document_preview", soft_time_limit=30,
                 time_limit=45)
def generate_document_preview(doc_id: int) -> None:
    """Cache a cover while serializing against document deletion."""
    succeeded = False
    with SessionLocal() as db:
        doc = db.query(Document).filter(Document.id == doc_id).with_for_update().first()
        if doc is None:
            succeeded = True
        elif doc.format == "pdf":
            destination = preview_path(doc.user_id, doc.id)
            try:
                if not destination.exists():
                    render_pdf_cover(doc.file_path, destination)
                db.commit()
                succeeded = True
            except Exception:
                logger.exception("Could not generate cover for document %s", doc_id)
                db.rollback()
    if succeeded:
        try:
            with redis.Redis.from_url(settings.celery_broker_url, socket_timeout=2) as client:
                client.delete(preview_job_key(doc_id))
        except redis.RedisError:
            logger.warning("Could not clear preview lease for document %s", doc_id)
