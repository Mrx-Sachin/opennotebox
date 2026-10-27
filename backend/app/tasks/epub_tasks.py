"""Celery task for EPUB document processing (ebooklib + BeautifulSoup).

Chapter/TOC extraction approach adapted from github.com/karpathy/reader3
(MIT), trimmed to the plain-text-per-page contract this app's pipeline
already uses for docx/pptx - dropped image extraction and the standalone
pickle format, since ExtractedText covers what chat/summaries/TTS need.
"""
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Document, ExtractedText
from app.tasks.pdf_tasks import _chain_summaries
from app.tasks.shared import guard_zip_decompression

# Same explicit parse budget as the docx/pptx tasks: a parse that exceeds 5
# minutes is stuck, not slow.
_EBOOK_SOFT_TIME_LIMIT = 300
_EBOOK_TIME_LIMIT = 360

_DROP_TAGS = ["script", "style", "iframe", "video", "nav", "form", "button", "input"]


def _chapter_text(raw_html: str) -> str:
    """Plain text with paragraph breaks preserved.

    BeautifulSoup's get_text(separator=' ') collapses an entire chapter
    into one line; readers (and the sentence splitter TTS relies on) need
    the original block structure instead.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")

    lines = [ln.strip() for ln in soup.get_text(separator="\n").split("\n")]
    collapsed: list = []
    for ln in lines:
        if ln or (collapsed and collapsed[-1]):
            collapsed.append(ln)
    return "\n".join(collapsed).strip()


def _toc_titles_by_file(toc) -> dict:
    """Map spine filename -> chapter title from the book's nav/TOC.

    ebooklib's TOC nodes are Link, Section, or (Section, [children]) tuples;
    the first entry seen for a given file wins so a mid-chapter subheading
    anchor doesn't override the chapter's own title.
    """
    from ebooklib import epub

    titles: dict = {}

    def walk(items):
        for item in items:
            if isinstance(item, tuple):
                section, children = item
                titles.setdefault(section.href.split("#")[0], section.title)
                walk(children)
            elif isinstance(item, (epub.Link, epub.Section)):
                titles.setdefault(item.href.split("#")[0], item.title)

    walk(toc)
    return titles


@celery_app.task(
    bind=True,
    max_retries=2,
    soft_time_limit=_EBOOK_SOFT_TIME_LIMIT,
    time_limit=_EBOOK_TIME_LIMIT,
)
def parse_epub(self, file_path: str, doc_id: int) -> dict:
    """Parse an EPUB: one page per spine chapter, headings from the TOC."""
    db: Session = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise ValueError(f"Document {doc_id} not found")

        doc.processing_status = "parsing"
        db.commit()

        guard_zip_decompression(file_path)
        import ebooklib
        from ebooklib import epub

        book = epub.read_epub(file_path)
        toc_titles = _toc_titles_by_file(book.toc)

        page_num = 0
        for item_id, _linear in book.spine:
            item = book.get_item_with_id(item_id)
            if not item or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue

            raw = item.get_content().decode("utf-8", errors="ignore")
            content = _chapter_text(raw)
            if not content:
                continue

            page_num += 1
            title: Optional[str] = toc_titles.get(item.get_name())
            headings = [{"title": title, "level": 0}] if title else None

            et = ExtractedText(
                document_id=doc_id,
                page_number=page_num,
                content=content,
                bounding_boxes=None,
                headings=headings,
            )
            db.add(et)

        if page_num == 0:
            raise RuntimeError("EPUB produced no readable chapters")

        doc.num_pages = page_num
        doc.processing_status = "extracted"
        db.commit()
        logger.info(f"Parsed EPUB document {doc_id}: {doc.num_pages} chapters")

        _chain_summaries(doc_id)
        return {"doc_id": doc_id, "num_pages": doc.num_pages, "status": "extracted"}

    except Exception as e:
        logger.error(f"EPUB parse failed for doc {doc_id}: {e}")
        if db:
            d = db.query(Document).filter(Document.id == doc_id).first()
            if d:
                d.processing_status = "failed"
                db.commit()
        raise self.retry(exc=e, countdown=30)
    finally:
        db.close()
