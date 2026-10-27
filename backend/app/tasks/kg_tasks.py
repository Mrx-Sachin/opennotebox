"""Celery task for building the knowledge graph (HybridRAG).

Runs the two-tier LLM extraction (abstract -> typed triplets) on a
single-GPU backend, chunk by chunk. Like page summaries, a large document
means many sequential LLM calls, so this task gets its own generous time
budget and commits progress incrementally so a retry only redoes remaining
work. Dispatched from the KG router instead of an in-process BackgroundTask
so the work survives a web-process restart and shows up in the Celery
monitoring.
"""
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, ExtractedText
from app.models.kg import KgExtractionState
from app.services.kg_builder import build_kg_for_document
from app.services.llm_settings import get_active_llm
from app.tasks.shared import run_async

# Many chunks * two LLM calls each against a single-GPU backend. Mirror the
# generous budget page summaries use so a big book is not silently SIGKILLed
# mid-build by the Celery-wide 600s soft limit.
KG_SOFT_TIME_LIMIT = 2 * 60 * 60
KG_TIME_LIMIT = KG_SOFT_TIME_LIMIT + 5 * 60


@celery_app.task(
    bind=True,
    max_retries=2,
    soft_time_limit=KG_SOFT_TIME_LIMIT,
    time_limit=KG_TIME_LIMIT,
)
def build_kg_document(self, doc_id: int, user_id: Optional[int] = None) -> dict:
    """Build the knowledge graph for a document.

    Reads the document's extracted pages fresh and resolves the user's
    active LLM backend at execution time (so a Settings change takes effect
    without the web process being involved). Marks the extraction state
    failed and retries on error.
    """
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        pages = db.query(ExtractedText).filter(
            ExtractedText.document_id == doc_id
        ).order_by(ExtractedText.page_number).all()

        if not pages:
            logger.warning(
                f"No extracted text for document {doc_id}; nothing to build a KG from"
            )
            return {"doc_id": doc_id, "entities": 0, "relationships": 0}

        all_text = "\n\n".join(
            f"[Page {p.page_number}]\n{p.content}" for p in pages if p.content
        )

        active = get_active_llm(user_id or doc.user_id)
        result = run_async(
            build_kg_for_document(
                db,
                doc_id,
                all_text,
                base_url=active["base_url"],
                model_name=active["model"],
                api_key=active["api_key"],
            )
        )
        logger.info(f"KG build completed for doc {doc_id}: {result}")
        return {"doc_id": doc_id, **result}

    except Exception as e:
        logger.error(f"KG build failed for doc {doc_id}: {e}")
        state = db.query(KgExtractionState).filter(
            KgExtractionState.document_id == doc_id
        ).first()
        if state:
            state.status = "failed"
            state.error_message = str(e)
            db.commit()
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()
