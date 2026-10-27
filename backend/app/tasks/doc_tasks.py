"""Celery tasks for DOCX and PPTX document processing."""

from loguru import logger
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, ExtractedText
from app.tasks.pdf_tasks import _chain_summaries
from app.tasks.shared import guard_zip_decompression

# DOCX/PPTX parse budgets, explicit instead of the Celery global (600/900):
# a parse that legitimately exceeds 5 minutes is stuck, not slow.
_DOC_SOFT_TIME_LIMIT = 300
_DOC_TIME_LIMIT = 360


@celery_app.task(
    bind=True,
    max_retries=2,
    soft_time_limit=_DOC_SOFT_TIME_LIMIT,
    time_limit=_DOC_TIME_LIMIT,
)
def parse_docx(self, file_path: str, doc_id: int) -> dict:
    """Parse a DOCX file using python-docx."""
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        doc.processing_status = "parsing"
        db.commit()

        guard_zip_decompression(file_path)
        from docx import Document as DocxDocument

        docx = DocxDocument(file_path)
        paragraphs = docx.paragraphs
        headings = []

        for para in paragraphs:
            if para.style.name.startswith("Heading"):
                try:
                    level = int(para.style.name.replace("Heading ", ""))
                except ValueError:
                    level = 1
                headings.append({"title": para.text, "level": level})

        full_text = "\n".join(p.text for p in paragraphs)
        page_num = 1
        chunk_size = 3000

        for i in range(0, len(full_text), chunk_size):
            chunk = full_text[i : i + chunk_size]
            et = ExtractedText(
                document_id=doc_id,
                page_number=page_num,
                content=chunk,
                bounding_boxes=None,
                headings=headings if page_num == 1 else None,
            )
            db.add(et)
            page_num += 1

        doc.num_pages = max(page_num - 1, 1)
        doc.processing_status = "extracted"
        db.commit()
        logger.info(f"Parsed DOCX document {doc_id}: {doc.num_pages} pages")

        _chain_summaries(doc_id)
        return {"doc_id": doc_id, "num_pages": doc.num_pages, "status": "extracted"}

    except Exception as e:
        logger.error(f"DOCX parse failed for doc {doc_id}: {e}")
        if db:
            d = db.query(Document).filter(Document.id == doc_id).first()
            if d:
                d.processing_status = "failed"
                db.commit()
        raise self.retry(exc=e, countdown=30)
    finally:
        db.close()


@celery_app.task(
    bind=True,
    max_retries=2,
    soft_time_limit=_DOC_SOFT_TIME_LIMIT,
    time_limit=_DOC_TIME_LIMIT,
)
def parse_pptx(self, file_path: str, doc_id: int) -> dict:
    """Parse a PPTX file using python-pptx, slide by slide."""
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        doc.processing_status = "parsing"
        db.commit()

        guard_zip_decompression(file_path)
        from pptx import Presentation as PptxPresentation

        prs = PptxPresentation(file_path)
        headings = []

        for idx, slide in enumerate(prs.slides, 1):
            slide_text = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        txt = para.text.strip()
                        if txt:
                            slide_text.append(txt)
                            if para.level == 0:
                                headings.append({"title": txt, "level": 1})

            et = ExtractedText(
                document_id=doc_id,
                page_number=idx,
                content="\n".join(slide_text),
                bounding_boxes=None,
                headings=headings if idx == 1 else None,
            )
            db.add(et)

        doc.num_pages = len(prs.slides)
        doc.processing_status = "extracted"
        db.commit()
        logger.info(f"Parsed PPTX document {doc_id}: {doc.num_pages} slides")

        _chain_summaries(doc_id)
        return {"doc_id": doc_id, "num_pages": doc.num_pages, "status": "extracted"}

    except Exception as e:
        logger.error(f"PPTX parse failed for doc {doc_id}: {e}")
        if db:
            d = db.query(Document).filter(Document.id == doc_id).first()
            if d:
                d.processing_status = "failed"
                db.commit()
        raise self.retry(exc=e, countdown=30)
    finally:
        db.close()
