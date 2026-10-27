"""Discover and persist private chapter lists for document readers."""

from sqlalchemy.orm import Session

from app.models import Document, ExtractedText, Summary
from app.services.summarizer import summarizer


_EXTRACTED_STATUSES = {"extracted", "summarizing_pages", "summarizing_sections", "completed"}


def persist_section_stubs(
    db: Session, doc_id: int, sections_meta: list, user_id: int | None = None
) -> int:
    """Commit missing chapter stubs without replacing generated content."""
    existing_rows = {
        row.section_title: row
        for row in db.query(Summary).filter(
            Summary.document_id == doc_id,
            Summary.user_id == user_id,
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
            user_id=user_id,
            mode="section",
            section_title=title,
            content="",
            start_page=section.get("start_page"),
            end_page=section.get("end_page"),
            level=section.get("level", 0),
        )
        db.add(new_row)
        existing_rows[title] = new_row
        created += 1
    db.commit()
    return created


def ensure_section_stubs(db: Session, doc: Document, user_id: int) -> None:
    """Backfill a member's chapter list while preserving generated summaries.

    The caller must authorize document access before invoking this operation.
    Only chapter metadata is copied between members; content remains private.
    """
    if doc.processing_status not in _EXTRACTED_STATUSES:
        return

    existing = db.query(Summary).filter(
        Summary.document_id == doc.id,
        Summary.mode == "section",
        Summary.user_id == user_id,
    ).all()
    has_real_sections = any(
        section.section_title not in (None, "Full Document", "Remaining")
        for section in existing
    )
    all_levels_null = bool(existing) and all(section.level is None for section in existing)
    if has_real_sections and not all_levels_null:
        return

    if not existing and doc.user_id is not None:
        source = db.query(
            Summary.section_title, Summary.start_page, Summary.end_page,
            Summary.level, Summary.page_number,
        ).filter(
            Summary.document_id == doc.id,
            Summary.mode == "section",
            Summary.user_id == doc.user_id,
        ).all()
        if source:
            for row in source:
                db.add(Summary(
                    document_id=doc.id,
                    user_id=user_id,
                    mode="section",
                    content="",
                    section_title=row.section_title,
                    start_page=row.start_page,
                    end_page=row.end_page,
                    level=row.level,
                    page_number=row.page_number,
                ))
            db.commit()
            return

    pages = db.query(ExtractedText).filter(
        ExtractedText.document_id == doc.id
    ).order_by(ExtractedText.page_number).all()
    if not pages:
        return

    sections_meta = summarizer.detect_sections_metadata(
        [page.content for page in pages],
        [page.headings for page in pages],
        [page.page_number for page in pages],
        file_path=doc.file_path if doc.format == "pdf" else None,
        fmt=doc.format,
    )
    if len(sections_meta) <= 1:
        return

    for section in existing:
        if not section.content:
            db.delete(section)
    db.flush()
    persist_section_stubs(db, doc.id, sections_meta, user_id=user_id)
