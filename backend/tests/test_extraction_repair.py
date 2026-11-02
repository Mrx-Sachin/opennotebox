"""Tests for legacy Docling PDF extraction repair service and CLI.

Verifies:
- Dry-run mode performs no database mutations and creates no backup.
- SHA-256 hash mismatches refuse repair.
- Active parsing, layout, embedding, and summary jobs refuse repair.
- Repeated repair on already-current documents is an idempotent no-op.
- Exclusive backup creation with restricted 0600 permissions.
- Preservation of ExtractedText IDs, document metadata, notes, library entries,
  bookmarks, conversations, and completed summaries.
- Recomputation of narration exclusions and valid source offsets.
- Transactional rollback on unexpected database failure.
- Classification of actual doc-3 stored-before vs fresh-parsed artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import (
    Bookmark,
    Conversation,
    Document,
    EmbeddingBackfill,
    ExtractedText,
    LibraryEntry,
    Note,
    Summary,
    User,
)
from app.services.extraction_repair import (
    BoxFormat,
    classify_document_boxes,
    classify_page_boxes,
    compute_file_sha256,
    repair_legacy_pdf_extraction,
)
from scripts.repair_legacy_pdf_extraction import main as cli_main


def _create_sample_pdf(file_path: Path, pages_text: list[list[str]]) -> None:
    """Generate a valid multi-page PDF using reportlab."""
    c = canvas.Canvas(str(file_path), pagesize=letter)
    for lines in pages_text:
        y = 700
        for line in lines:
            c.drawString(72, y, line)
            y -= 40
        c.showPage()
    c.save()


@pytest.fixture()
def sample_pdf_path(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "sample.pdf"
    pages_text = [
        [
            "Header: Document Title",
            "This is the first page of the research paper.",
            "It explains neural speech translation models in detail.",
        ],
        [
            "Header: Page Two",
            "This is the second page covering evaluation benchmarks.",
            "Results indicate significant improvements in BLEU score.",
        ],
    ]
    _create_sample_pdf(pdf_path, pages_text)
    return pdf_path


def test_classify_page_boxes_legacy():
    boxes = [
        {"text": "Header: Document Title", "bbox": [72, 50, 200, 70]},
        {
            "text": "This is the first page of the research paper explaining models in detail.",
            "bbox": [72, 100, 500, 180],
        },
    ]
    fmt, stats = classify_page_boxes(boxes)
    assert fmt == BoxFormat.LEGACY
    assert stats["multi_word_boxes"] == 2
    assert stats["single_word_boxes"] == 0
    assert stats["avg_chars"] > 20.0


def test_classify_page_boxes_current():
    boxes = [
        {"text": "Header:", "bbox": [72, 50, 110, 65]},
        {"text": "Document", "bbox": [115, 50, 160, 65]},
        {"text": "Title", "bbox": [165, 50, 195, 65]},
        {"text": "This", "bbox": [72, 100, 95, 115]},
        {"text": "is", "bbox": [100, 100, 110, 115]},
    ]
    fmt, stats = classify_page_boxes(boxes)
    assert fmt == BoxFormat.CURRENT
    assert stats["multi_word_boxes"] == 0
    assert stats["single_word_boxes"] == 5


def test_classify_page_boxes_empty_and_unsupported():
    fmt_empty, stats_empty = classify_page_boxes([])
    assert fmt_empty == BoxFormat.EMPTY
    assert stats_empty["total_boxes"] == 0

    fmt_none, _ = classify_page_boxes(None)
    assert fmt_none == BoxFormat.EMPTY

    fmt_bad, stats_bad = classify_page_boxes([{"text": "Only text"}])
    assert fmt_bad == BoxFormat.UNSUPPORTED
    assert "error" in stats_bad

    fmt_bad_coord, _ = classify_page_boxes(
        [{"text": "Word", "bbox": [10, "bad", 30, 40]}]
    )
    assert fmt_bad_coord == BoxFormat.UNSUPPORTED


def test_classify_document_boxes_mixed(db: Session, test_user: User):
    doc = Document(
        user_id=test_user.id,
        title="Mixed Doc",
        format="pdf",
        file_path="/tmp/fake.pdf",
        file_size=100,
        num_pages=2,
        processing_status="completed",
    )
    db.add(doc)
    db.flush()

    p1 = ExtractedText(
        document_id=doc.id,
        page_number=1,
        content="Page 1 text",
        bounding_boxes=[
            {"text": "Paragraph box one", "bbox": [10, 10, 100, 50]},
            {"text": "Paragraph box two", "bbox": [10, 60, 100, 100]},
        ],
    )
    p2 = ExtractedText(
        document_id=doc.id,
        page_number=2,
        content="Page 2 text",
        bounding_boxes=[
            {"text": "Single", "bbox": [10, 10, 50, 20]},
            {"text": "Tokens", "bbox": [55, 10, 90, 20]},
        ],
    )
    db.add_all([p1, p2])
    db.flush()

    fmt, page_stats = classify_document_boxes([p1, p2])
    assert fmt == BoxFormat.MIXED
    assert len(page_stats) == 2


def test_actual_stored_before_and_fresh_parsed_classification():
    """Verify classifier correctly recognizes all 28 stored pages as LEGACY and fresh as CURRENT."""
    stored_path = Path("_scratch/highlight-fix/stored-before.json")
    fresh_path = Path("_scratch/highlight-fix/fresh-parsed.json")
    if not stored_path.exists() or not fresh_path.exists():
        pytest.skip("Scratch test artifacts not present in environment")

    with open(stored_path, encoding="utf-8") as f:
        stored_data = json.load(f)
    with open(fresh_path, encoding="utf-8") as f:
        fresh_data = json.load(f)

    stored_pages = [
        ExtractedText(
            page_number=p["page_number"],
            bounding_boxes=p.get("bounding_boxes"),
            content=p.get("content", ""),
        )
        for p in stored_data["pages"]
    ]
    stored_fmt, stored_stats = classify_document_boxes(stored_pages)
    assert stored_fmt == BoxFormat.LEGACY
    assert len(stored_pages) == 28
    assert all(s["format"] == "legacy" for s in stored_stats)

    fresh_pages = [
        ExtractedText(
            page_number=p["page_number"],
            bounding_boxes=p.get("bounding_boxes"),
            content=p.get("content", ""),
        )
        for p in fresh_data["pages"]
    ]
    fresh_fmt, fresh_stats = classify_document_boxes(fresh_pages)
    assert fresh_fmt == BoxFormat.CURRENT
    assert len(fresh_pages) == 28
    assert all(s["format"] == "current" for s in fresh_stats)


def test_dryrun_no_mutations(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
        layout_status="ready",
        content_hash=sha,
    )
    db.add(doc)
    db.flush()

    legacy_boxes = [
        {"text": "Header: Document Title", "bbox": [72, 50, 200, 70]},
        {
            "text": "This is the first page of the research paper explaining models in detail.",
            "bbox": [72, 100, 500, 180],
        },
    ]
    p1 = ExtractedText(
        document_id=doc.id,
        page_number=1,
        content="| Table | Col |\n|---|---|\nLegacy markdown content page 1",
        bounding_boxes=legacy_boxes,
        layout_regions=[
            {
                "label": "page_header",
                "narrate": False,
                "bbox": [0.1, 0.05, 0.4, 0.1],
                "source": "docling",
            }
        ],
    )
    p2 = ExtractedText(
        document_id=doc.id,
        page_number=2,
        content="Legacy markdown content page 2",
        bounding_boxes=legacy_boxes,
        layout_regions=[],
    )
    db.add_all([p1, p2])
    db.commit()

    backup_path = tmp_path / "backup_not_created.json"

    result = repair_legacy_pdf_extraction(
        db=db,
        document_id=doc.id,
        expected_sha256=sha,
        backup_path=str(backup_path),
        apply=False,
    )

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert result["pages_to_repair"] == 2
    assert not backup_path.exists()

    db.refresh(p1)
    db.refresh(p2)
    assert p1.content.startswith("| Table | Col |")
    assert len(p1.bounding_boxes) == 2
    assert p1.bounding_boxes[0]["text"] == "Header: Document Title"


def test_wrong_hash_refuses_repair(
    db: Session, test_user: User, sample_pdf_path: Path
):
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
    )
    db.add(doc)
    db.flush()
    db.add(
        ExtractedText(
            document_id=doc.id,
            page_number=1,
            content="Page 1",
            bounding_boxes=[],
        )
    )
    db.commit()

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        repair_legacy_pdf_extraction(
            db=db,
            document_id=doc.id,
            expected_sha256="0000000000000000000000000000000000000000000000000000000000000000",
            apply=False,
        )


def test_active_work_refuses_repair(
    db: Session, test_user: User, sample_pdf_path: Path
):
    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="parsing",
        layout_status="ready",
        content_hash=sha,
    )
    db.add(doc)
    db.commit()

    with pytest.raises(RuntimeError, match="active work in progress"):
        repair_legacy_pdf_extraction(
            db=db,
            document_id=doc.id,
            expected_sha256=sha,
            apply=False,
        )

    # Test active layout status queued
    doc.processing_status = "completed"
    doc.layout_status = "queued"
    db.commit()

    with pytest.raises(RuntimeError, match="layout_status is 'queued'"):
        repair_legacy_pdf_extraction(
            db=db,
            document_id=doc.id,
            expected_sha256=sha,
            apply=False,
        )

    # Test active embedding backfill (pending)
    doc.layout_status = "ready"
    backfill = EmbeddingBackfill(
        document_id=doc.id,
        status="pending",
    )
    db.add(backfill)
    db.commit()

    with pytest.raises(RuntimeError, match="embedding backfill job"):
        repair_legacy_pdf_extraction(
            db=db,
            document_id=doc.id,
            expected_sha256=sha,
            apply=False,
        )


def test_apply_refreshes_cached_document_before_active_work_check(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        processing_status="completed",
    )
    db.add(doc)
    db.commit()
    doc_id = doc.id
    db.execute(
        Document.__table__.update()
        .where(Document.id == doc_id)
        .values(processing_status="parsing")
    )
    assert doc.processing_status == "completed"

    with pytest.raises(RuntimeError, match="processing_status is 'parsing'"):
        repair_legacy_pdf_extraction(
            db=db,
            document_id=doc_id,
            expected_sha256=compute_file_sha256(str(sample_pdf_path)),
            backup_path=str(tmp_path / "unused-backup.json"),
            apply=True,
        )
    assert not (tmp_path / "unused-backup.json").exists()


def test_apply_refreshes_cached_pages_before_classifying(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        processing_status="completed",
    )
    db.add(doc)
    db.flush()
    page = ExtractedText(
        document_id=doc.id,
        page_number=1,
        content="Legacy paragraph text",
        bounding_boxes=[{"text": "Legacy paragraph text", "bbox": [0, 0, 100, 50]}],
    )
    db.add(page)
    db.commit()
    page_id = page.id
    assert page.bounding_boxes[0]["text"] == "Legacy paragraph text"
    db.execute(
        ExtractedText.__table__.update()
        .where(ExtractedText.id == page_id)
        .values(bounding_boxes=[{"text": "Word", "bbox": [0, 0, 10, 10]}])
    )

    result = repair_legacy_pdf_extraction(
        db=db,
        document_id=doc.id,
        expected_sha256=compute_file_sha256(str(sample_pdf_path)),
        backup_path=str(tmp_path / "unused-backup.json"),
        apply=True,
    )
    assert result["noop"] is True
    assert not (tmp_path / "unused-backup.json").exists()


def test_apply_legacy_repair_with_backup_and_preservation(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
        layout_status="ready",
        content_hash=sha,
    )
    db.add(doc)
    db.flush()

    legacy_boxes = [
        {"text": "Header: Document Title", "bbox": [72, 50, 200, 70]},
        {
            "text": "This is the first page of the research paper explaining models in detail.",
            "bbox": [72, 100, 500, 180],
        },
    ]

    p1 = ExtractedText(
        document_id=doc.id,
        page_number=1,
        content="| Old Table |\n|---|\nLegacy page 1",
        bounding_boxes=legacy_boxes,
        headings=["Old Heading"],
        tables=[{"raw": "old_docling_table"}],
        images=[{"raw": "old_docling_image"}],
        layout_regions=[
            {
                "label": "page_header",
                "narrate": False,
                "bbox": [0.05, 0.02, 0.5, 0.1],
                "source": "docling",
            }
        ],
        narration_exclusions=[],
    )
    p2 = ExtractedText(
        document_id=doc.id,
        page_number=2,
        content="Legacy page 2",
        bounding_boxes=legacy_boxes,
        headings=None,
        tables=[{"raw": "stale"}],
        images=[],
        layout_regions=[],
        narration_exclusions=[],
    )
    db.add_all([p1, p2])
    db.flush()

    p1_original_id = p1.id
    p2_original_id = p2.id

    u2 = User(username="reader2", password_hash="hash2")
    db.add(u2)
    db.flush()
    lib_entry2 = LibraryEntry(user_id=u2.id, document_id=doc.id)
    note1 = Note(
        user_id=test_user.id,
        document_id=doc.id,
        page_number=1,
        note="User note on page 1",
        quote="Selected passage",
        color="amber",
    )
    note2 = Note(
        user_id=test_user.id,
        document_id=doc.id,
        page_number=2,
        note="User note on page 2",
        quote=None,
        color="indigo",
    )
    note3 = Note(
        user_id=test_user.id,
        document_id=doc.id,
        page_number=1,
        note="Second note on page 1",
        quote="Another passage",
        color="green",
    )
    bookmark = Bookmark(
        user_id=test_user.id, document_id=doc.id, page_number=1
    )
    conv = Conversation(user_id=test_user.id, document_ids=[doc.id], title="Chat")
    summary1 = Summary(
        document_id=doc.id,
        mode="section",
        section_title="Section 1",
        content="",
        status="completed",
    )
    summary2 = Summary(
        document_id=doc.id,
        mode="section",
        section_title="Section 2",
        content="Detailed section summary text.",
        status="completed",
    )
    backfill = EmbeddingBackfill(
        document_id=doc.id,
        status="completed",
    )
    db.add_all([lib_entry2, note1, note2, note3, bookmark, conv, summary1, summary2, backfill])
    db.commit()

    backup_file = tmp_path / "repair_backup.json"

    result = repair_legacy_pdf_extraction(
        db=db,
        document_id=doc.id,
        expected_sha256=sha,
        backup_path=str(backup_file),
        apply=True,
    )

    assert result["applied"] is True
    assert result["noop"] is False
    assert result["updated_pages"] == 2
    assert result["deleted_backfills"] == 1

    assert backup_file.exists()
    mode = os.stat(backup_file).st_mode
    assert (mode & 0o777) == 0o600

    db.expire_all()
    p1_repaired = db.query(ExtractedText).filter_by(id=p1_original_id).one()
    p2_repaired = db.query(ExtractedText).filter_by(id=p2_original_id).one()

    # ExtractedText IDs strictly preserved
    assert p1_repaired.page_number == 1
    assert p2_repaired.page_number == 2

    # Boxes converted to modern word tokens
    p1_fmt, p1_stats = classify_page_boxes(p1_repaired.bounding_boxes)
    assert p1_fmt == BoxFormat.CURRENT
    assert p1_stats["multi_word_boxes"] == 0
    assert p1_stats["single_word_boxes"] > 5

    # Stale Docling tables cleared
    assert p1_repaired.tables == []
    assert p2_repaired.tables == []

    # Layout regions preserved
    assert len(p1_repaired.layout_regions) == 1
    assert p1_repaired.layout_regions[0]["label"] == "page_header"

    # Narration exclusions offsets valid
    if p1_repaired.narration_exclusions:
        for excl in p1_repaired.narration_exclusions:
            start = excl["start"]
            end = excl["end"]
            assert 0 <= start < end <= len(p1_repaired.content)

    # Related items preserved
    assert db.query(LibraryEntry).filter_by(document_id=doc.id).count() == 2
    assert db.query(Note).filter_by(document_id=doc.id).count() == 3
    assert db.query(Bookmark).filter_by(document_id=doc.id).count() == 1
    assert db.query(Conversation).filter_by(id=conv.id).count() == 1
    assert db.query(Summary).filter_by(document_id=doc.id).count() == 2

    # Verify empty section stub summary preserved
    s1 = db.query(Summary).filter_by(document_id=doc.id, section_title="Section 1").one()
    assert s1.content == ""

    # Stale backfill invalidated
    assert db.query(EmbeddingBackfill).filter_by(document_id=doc.id).first() is None


def test_backup_exclusive_creation_refuses_existing(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
        content_hash=sha,
    )
    db.add(doc)
    db.flush()
    db.add(
        ExtractedText(
            document_id=doc.id,
            page_number=1,
            content="Page 1",
            bounding_boxes=[{"text": "Paragraph text block", "bbox": [10, 10, 100, 50]}],
        )
    )
    db.add(
        ExtractedText(
            document_id=doc.id,
            page_number=2,
            content="Page 2",
            bounding_boxes=[{"text": "Paragraph text block", "bbox": [10, 10, 100, 50]}],
        )
    )
    db.commit()

    existing_backup = tmp_path / "already_exists.json"
    existing_backup.write_text("{\"existing\": true}")

    with pytest.raises(FileExistsError, match="Backup file already exists"):
        repair_legacy_pdf_extraction(
            db=db,
            document_id=doc.id,
            expected_sha256=sha,
            backup_path=str(existing_backup),
            apply=True,
        )


def test_repeated_repair_is_idempotent_noop(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
        content_hash=sha,
    )
    db.add(doc)
    db.flush()

    word_boxes = [
        {"text": "Word", "bbox": [10, 10, 30, 20]},
        {"text": "Token", "bbox": [35, 10, 60, 20]},
    ]
    db.add(
        ExtractedText(
            document_id=doc.id,
            page_number=1,
            content="Word Token",
            bounding_boxes=word_boxes,
        )
    )
    db.add(
        ExtractedText(
            document_id=doc.id,
            page_number=2,
            content="Word Token",
            bounding_boxes=word_boxes,
        )
    )
    db.commit()

    backup_path = tmp_path / "should_not_exist.json"

    result = repair_legacy_pdf_extraction(
        db=db,
        document_id=doc.id,
        expected_sha256=sha,
        backup_path=str(backup_path),
        apply=True,
    )

    assert result["noop"] is True
    assert result["applied"] is False
    assert not backup_path.exists()


def test_unsupported_and_mixed_formats_refused(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
        content_hash=sha,
    )
    db.add(doc)
    db.flush()

    db.add(
        ExtractedText(
            document_id=doc.id,
            page_number=1,
            content="Page 1",
            bounding_boxes=[{"text": "Bad", "bbox": ["x", "y", "z", "w"]}],
        )
    )
    db.add(
        ExtractedText(
            document_id=doc.id,
            page_number=2,
            content="Page 2",
            bounding_boxes=[],
        )
    )
    db.commit()

    backup_path = tmp_path / "test.json"
    with pytest.raises(ValueError, match="Refusing repair"):
        repair_legacy_pdf_extraction(
            db=db,
            document_id=doc.id,
            expected_sha256=sha,
            backup_path=str(backup_path),
            apply=True,
        )


def test_transactional_rollback_on_failure(sample_pdf_path: Path, tmp_path: Path):
    """Test rollback using a dedicated isolated engine and session."""
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated_rollback.db'}")
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()

    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=None,
        title="Rollback Test.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
        content_hash=sha,
    )
    session.add(doc)
    session.flush()

    legacy_boxes = [
        {"text": "Paragraph block one", "bbox": [10, 10, 100, 50]},
        {"text": "Paragraph block two", "bbox": [10, 60, 100, 100]},
    ]
    p1 = ExtractedText(
        document_id=doc.id,
        page_number=1,
        content="Original legacy content p1",
        bounding_boxes=legacy_boxes,
    )
    p2 = ExtractedText(
        document_id=doc.id,
        page_number=2,
        content="Original legacy content p2",
        bounding_boxes=legacy_boxes,
    )
    session.add_all([p1, p2])
    session.commit()

    p1_id = p1.id
    backup_path = tmp_path / "rollback_backup.json"

    with patch(
        "app.services.extraction_repair.retrieval.delete_document_chunks",
        side_effect=RuntimeError("Simulated database failure"),
    ):
        with pytest.raises(RuntimeError, match="Simulated database failure"):
            repair_legacy_pdf_extraction(
                db=session,
                document_id=doc.id,
                expected_sha256=sha,
                backup_path=str(backup_path),
                apply=True,
            )

    # Check that database state rolled back to original legacy content
    session.close()
    verify_session = SessionMaker()
    p1_check = verify_session.query(ExtractedText).filter_by(id=p1_id).one()
    assert p1_check.content == "Original legacy content p1"
    assert p1_check.bounding_boxes == legacy_boxes
    verify_session.close()


def test_cli_execution_dry_run_and_apply(
    db: Session, test_user: User, sample_pdf_path: Path, tmp_path: Path
):
    sha = compute_file_sha256(str(sample_pdf_path))
    doc = Document(
        user_id=test_user.id,
        title="Sample.pdf",
        format="pdf",
        file_path=str(sample_pdf_path),
        file_size=sample_pdf_path.stat().st_size,
        num_pages=2,
        processing_status="completed",
        content_hash=sha,
    )
    db.add(doc)
    db.flush()

    legacy_boxes = [
        {"text": "Paragraph block one", "bbox": [10, 10, 100, 50]},
        {"text": "Paragraph block two", "bbox": [10, 60, 100, 100]},
    ]
    db.add_all([
        ExtractedText(
            document_id=doc.id,
            page_number=1,
            content="Old page 1",
            bounding_boxes=legacy_boxes,
        ),
        ExtractedText(
            document_id=doc.id,
            page_number=2,
            content="Old page 2",
            bounding_boxes=legacy_boxes,
        ),
    ])
    db.commit()

    backup_path = tmp_path / "cli_backup.json"

    with patch("scripts.repair_legacy_pdf_extraction.SessionLocal", return_value=db):
        exit_code = cli_main([
            "--document-id", str(doc.id),
            "--expected-sha256", sha,
        ])
    assert exit_code == 0
    assert not backup_path.exists()

    with patch("scripts.repair_legacy_pdf_extraction.SessionLocal", return_value=db):
        exit_code = cli_main([
            "--apply",
            "--document-id", str(doc.id),
            "--expected-sha256", sha,
            "--backup", str(backup_path),
        ])
    assert exit_code == 0
    assert backup_path.exists()

    backup_path2 = tmp_path / "cli_backup2.json"
    with patch("scripts.repair_legacy_pdf_extraction.SessionLocal", return_value=db):
        exit_code = cli_main([
            "--apply",
            "--document-id", str(doc.id),
            "--expected-sha256", sha,
            "--backup", str(backup_path2),
        ])
    assert exit_code == 0
    assert not backup_path2.exists()
