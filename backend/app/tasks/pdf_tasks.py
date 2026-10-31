"""Celery tasks for document processing (PDF, TXT, MD).

Parse tasks extract text and store in DB, then chain summary tasks.
"""

from loguru import logger
from datetime import datetime, timezone
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, ExtractedText, Summary
from app.services.pdf_parser import PDFParserService
from app.config import settings
from app.services.narration import compute_narration_exclusions

pdf_parser = PDFParserService()


def invalidate_layout(doc: Document) -> None:
    """Make any in-flight layout result obsolete before replacing text."""
    doc.layout_status = "not_requested"
    doc.layout_task_id = None
    doc.layout_error = None
    doc.layout_requested_at = None


@celery_app.task(bind=True, soft_time_limit=1800, time_limit=1860)
def analyze_document_layout(self, doc_id: int, task_id: str) -> dict:
    """Enrich persisted PDF geometry once, without extracting text again."""
    import pdfplumber

    db = SessionLocal()
    try:
        claimed = db.query(Document).filter(
            Document.id == doc_id,
            Document.layout_task_id == task_id,
            Document.layout_status == "queued",
        ).update({Document.layout_status: "running", Document.layout_requested_at: datetime.now(timezone.utc)}, synchronize_session=False)
        db.commit()
        if not claimed:
            return {"doc_id": doc_id, "status": "ignored"}
        doc = db.query(Document).filter(Document.id == doc_id).one()
        pages = db.query(ExtractedText).filter(
            ExtractedText.document_id == doc_id
        ).order_by(ExtractedText.page_number).all()
        file_path, num_pages = doc.file_path, doc.num_pages
        page_inputs = [
            (page.id, page.page_number, page.content, page.bounding_boxes)
            for page in pages
        ]
        # Do not hold a database transaction during CPU-heavy inference.
        db.commit()
        regions = pdf_parser._detect_docling_regions(file_path, num_pages)
        if not isinstance(regions, dict):
            raise RuntimeError("Layout detector returned no result")
        enriched = []
        with pdfplumber.open(file_path) as pdf:
            for page_id, page_number, content, bounding_boxes in page_inputs:
                # Match the MediaBox/rotation geometry used by extraction.
                pdf_page = pdf.pages[page_number - 1]
                page_regions = regions.get(page_number, [])
                exclusions = compute_narration_exclusions(
                    content, bounding_boxes, page_regions,
                    page_width=float(pdf_page.width), page_height=float(pdf_page.height),
                )
                enriched.append((page_id, page_regions, exclusions))
        doc = db.query(Document).filter(Document.id == doc_id).with_for_update().first()
        if not doc or doc.layout_task_id != task_id or doc.layout_status != "running":
            return {"doc_id": doc_id, "status": "ignored"}
        for page_id, page_regions, exclusions in enriched:
            db.query(ExtractedText).filter(
                ExtractedText.id == page_id, ExtractedText.document_id == doc_id
            ).update({"layout_regions": page_regions, "narration_exclusions": exclusions}, synchronize_session=False)
        doc.layout_status = "ready"
        doc.layout_error = None
        db.commit()
        return {"doc_id": doc_id, "status": "ready"}
    except Exception:
        db.rollback()
        logger.exception("Optional layout analysis failed for document {}", doc_id)
        db.query(Document).filter(
            Document.id == doc_id, Document.layout_task_id == task_id,
            Document.layout_status == "running",
        ).update({"layout_status": "failed", "layout_error": "Layout analysis failed. Try again."}, synchronize_session=False)
        db.commit()
        return {"doc_id": doc_id, "status": "failed"}
    finally:
        db.close()


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
    try:
        generate_section_summaries.delay(doc_id)
    except Exception:
        logger.exception("Summary dispatch failed for extracted document {}", doc_id)


def _replace_extracted_pages(db: Session, doc_id: int, pages: list[dict]) -> None:
    """Replace one document's extracted pages and invalidate its retrieval index.

    Parse retries and duplicate queue deliveries are normal Celery behavior.
    Replacing inside one transaction keeps them from duplicating page rows or
    serving chunks built from an older extraction.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            sql("SELECT pg_advisory_xact_lock(:namespace, :doc_id)"),
            {"namespace": 2030401, "doc_id": int(doc_id)},
        )

    from app.services import retrieval

    doc = db.query(Document).filter(Document.id == doc_id).with_for_update().one()
    invalidate_layout(doc)

    db.query(ExtractedText).filter(
        ExtractedText.document_id == doc_id
    ).delete(synchronize_session=False)
    retrieval.delete_document_chunks(db, doc_id)

    db.add_all(
        ExtractedText(
            document_id=doc_id,
            page_number=page["page_number"],
            content=page["content"],
            bounding_boxes=page.get("bounding_boxes"),
            headings=page.get("headings"),
            tables=page.get("tables"),
            images=page.get("images"),
            layout_regions=page.get("layout_regions"),
            narration_exclusions=page.get("narration_exclusions"),
        )
        for page in pages
    )


@celery_app.task(bind=True, max_retries=3, soft_time_limit=300)
def parse_pdf_document(self, doc_id: int) -> dict:
    """Extract PDF text and geometry, then queue automatic section summaries."""
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

        _replace_extracted_pages(db, doc_id, result["pages"])
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

        layout_mode = getattr(doc, "layout_mode", None) or "auto"
        result = pdf_parser.parse_pdf(
            doc.file_path, use_ocr=_resolve_ocr(), layout_mode=layout_mode
        )
        if result is None:
            raise RuntimeError(f"PDF re-parse returned None for document {doc_id}")

        _replace_extracted_pages(db, doc_id, result["pages"])
        # Drop empty section stubs so section detection re-runs with the new
        # text. Keep rows that already have generated summary content.
        empty_sections = db.query(Summary).filter(
            Summary.document_id == doc_id,
            Summary.mode == "section",
            Summary.content == "",
        ).all()
        for summary in empty_sections:
            db.delete(summary)

        doc.num_pages = result["num_pages"]
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

        pages = []
        page_num = 1
        chunk_size = 3000

        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            pages.append({"page_number": page_num, "content": chunk})
            page_num += 1

        _replace_extracted_pages(db, doc_id, pages)
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

        _replace_extracted_pages(
            db,
            doc_id,
            [{"page_number": 1, "content": content, "headings": headings}],
        )
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
