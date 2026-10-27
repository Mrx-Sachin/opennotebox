"""Durable embedding backfill tasks for document retrieval chunks."""
from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import EmbeddingBackfill
from app.services import retrieval

_EMBED_SOFT_TIME_LIMIT = 3600
_EMBED_TIME_LIMIT = 3900
_EMBED_LOCK_NAMESPACE = 2030403


@celery_app.task(
    bind=True,
    max_retries=5,
    soft_time_limit=_EMBED_SOFT_TIME_LIMIT,
    time_limit=_EMBED_TIME_LIMIT,
)
def embed_document_chunks(self, doc_id: int, job_id: int) -> dict:
    """Fill missing document chunk embeddings with retryable task state."""
    db: Session = SessionLocal()
    pg_lock = db.bind is not None and db.bind.dialect.name == "postgresql"
    lock_connection = None
    lock_acquired = False
    try:
        if pg_lock:
            lock_connection = db.get_bind().connect()
            try:
                lock_acquired = bool(
                    lock_connection.execute(
                        sql("SELECT pg_try_advisory_lock(:namespace, :doc_id)"),
                        {
                            "namespace": _EMBED_LOCK_NAMESPACE,
                            "doc_id": int(doc_id),
                        },
                    ).scalar()
                )
                lock_connection.commit()
            except Exception:
                lock_connection.invalidate()
                lock_connection.close()
                lock_connection = None
                raise
            if not lock_acquired:
                lock_connection.close()
                lock_connection = None
                return {"doc_id": doc_id, "status": "already_running"}

        job = (
            db.query(EmbeddingBackfill)
            .filter(
                EmbeddingBackfill.id == job_id,
                EmbeddingBackfill.document_id == doc_id,
            )
            .first()
        )
        if not job:
            return {"doc_id": doc_id, "status": "missing_job"}

        if job.status == "completed":
            missing = _has_unembedded_chunks(db, doc_id)
            if not missing:
                return {"doc_id": doc_id, "status": "completed"}

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.error = None
        db.commit()

        if not retrieval.ensure_embeddings(db, doc_id):
            raise RuntimeError("Embedding service is unavailable")
        if _has_unembedded_chunks(db, doc_id):
            raise RuntimeError("Some document chunks remain unembedded")

        job = db.query(EmbeddingBackfill).filter_by(id=job_id).first()
        if job:
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.error = None
        db.commit()
        return {"doc_id": doc_id, "status": "completed"}
    except Exception as exc:
        db.rollback()
        job = db.query(EmbeddingBackfill).filter_by(id=job_id).first()
        if job:
            job.error = str(exc)[:2000]
            job.started_at = None
            if self.request.retries < self.max_retries:
                job.status = "queued"
            else:
                job.status = "failed"
            db.commit()

        if self.request.retries < self.max_retries:
            logger.warning(
                f"Embedding backfill for doc {doc_id} failed; retrying: {exc}"
            )
            raise self.retry(exc=exc, countdown=min(60 * (2 ** self.request.retries), 900))

        logger.error(f"Embedding backfill for doc {doc_id} failed: {exc}")
        raise
    finally:
        if lock_connection is not None and lock_acquired:
            try:
                lock_connection.execute(
                    sql("SELECT pg_advisory_unlock(:namespace, :doc_id)"),
                    {"namespace": _EMBED_LOCK_NAMESPACE, "doc_id": int(doc_id)},
                )
                lock_connection.commit()
            except Exception:
                logger.exception(f"Could not release embedding lock for doc {doc_id}")
                lock_connection.invalidate()
            finally:
                lock_connection.close()
        elif lock_connection is not None:
            lock_connection.close()
        db.close()


def _has_unembedded_chunks(db: Session, doc_id: int) -> bool:
    return (
        db.execute(
            sql(
                "SELECT 1 FROM chunk WHERE document_id = :d "
                "AND emb IS NULL LIMIT 1"
            ),
            {"d": doc_id},
        ).first()
        is not None
    )
