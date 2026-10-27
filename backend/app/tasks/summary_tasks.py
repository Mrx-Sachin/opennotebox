"""Celery tasks for summarization."""

from loguru import logger
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, ExtractedText, Summary
from app.services.summarizer import summarizer
from app.services.llm_settings import get_active_llm
from app.tasks.shared import run_async


# Page summarization on a large document (hundreds of pages) means hundreds of
# sequential LLM calls against a remote, single-worker Ollama instance - this can
# legitimately take well over an hour. The Celery-wide default time limits
# (task_soft_time_limit=600 / task_time_limit=900 in celery_app.py) are far too
# short for this task and were silently SIGKILLing the worker mid-run (no
# exception, no retry, doc stuck with 0 page summaries). Give this task its own,
# much larger budget.
PAGE_SUMMARY_SOFT_TIME_LIMIT = 3 * 60 * 60  # 3 hours
PAGE_SUMMARY_TIME_LIMIT = PAGE_SUMMARY_SOFT_TIME_LIMIT + 5 * 60

# How many pages to summarize per LLM-call batch, and how often to commit
# progress to the DB so a retry/kill doesn't lose everything done so far.
PAGE_SUMMARY_BATCH_SIZE = 10


@celery_app.task(
    bind=True,
    max_retries=2,
    soft_time_limit=PAGE_SUMMARY_SOFT_TIME_LIMIT,
    time_limit=PAGE_SUMMARY_TIME_LIMIT,
)
def generate_page_summaries(self, doc_id: int) -> dict:
    """Generate page-level summaries for all pages of a document.

    Idempotent across retries: pages that already have a summary are skipped,
    and results are committed incrementally per batch so a retry only redoes
    the remaining pages instead of starting over.
    """
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        doc.processing_status = "summarizing_pages"
        db.commit()

        pages = db.query(ExtractedText).filter(
            ExtractedText.document_id == doc_id
        ).order_by(ExtractedText.page_number).all()

        if not pages:
            logger.warning(f"No extracted text for document {doc_id}, skipping page summaries")
            return {"doc_id": doc_id, "summary_count": 0}

        already_done = {
            p for (p,) in db.query(Summary.page_number).filter(
                Summary.document_id == doc_id,
                Summary.mode == "page",
            ).all()
        }
        pages = [p for p in pages if p.page_number not in already_done]

        total_generated = len(already_done)
        for start in range(0, len(pages), PAGE_SUMMARY_BATCH_SIZE):
            batch = pages[start:start + PAGE_SUMMARY_BATCH_SIZE]
            batch_contents = [p.content for p in batch]
            batch_summaries = run_async(
                summarizer.summarize_pages_batch(batch_contents, user_id=doc.user_id)
            )

            for page, summary_text in zip(batch, batch_summaries):
                if summary_text:
                    s = Summary(
                        document_id=doc_id,
                        mode="page",
                        page_number=page.page_number,
                        content=summary_text,
                        ai_model=get_active_llm(doc.user_id)["model"],
                    )
                    db.add(s)
                    total_generated += 1

            db.commit()
            logger.info(
                f"doc {doc_id}: page summaries {min(start + PAGE_SUMMARY_BATCH_SIZE, len(pages))}/{len(pages)} done this run"
            )

        logger.info(f"Generated {total_generated} page summaries for doc {doc_id}")
        return {"doc_id": doc_id, "summary_count": total_generated}

    except Exception as e:
        logger.error(f"Page summarization failed for doc {doc_id}: {e}")
        if db:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.processing_status = "failed"
                db.commit()
        raise self.retry(exc=e, countdown=30)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=1, soft_time_limit=600, time_limit=660)
def generate_single_page_summary(self, doc_id: int, page_number: int) -> dict:
    """Generate the content for one on-demand page summary. Idempotent: if
    the page already has non-empty content, this is a no-op. Mirrors
    generate_single_section_summary's pattern - the router creates the stub
    row (status="generating") before dispatching this task.
    """
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        summary = db.query(Summary).filter(
            Summary.document_id == doc_id,
            Summary.mode == "page",
            Summary.page_number == page_number,
        ).first()
        if not summary:
            raise ValueError(f"Page summary stub for doc {doc_id} page {page_number} not found")

        if summary.content:
            return {"doc_id": doc_id, "page_number": page_number, "status": "already_generated"}

        page = db.query(ExtractedText).filter(
            ExtractedText.document_id == doc_id,
            ExtractedText.page_number == page_number,
        ).first()
        if not page:
            summary.status = "failed"
            db.commit()
            raise ValueError(f"Page {page_number} not found for doc {doc_id}")

        summary_text = run_async(summarizer.summarize_page(page.content, user_id=doc.user_id))

        if not summary_text:
            summary.status = "failed"
            db.commit()
            logger.warning(
                f"Page summary generation produced no usable content for doc {doc_id} page {page_number}"
            )
            return {"doc_id": doc_id, "page_number": page_number, "status": "failed"}

        summary.content = summary_text
        summary.ai_model = get_active_llm(doc.user_id)["model"]
        summary.status = None
        db.commit()
        logger.info(f"Generated page summary for doc {doc_id} page {page_number}")
        return {"doc_id": doc_id, "page_number": page_number, "status": "completed"}

    except Exception as e:
        logger.error(f"Single page summary failed for doc {doc_id} page {page_number}: {e}")
        if db:
            s = db.query(Summary).filter(
                Summary.document_id == doc_id,
                Summary.mode == "page",
                Summary.page_number == page_number,
            ).first()
            if s:
                s.status = "failed"
                db.commit()
        raise
    finally:
        db.close()


