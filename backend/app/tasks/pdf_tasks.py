"""Celery tasks for document processing (PDF, TXT, MD).

Parse tasks extract text and store in DB, then chain summary tasks.
"""

from loguru import logger
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, ExtractedText, Summary
from app.services.pdf_parser import PDFParserService
from app.config import settings

pdf_parser = PDFParserService()


def _resolve_ocr() -> bool | None:
    """Convert PDF_OCR_DEFAULT setting to parse_pdf use_ocr value."""
    v = settings.pdf_ocr_default
    if v == "on":
        return True
    if v == "off":
        return False
    return None  # "auto" or any other value


def _chain_summaries(doc_id: int):
    """Dispatch section detection after parsing completes.

    Whole-book page summarization used to auto-queue here too, but for a
    large book that's hours of sequential LLM calls the user never asked
    for. Page summaries are now generated on demand (see
    POST /api/documents/{doc_id}/summaries/page) or in bulk via the explicit
    POST /api/documents/{doc_id}/summaries/pages/all.
    """
    from app.tasks.summary_tasks import generate_section_summaries
    generate_section_summaries.delay(doc_id)


@celery_app.task(bind=True, max_retries=3, soft_time_limit=300)
def parse_pdf_document(self, doc_id: int) -> dict:
    """Parse a PDF document using OpenDataLoader, store extracted text per page."""
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        doc.processing_status = "parsing"
        db.commit()

        layout_mode = getattr(doc, "layout_mode", None) or "auto"
        result = pdf_parser.parse_pdf(
            doc.file_path, use_ocr=_resolve_ocr(), layout_mode=layout_mode
        )
        if result is None:
            raise RuntimeError(f"PDF parsing returned None for document {doc_id}")

        doc.num_pages = result["num_pages"]
        doc.processing_status = "extracting"
        db.commit()

        for page_data in result["pages"]:
            et = ExtractedText(
                document_id=doc_id,
                page_number=page_data["page_number"],
                content=page_data["content"],
                bounding_boxes=page_data.get("bounding_boxes"),
                headings=page_data.get("headings"),
                tables=page_data.get("tables"),
                images=page_data.get("images"),
            )
            db.add(et)

        doc.processing_status = "extracted"
        db.commit()
        logger.info(
            f"Finished parsing document {doc_id}: {doc.num_pages} pages "
            f"(layout_mode={layout_mode})"
        )

        _chain_summaries(doc_id)
        return {"doc_id": doc_id, "num_pages": doc.num_pages, "status": "extracted"}

    except Exception as e:
        logger.error(f"PDF parse failed for doc {doc_id}: {e}")
        if db:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.processing_status = "failed"
                db.commit()
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, soft_time_limit=300)
def reparse_pdf_document(self, doc_id: int) -> dict:
    """Re-extract text from an existing PDF, replacing ExtractedText rows.

    Keeps summaries that already have generated content; clears empty section
    stubs so section detection re-runs with the new text (research-paper
    regex, outline, etc.).
    """
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")
        if doc.format != "pdf":
            raise ValueError(f"Re-parse only supports PDF, got {doc.format}")

        doc.processing_status = "parsing"
        db.commit()

        # Drop previous page extractions so we don't accumulate duplicates.
        db.query(ExtractedText).filter(ExtractedText.document_id == doc_id).delete()
        # The retrieval chunk index has no FK cascade and no staleness
        # tracking - drop it here too, otherwise the next chat keeps
        # retrieving the OLD text (the lazy indexer skips any document that
        # already has chunks).
        from app.services import retrieval

        retrieval.delete_document_chunks(db, doc_id)
        # Drop empty section stubs (no content yet) so detection re-runs.
        # Keep rows that already have generated summary content.
        empty_sections = db.query(Summary).filter(
            Summary.document_id == doc_id,
            Summary.mode == "section",
            Summary.content == "",
        ).all()
        for s in empty_sections:
            db.delete(s)
        db.flush()

        layout_mode = getattr(doc, "layout_mode", None) or "auto"
        result = pdf_parser.parse_pdf(
            doc.file_path, use_ocr=_resolve_ocr(), layout_mode=layout_mode
        )
        if result is None:
            raise RuntimeError(f"PDF re-parse returned None for document {doc_id}")

        doc.num_pages = result["num_pages"]
        for page_data in result["pages"]:
            et = ExtractedText(
                document_id=doc_id,
                page_number=page_data["page_number"],
                content=page_data["content"],
                bounding_boxes=page_data.get("bounding_boxes"),
                headings=page_data.get("headings"),
                tables=page_data.get("tables"),
                images=page_data.get("images"),
            )
            db.add(et)

        doc.processing_status = "extracted"
        db.commit()
        logger.info(
            f"Finished re-parsing document {doc_id}: {doc.num_pages} pages "
            f"(layout_mode={layout_mode})"
        )

        _chain_summaries(doc_id)
        return {
            "doc_id": doc_id,
            "num_pages": doc.num_pages,
            "status": "extracted",
            "layout_mode": layout_mode,
        }

    except Exception as e:
        logger.error(f"PDF re-parse failed for doc {doc_id}: {e}")
        if db:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc:
                doc.processing_status = "failed"
                db.commit()
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()


@celery_app.task
def parse_txt(file_path: str, title: str, doc_id: int) -> dict:
    """Parse plain text file."""
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        doc.processing_status = "parsing"
        db.commit()

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        page_num = 1
        chunk_size = 3000

        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            et = ExtractedText(
                document_id=doc_id,
                page_number=page_num,
                content=chunk,
                bounding_boxes=None,
            )
            db.add(et)
            page_num += 1

        doc.num_pages = max(page_num - 1, 1)
        doc.processing_status = "extracted"
        db.commit()

        _chain_summaries(doc_id)
        return {"doc_id": doc_id, "num_pages": doc.num_pages, "status": "extracted"}
    except Exception as e:
        logger.error(f"TXT parse failed for doc {doc_id}: {e}")
        if db:
            d = db.query(Document).filter(Document.id == doc_id).first()
            if d:
                d.processing_status = "failed"
                db.commit()
        raise
    finally:
        db.close()


@celery_app.task
def parse_md(file_path: str, title: str, doc_id: int) -> dict:
    """Parse markdown file."""
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        doc.processing_status = "parsing"
        db.commit()

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        headings = []
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("#"):
                headings.append({"title": line.lstrip("# ").strip(), "level": len(line) - len(line.lstrip("#"))})

        et = ExtractedText(
            document_id=doc_id,
            page_number=1,
            content=content,
            bounding_boxes=None,
            headings=headings,
        )
        db.add(et)
        doc.num_pages = 1
        doc.processing_status = "extracted"
        db.commit()

        _chain_summaries(doc_id)
        return {"doc_id": doc_id, "num_pages": 1, "status": "extracted"}
    except Exception as e:
        logger.error(f"MD parse failed for doc {doc_id}: {e}")
        if db:
            d = db.query(Document).filter(Document.id == doc_id).first()
            if d:
                d.processing_status = "failed"
                db.commit()
        raise
    finally:
        db.close()
