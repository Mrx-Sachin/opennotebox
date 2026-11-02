"""Service for safe maintenance repair of legacy Docling PDF extractions.

Restores modern word-level bounding boxes and untouched line extraction for
documents with legacy Docling paragraph boxes. Preserves document identity,
ownership, library entries, notes, bookmarks, conversations, summaries,
and cached layout regions.
"""

from __future__ import annotations

from datetime import datetime, timezone
import enum
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
from sqlalchemy import inspect as sa_inspect, text as sql
from sqlalchemy.orm import Session

from app.models import Document, EmbeddingBackfill, ExtractedText, Summary
from app.services.narration import compute_narration_exclusions
from app.services.pdf_parser import PDFParserService
from app.services import retrieval

logger = logging.getLogger(__name__)


class BoxFormat(str, enum.Enum):
    """Classification of bounding box formats."""

    LEGACY = "legacy"
    CURRENT = "current"
    EMPTY = "empty"
    MIXED = "mixed"
    UNSUPPORTED = "unsupported"


def compute_file_sha256(file_path: str) -> str:
    """Compute SHA-256 hexadecimal digest of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def classify_page_boxes(boxes: Any) -> Tuple[BoxFormat, Dict[str, Any]]:
    """Classify bounding boxes on a single page."""
    if boxes is None or (isinstance(boxes, list) and len(boxes) == 0):
        return BoxFormat.EMPTY, {
            "total_boxes": 0,
            "single_word_boxes": 0,
            "multi_word_boxes": 0,
            "avg_chars": 0.0,
            "max_chars": 0,
        }

    if not isinstance(boxes, list):
        return BoxFormat.UNSUPPORTED, {
            "error": f"Bounding boxes is not a list (got {type(boxes).__name__})",
        }

    total_boxes = len(boxes)
    single_word_boxes = 0
    multi_word_boxes = 0
    total_chars = 0
    max_chars = 0

    for box in boxes:
        if not isinstance(box, dict) or "text" not in box or "bbox" not in box:
            return BoxFormat.UNSUPPORTED, {"error": "Malformed box item"}

        bbox = box.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return BoxFormat.UNSUPPORTED, {"error": f"Invalid bbox coordinates: {bbox}"}
        if not all(isinstance(c, (int, float)) for c in bbox):
            return BoxFormat.UNSUPPORTED, {"error": f"Non-numeric bbox coordinates: {bbox}"}

        raw_text = str(box.get("text", "")).strip()
        text_len = len(raw_text)
        total_chars += text_len
        if text_len > max_chars:
            max_chars = text_len

        words = raw_text.split()
        if len(words) >= 2:
            multi_word_boxes += 1
        elif len(words) == 1:
            single_word_boxes += 1

    avg_chars = total_chars / total_boxes if total_boxes > 0 else 0.0
    stats = {
        "total_boxes": total_boxes,
        "single_word_boxes": single_word_boxes,
        "multi_word_boxes": multi_word_boxes,
        "avg_chars": avg_chars,
        "max_chars": max_chars,
    }

    if multi_word_boxes >= 3 and single_word_boxes >= 25:
        return BoxFormat.MIXED, stats

    if multi_word_boxes > 0 and (
        single_word_boxes == 0
        or multi_word_boxes >= single_word_boxes
        or avg_chars >= 20.0
        or (total_boxes <= 30 and multi_word_boxes >= 2)
    ):
        return BoxFormat.LEGACY, stats

    if single_word_boxes > 0 and (
        multi_word_boxes == 0 or (multi_word_boxes <= 2 and single_word_boxes > 50)
    ):
        return BoxFormat.CURRENT, stats

    if total_boxes == 0:
        return BoxFormat.EMPTY, stats

    return BoxFormat.UNSUPPORTED, stats


def classify_document_boxes(
    pages: List[ExtractedText],
) -> Tuple[BoxFormat, List[Dict[str, Any]]]:
    """Classify bounding box format across all document pages."""
    page_stats: List[Dict[str, Any]] = []
    page_formats: List[BoxFormat] = []

    for page in pages:
        fmt, stats = classify_page_boxes(page.bounding_boxes)
        stats["page_number"] = page.page_number
        stats["format"] = fmt.value
        page_stats.append(stats)
        page_formats.append(fmt)

    if any(f == BoxFormat.UNSUPPORTED for f in page_formats):
        return BoxFormat.UNSUPPORTED, page_stats

    if any(f == BoxFormat.MIXED for f in page_formats):
        return BoxFormat.MIXED, page_stats

    has_legacy = any(f == BoxFormat.LEGACY for f in page_formats)
    has_current = any(f == BoxFormat.CURRENT for f in page_formats)

    if has_legacy and has_current:
        return BoxFormat.MIXED, page_stats

    if has_legacy:
        return BoxFormat.LEGACY, page_stats

    if has_current:
        return BoxFormat.CURRENT, page_stats

    return BoxFormat.EMPTY, page_stats


def check_document_active_work(db: Session, doc: Document) -> List[str]:
    """Refuse repair if document, layout, embedding, summary, or KG has active work."""
    active: List[str] = []

    # Completed, extracted, and failed are idle; queued, parsing, extracting, pending are active
    if doc.processing_status not in {"completed", "extracted", "failed"}:
        active.append(f"document processing_status is '{doc.processing_status}'")

    # Ready, not_requested, and failed are idle; queued, running, pending are active
    if doc.layout_status in {"queued", "running", "pending"}:
        active.append(f"document layout_status is '{doc.layout_status}'")

    # Refuse active embedding backfill (pending, queued, or running)
    active_backfill = (
        db.query(EmbeddingBackfill)
        .filter(
            EmbeddingBackfill.document_id == doc.id,
            EmbeddingBackfill.status.in_(["pending", "queued", "running"]),
        )
        .first()
    )
    if active_backfill:
        active.append(
            f"embedding backfill job {active_backfill.id} is '{active_backfill.status}'"
        )

    # Refuse active summary generation
    active_summary = (
        db.query(Summary)
        .filter(
            Summary.document_id == doc.id,
            Summary.status.in_(["generating", "queued", "running"]),
        )
        .first()
    )
    if active_summary:
        active.append("document summary generation is active")

    # Check kg_extraction_state singular table
    if sa_inspect(db.bind).has_table("kg_extraction_state"):
        from app.models.kg import KgExtractionState

        active_kg = (
            db.query(KgExtractionState)
            .filter(
                KgExtractionState.document_id == doc.id,
                KgExtractionState.status.in_(["pending", "extracting", "queued", "running"]),
            )
            .first()
        )
        if active_kg:
            active.append(f"knowledge graph extraction is '{active_kg.status}'")

    return active


def create_repair_backup(
    db: Session,
    doc: Document,
    expected_sha256: str,
    actual_sha256: str,
    backup_path: str,
) -> Dict[str, Any]:
    """Write exclusive pre-repair snapshot with 0600 permissions before mutating DB."""
    if not backup_path:
        raise ValueError("Backup path must be provided")

    if os.path.exists(backup_path):
        raise FileExistsError(
            f"Backup file already exists: {backup_path}. Exclusive creation required."
        )

    parent_dir = os.path.dirname(os.path.abspath(backup_path))
    os.makedirs(parent_dir, exist_ok=True)

    doc_data = {
        "id": doc.id,
        "user_id": doc.user_id,
        "title": doc.title,
        "format": doc.format,
        "file_path": doc.file_path,
        "file_size": doc.file_size,
        "num_pages": doc.num_pages,
        "processing_status": doc.processing_status,
        "layout_mode": doc.layout_mode,
        "layout_status": doc.layout_status,
        "layout_task_id": doc.layout_task_id,
        "layout_error": doc.layout_error,
        "layout_requested_at": (
            doc.layout_requested_at.isoformat()
            if doc.layout_requested_at
            else None
        ),
        "content_hash": doc.content_hash,
        "is_shared": doc.is_shared,
        "shared_at": doc.shared_at.isoformat() if doc.shared_at else None,
        "shared_title": doc.shared_title,
        "author": doc.author,
        "description": doc.description,
        "topics": doc.topics,
        "document_type": doc.document_type,
        "language": doc.language,
        "shared_by_user_id": doc.shared_by_user_id,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "last_read_page": doc.last_read_page,
        "last_read_position": doc.last_read_position,
        "last_read_at": (
            doc.last_read_at.isoformat() if doc.last_read_at else None
        ),
    }

    existing_pages = (
        db.query(ExtractedText)
        .filter(ExtractedText.document_id == doc.id)
        .order_by(ExtractedText.page_number)
        .all()
    )
    pages_data = [
        {
            "id": p.id,
            "document_id": p.document_id,
            "page_number": p.page_number,
            "content": p.content,
            "bounding_boxes": p.bounding_boxes,
            "headings": p.headings,
            "tables": p.tables,
            "images": p.images,
            "layout_regions": p.layout_regions,
            "narration_exclusions": p.narration_exclusions,
        }
        for p in existing_pages
    ]

    backfill = (
        db.query(EmbeddingBackfill)
        .filter(EmbeddingBackfill.document_id == doc.id)
        .first()
    )
    backfill_data = None
    if backfill:
        backfill_data = {
            "id": backfill.id,
            "document_id": backfill.document_id,
            "status": backfill.status,
            "requested_at": (
                backfill.requested_at.isoformat()
                if backfill.requested_at
                else None
            ),
            "started_at": (
                backfill.started_at.isoformat()
                if backfill.started_at
                else None
            ),
            "completed_at": (
                backfill.completed_at.isoformat()
                if backfill.completed_at
                else None
            ),
            "error": backfill.error,
        }

    chunk_count = 0
    if sa_inspect(db.bind).has_table("chunk"):
        result = db.execute(
            sql("SELECT count(*) FROM chunk WHERE document_id = :d"),
            {"d": doc.id},
        ).scalar()
        chunk_count = int(result or 0)

    backup_payload = {
        "backup_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "document_id": doc.id,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
        "document": doc_data,
        "extracted_text": pages_data,
        "embedding_backfill": backfill_data,
        "chunk_count": chunk_count,
        "note": (
            "Chunks are derivative search index rows regenerable via "
            "retrieval.index_document; extracted_text and embedding_backfill rows "
            "are fully captured for restoration."
        ),
    }

    fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(backup_payload, f, indent=2, default=str)

    return {
        "backup_path": backup_path,
        "page_count": len(pages_data),
        "chunk_count": chunk_count,
    }


def _validate_fresh_pages(
    fresh_pages: List[Dict[str, Any]], expected_count: int, doc_num_pages: int
) -> None:
    """Validate fresh parser pages sequence, count, and modern box format."""
    if len(fresh_pages) != expected_count:
        raise ValueError(
            f"Fresh parse page count ({len(fresh_pages)}) does not match "
            f"database page count ({expected_count})"
        )

    if doc_num_pages > 0 and len(fresh_pages) != doc_num_pages:
        raise ValueError(
            f"Fresh parse page count ({len(fresh_pages)}) does not match "
            f"document num_pages ({doc_num_pages})"
        )

    for idx, page in enumerate(fresh_pages, 1):
        if page.get("page_number") != idx:
            raise ValueError(
                f"Fresh parse page sequence mismatch: expected page {idx}, "
                f"got {page.get('page_number')}"
            )
        fmt, _ = classify_page_boxes(page.get("bounding_boxes"))
        if fmt not in {BoxFormat.CURRENT, BoxFormat.EMPTY}:
            raise RuntimeError(
                f"Fresh parse produced non-modern bounding box format '{fmt.value}' "
                f"on page {idx}"
            )


def repair_legacy_pdf_extraction(
    db: Session,
    document_id: int,
    expected_sha256: str,
    backup_path: Optional[str] = None,
    apply: bool = False,
    file_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect and optionally perform transactional repair of legacy Docling extraction.

    In apply mode, holds row and advisory locks, refreshes active job checks,
    validates SHA-256 before and after parse, creates exclusive 0600 backup,
    updates ExtractedText rows in place (preserving IDs), replaces stale Docling
    tables/images, recomputes narration exclusions from cached layout regions and
    geometry, and clears stale chunks/backfills.
    """
    if apply and not backup_path:
        raise ValueError("backup_path is required when apply=True")

    # In apply mode, open transaction and acquire locks first
    if apply:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                sql("SELECT pg_advisory_xact_lock(:namespace, :doc_id)"),
                {"namespace": 2030401, "doc_id": int(document_id)},
            )
        doc = (
            db.query(Document)
            .filter(Document.id == document_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
    else:
        doc = db.query(Document).filter(Document.id == document_id).first()

    if not doc:
        raise ValueError(f"Document {document_id} not found in database")

    # Check active work under lock
    active_reasons = check_document_active_work(db, doc)
    if active_reasons:
        raise RuntimeError(
            f"Cannot repair document {document_id}: active work in progress "
            f"({'; '.join(active_reasons)})"
        )

    resolved_path = file_path or doc.file_path
    if not os.path.isfile(resolved_path):
        raise FileNotFoundError(
            f"Source PDF file not found at '{resolved_path}'"
        )

    # Initial hash verification
    actual_sha256 = compute_file_sha256(resolved_path)
    if actual_sha256.lower() != expected_sha256.lower():
        raise ValueError(
            f"SHA-256 mismatch for document {document_id}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    if doc.content_hash and doc.content_hash.lower() != expected_sha256.lower():
        raise ValueError(
            f"Document content_hash '{doc.content_hash}' does not match "
            f"expected SHA-256 '{expected_sha256}'"
        )

    # Fetch stored pages (with row lock if apply)
    if apply:
        existing_pages = (
            db.query(ExtractedText)
            .filter(ExtractedText.document_id == document_id)
            .order_by(ExtractedText.page_number)
            .populate_existing()
            .with_for_update()
            .all()
        )
    else:
        existing_pages = (
            db.query(ExtractedText)
            .filter(ExtractedText.document_id == document_id)
            .order_by(ExtractedText.page_number)
            .all()
        )

    if not existing_pages:
        raise ValueError(
            f"Document {document_id} has no extracted text pages in database"
        )

    for idx, page in enumerate(existing_pages, 1):
        if page.page_number != idx:
            raise ValueError(
                f"Stored pages sequence is not contiguous: expected page {idx}, "
                f"got page {page.page_number}"
            )

    classification, page_stats = classify_document_boxes(existing_pages)

    # Idempotent no-op when already current
    if classification == BoxFormat.CURRENT:
        logger.info(
            f"Document {document_id} extraction is already current. No repair needed."
        )
        return {
            "applied": False,
            "noop": True,
            "document_id": document_id,
            "num_pages": len(existing_pages),
            "reason": (
                "Document bounding boxes are already in current word-level format."
            ),
        }

    # Only explicit legacy format triggers repair. Refuse empty, mixed, or unsupported.
    if classification != BoxFormat.LEGACY:
        raise ValueError(
            f"Refusing repair: document {document_id} has '{classification.value}' "
            f"bounding box format across its pages."
        )

    # Fresh extraction using untouched lines and word boxes
    parser = PDFParserService()
    fresh_result = parser.parse_pdf(
        resolved_path, layout_mode=doc.layout_mode or "auto"
    )
    if not fresh_result or "pages" not in fresh_result:
        raise RuntimeError("PDFParserService returned invalid parse result")

    fresh_pages = fresh_result["pages"]
    _validate_fresh_pages(fresh_pages, len(existing_pages), doc.num_pages or 0)

    # Validate file hash again after parse to prevent race or tampering
    post_parse_sha256 = compute_file_sha256(resolved_path)
    if post_parse_sha256.lower() != expected_sha256.lower():
        raise ValueError(
            f"SHA-256 changed during parse for document {document_id}: "
            f"expected {expected_sha256}, got {post_parse_sha256}"
        )

    # Dry-run report
    if not apply:
        return {
            "applied": False,
            "dry_run": True,
            "document_id": document_id,
            "title": doc.title,
            "num_pages": len(existing_pages),
            "layout_status": doc.layout_status,
            "classification": classification.value,
            "pages_to_repair": len(fresh_pages),
            "page_stats": page_stats,
            "sha256_verified": True,
        }

    # Apply mode: write backup while holding locks, before mutating database
    assert backup_path is not None
    backup_meta = create_repair_backup(
        db=db,
        doc=doc,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        backup_path=backup_path,
    )

    try:
        pdf_dimensions: Dict[int, Tuple[float, float]] = {}
        if doc.layout_status == "ready":
            with pdfplumber.open(resolved_path) as pdf:
                for idx, page in enumerate(pdf.pages, 1):
                    pdf_dimensions[idx] = (
                        float(page.width),
                        float(page.height or 0.0),
                    )

        existing_page_map = {p.page_number: p for p in existing_pages}
        recomputed_exclusions_count = 0

        for fresh_page in fresh_pages:
            page_number = fresh_page["page_number"]
            row = existing_page_map.get(page_number)
            if not row:
                raise ValueError(
                    f"Existing page {page_number} not found during update"
                )

            # Preserve ready layout_regions and recompute narration exclusions
            if doc.layout_status == "ready" and row.layout_regions:
                pw, ph = pdf_dimensions.get(page_number, (612.0, 792.0))
                exclusions = compute_narration_exclusions(
                    content=fresh_page["content"],
                    words=fresh_page["bounding_boxes"],
                    layout_regions=row.layout_regions,
                    page_width=pw,
                    page_height=ph,
                )
                if exclusions:
                    recomputed_exclusions_count += len(exclusions)
                row.layout_regions = row.layout_regions
                row.narration_exclusions = exclusions
            else:
                row.layout_regions = (
                    row.layout_regions if doc.layout_status == "ready" else []
                )
                row.narration_exclusions = []

            # In-place update preserving ExtractedText.id
            row.content = fresh_page["content"]
            row.bounding_boxes = fresh_page["bounding_boxes"]
            row.headings = fresh_page.get("headings")
            # Replace or clear old Docling tables and images
            row.tables = fresh_page.get("tables") or []
            row.images = fresh_page.get("images") or []

        deleted_chunks = retrieval.delete_document_chunks(db, document_id)
        deleted_backfills = (
            db.query(EmbeddingBackfill)
            .filter(EmbeddingBackfill.document_id == document_id)
            .delete(synchronize_session=False)
        )

        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            f"Failed to apply extraction repair for document {document_id}"
        )
        raise

    return {
        "applied": True,
        "noop": False,
        "document_id": document_id,
        "backup_path": backup_path,
        "updated_pages": len(fresh_pages),
        "deleted_chunks": deleted_chunks,
        "deleted_backfills": deleted_backfills,
        "recomputed_exclusions_count": recomputed_exclusions_count,
        "layout_status": doc.layout_status,
        "backup_metadata": backup_meta,
    }