def persist_section_stubs(db: Session, doc_id: int, sections_meta: list) -> int:
    """Insert empty-content Summary "stub" rows for newly detected sections, so
    the chapter list is visible immediately without generating any content.
    Idempotent: a section whose title already has a row (stub or generated)
    keeps its content untouched. Returns the number of new stubs created.

    A row that already has generated content is never deleted (that would
    throw away real work), but if it predates the `level` column (level is
    NULL) and a freshly detected section has the same title, its level is
    backfilled in place - level is just a display/indent hint, safe to set
    without touching content.
    """
    existing_rows = {
        row.section_title: row
        for row in db.query(Summary).filter(
            Summary.document_id == doc_id,
            Summary.mode == "section",
        ).all()
    }
    created = 0
    for section in sections_meta:
        title = section["title"]
        existing = existing_rows.get(title)
        if existing is not None:
            if existing.level is None:
                existing.level = section.get("level", 0)
            continue
        new_row = Summary(
            document_id=doc_id,
            mode="section",
            section_title=title,
            content="",
            start_page=section.get("start_page"),
            end_page=section.get("end_page"),
            level=section.get("level", 0),
        )
        db.add(new_row)
        existing_rows[title] = new_row  # dedupe a repeated title later in this same list
        created += 1
    db.commit()
    return created


@celery_app.task(bind=True, max_retries=2, soft_time_limit=120, time_limit=180)
def generate_section_summaries(self, doc_id: int) -> dict:
    """Detect a document's sections (chapters) and store them as empty-content
    stub rows, so the chapter list is visible in the UI right after extraction.

    This intentionally does NOT generate any section content - that used to
    happen here via hundreds of sequential LLM calls, which meant a big book's
    section list stayed invisible for as long as the whole-book job ran. Content
    for a given section is now generated on demand by
    generate_single_section_summary, once the user picks a chapter to read.
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
            logger.warning(f"No extracted text for document {doc_id}, skipping section detection")
            doc.processing_status = "completed"
            db.commit()
            return {"doc_id": doc_id, "section_count": 0}

        page_texts = [p.content for p in pages]
        page_headings = [p.headings for p in pages]
        page_numbers = [p.page_number for p in pages]

        sections_meta = summarizer.detect_sections_metadata(
            page_texts,
            page_headings,
            page_numbers,
            file_path=doc.file_path if doc.format == "pdf" else None,
            fmt=doc.format,
        )

        created = persist_section_stubs(db, doc_id, sections_meta)

        doc.processing_status = "completed"
        db.commit()
        logger.info(f"Detected {len(sections_meta)} sections for doc {doc_id} ({created} new)")
        return {"doc_id": doc_id, "section_count": len(sections_meta)}

    except Exception as e:
        logger.error(f"Section detection failed for doc {doc_id}: {e}")
        if db:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.processing_status = "failed"
                db.commit()
        raise self.retry(exc=e, countdown=30)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=1, soft_time_limit=600, time_limit=660)
def generate_single_section_summary(self, doc_id: int, section_title: str) -> dict:
    """Generate the content for one on-demand section summary - the "read just
    this chapter" path. Idempotent: if the section already has non-empty
    content, this is a no-op.
    """
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        summary = db.query(Summary).filter(
            Summary.document_id == doc_id,
            Summary.mode == "section",
            Summary.section_title == section_title,
        ).first()
        if not summary:
            raise ValueError(f"Section '{section_title}' not found for document {doc_id}")

        if summary.content:
            return {"doc_id": doc_id, "section_title": section_title, "status": "already_generated"}

        summary.status = "generating"
        db.commit()

        query = db.query(ExtractedText).filter(ExtractedText.document_id == doc_id)
        if summary.start_page is not None:
            query = query.filter(ExtractedText.page_number >= summary.start_page)
        if summary.end_page is not None:
            query = query.filter(ExtractedText.page_number <= summary.end_page)
        pages = query.order_by(ExtractedText.page_number).all()

        section_content = "\n".join(p.content for p in pages)
        summary_text = run_async(
            summarizer.summarize_section(section_content, section_title, user_id=doc.user_id)
        )

        if not summary_text:
            summary.status = "failed"
            db.commit()
            logger.warning(
                f"Section summary generation produced no usable content for doc {doc_id} "
                f"section '{section_title}'"
            )
            return {"doc_id": doc_id, "section_title": section_title, "status": "failed"}

        summary.content = summary_text
        summary.ai_model = get_active_llm(doc.user_id)["model"]
        summary.status = None
        db.commit()
        logger.info(f"Generated section summary for doc {doc_id} section '{section_title}'")
        return {"doc_id": doc_id, "section_title": section_title, "status": "completed"}

    except Exception as e:
        logger.error(f"Single section summary failed for doc {doc_id} section '{section_title}': {e}")
        if db:
            s = db.query(Summary).filter(
                Summary.document_id == doc_id,
                Summary.mode == "section",
                Summary.section_title == section_title,
            ).first()
            if s:
                s.status = "failed"
                db.commit()
        raise
    finally:
        db.close()


@celery_app.task
def process_document_pipeline(doc_id: int) -> dict:
    """Run the full document processing pipeline: parse -> page summaries -> section summaries."""
    from app.tasks.pdf_tasks import parse_pdf_document

    parse_pdf_document.delay(doc_id)
    return {"doc_id": doc_id, "pipeline": "started"}
