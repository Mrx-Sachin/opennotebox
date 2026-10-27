"""Targeted lifecycle and retirement invariant tests for document assets."""
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.models import (
    Bookmark,
    Document,
    EmbeddingBackfill,
    Flashcard,
    KgExtractionState,
    LibraryEntry,
    Note,
    Podcast,
    Summary,
    TTSCache,
)
from app.services.asset_lifecycle import (
    retire_document_membership,
)
from app.services.document_preview import preview_path
from app.tasks.asset_tasks import collect_unreferenced_asset


def test_retire_preserves_other_users_children_and_shared_asset(
    db, test_user, second_user, test_document, tmp_path
):
    """Retiring one membership cleans only that user children and preserves others."""
    audio1 = tmp_path / "audio1.mp3"
    audio1.write_bytes(b"audio1")
    audio2 = tmp_path / "audio2.mp3"
    audio2.write_bytes(b"audio2")

    second_entry = LibraryEntry(
        user_id=second_user.id,
        document_id=test_document.id,
        can_publish=True,
    )
    db.add(second_entry)

    db.add_all([
        Note(user_id=test_user.id, document_id=test_document.id, page_number=1, note="u1 note"),
        Note(user_id=second_user.id, document_id=test_document.id, page_number=1, note="u2 note"),
        Bookmark(user_id=test_user.id, document_id=test_document.id, page_number=1, label="u1 bm"),
        Bookmark(user_id=second_user.id, document_id=test_document.id, page_number=1, label="u2 bm"),
        Flashcard(user_id=test_user.id, document_id=test_document.id, question="q1", answer="a1"),
        Flashcard(user_id=second_user.id, document_id=test_document.id, question="q2", answer="a2"),
        Summary(user_id=test_user.id, document_id=test_document.id, mode="page", page_number=1, content="s1"),
        Summary(user_id=second_user.id, document_id=test_document.id, mode="page", page_number=1, content="s2"),
        TTSCache(user_id=test_user.id, document_id=test_document.id, text_hash="hash1", audio_path=str(audio1)),
        TTSCache(user_id=second_user.id, document_id=test_document.id, text_hash="hash2", audio_path=str(audio2)),
        Podcast(user_id=test_user.id, document_id=test_document.id, scope="doc", audio_path=str(audio1)),
        Podcast(user_id=second_user.id, document_id=test_document.id, scope="doc", audio_path=str(audio2)),
    ])
    db.commit()

    retire_document_membership(db, user_id=test_user.id, doc_id=test_document.id)

    assert db.query(LibraryEntry).filter(
        LibraryEntry.document_id == test_document.id,
        LibraryEntry.user_id == test_user.id,
    ).first() is None
    assert db.query(Note).filter(Note.document_id == test_document.id, Note.user_id == test_user.id).count() == 0
    assert db.query(Bookmark).filter(Bookmark.document_id == test_document.id, Bookmark.user_id == test_user.id).count() == 0
    assert db.query(Flashcard).filter(Flashcard.document_id == test_document.id, Flashcard.user_id == test_user.id).count() == 0
    assert db.query(Summary).filter(Summary.document_id == test_document.id, Summary.user_id == test_user.id).count() == 0
    assert db.query(TTSCache).filter(TTSCache.document_id == test_document.id, TTSCache.user_id == test_user.id).count() == 0
    assert db.query(Podcast).filter(Podcast.document_id == test_document.id, Podcast.user_id == test_user.id).count() == 0

    assert db.query(LibraryEntry).filter(
        LibraryEntry.document_id == test_document.id,
        LibraryEntry.user_id == second_user.id,
    ).first() is not None
    assert db.query(Note).filter(Note.document_id == test_document.id, Note.user_id == second_user.id).count() == 1
    assert db.query(Bookmark).filter(Bookmark.document_id == test_document.id, Bookmark.user_id == second_user.id).count() == 1
    assert db.query(Flashcard).filter(Flashcard.document_id == test_document.id, Flashcard.user_id == second_user.id).count() == 1
    assert db.query(Summary).filter(Summary.document_id == test_document.id, Summary.user_id == second_user.id).count() == 1
    assert db.query(TTSCache).filter(TTSCache.document_id == test_document.id, TTSCache.user_id == second_user.id).count() == 1
    assert db.query(Podcast).filter(Podcast.document_id == test_document.id, Podcast.user_id == second_user.id).count() == 1

    assert db.get(Document, test_document.id) is not None
    assert Path(test_document.file_path).exists()
    assert audio2.exists()
    assert not audio1.exists()


def test_retire_shared_assets(db, test_user, second_user, test_document):
    """Retirement preserves shared publication state unless sharer leaves multi-membership."""
    test_document.is_shared = True
    test_document.shared_by_user_id = test_user.id
    second_entry = LibraryEntry(
        user_id=second_user.id,
        document_id=test_document.id,
        can_publish=True,
    )
    db.add(second_entry)
    db.commit()

    retire_document_membership(db, user_id=second_user.id, doc_id=test_document.id)

    db.refresh(test_document)
    assert test_document.is_shared is True
    assert test_document.shared_by_user_id == test_user.id
    assert db.get(Document, test_document.id) is not None

    db.add(LibraryEntry(user_id=second_user.id, document_id=test_document.id, can_publish=True))
    db.commit()

    retire_document_membership(db, user_id=test_user.id, doc_id=test_document.id)

    db.refresh(test_document)
    assert test_document.is_shared is False
    assert test_document.shared_by_user_id is None
    assert db.get(Document, test_document.id) is not None


def test_retire_last_membership_deletes_asset_and_artifacts(
    db, test_user, test_document
):
    """Retiring the last membership deletes the database record and files."""
    test_document.processing_status = "completed"
    doc_path = Path(test_document.file_path)
    cover = preview_path(0, test_document.id)
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"preview-cover")
    db.commit()

    assert doc_path.exists()
    assert cover.exists()

    retire_document_membership(db, user_id=test_user.id, doc_id=test_document.id)

    assert db.get(Document, test_document.id) is None
    assert not doc_path.exists()
    assert not cover.exists()


def test_retire_active_job_defers_collection(
    db, test_user, test_document
):
    """Active jobs prevent immediate file deletion and schedule deferred cleanup."""
    doc_path = Path(test_document.file_path)
    job = EmbeddingBackfill(document_id=test_document.id, status="running")
    db.add(job)
    db.commit()

    with patch("app.tasks.asset_tasks.collect_unreferenced_asset.apply_async") as enqueue:
        retire_document_membership(db, user_id=test_user.id, doc_id=test_document.id)
        enqueue.assert_called_once_with(args=[test_document.id], countdown=300)

    assert db.get(Document, test_document.id) is not None
    assert doc_path.exists()
    assert db.query(LibraryEntry).filter(LibraryEntry.document_id == test_document.id).count() == 0

    job.status = "completed"
    db.commit()

    test_session_maker = sessionmaker(bind=db.get_bind())
    with patch("app.tasks.asset_tasks.SessionLocal", side_effect=test_session_maker):
        res = collect_unreferenced_asset(test_document.id)
        assert res == {"status": "collected"}

    doc_id = test_document.id
    db.expire_all()
    assert db.get(Document, doc_id) is None
    assert not doc_path.exists()


def test_retire_snapshots_active_summary_before_removing_its_marker(
    db, test_user, test_document,
):
    marker = Summary(
        document_id=test_document.id,
        user_id=test_user.id,
        mode="page_job",
        status="generating",
        content="",
    )
    db.add(marker)
    db.commit()
    document_id = test_document.id
    document_path = Path(test_document.file_path)

    with patch("app.tasks.asset_tasks.collect_unreferenced_asset.apply_async") as enqueue:
        retire_document_membership(db, user_id=test_user.id, doc_id=document_id)
        enqueue.assert_called_once_with(args=[document_id], countdown=300)

    assert db.query(Summary).filter_by(document_id=document_id).count() == 0
    assert db.query(LibraryEntry).filter_by(document_id=document_id).count() == 0
    assert db.get(Document, document_id) is not None
    assert document_path.exists()


def test_retire_queued_embedding_job_defers_collection(db, test_user, test_document):
    document_path = Path(test_document.file_path)
    backfill_job = EmbeddingBackfill(document_id=test_document.id, status="queued")
    db.add(backfill_job)
    db.commit()

    with patch("app.tasks.asset_tasks.collect_unreferenced_asset.apply_async") as enqueue_mock:
        retire_document_membership(db, user_id=test_user.id, doc_id=test_document.id)
        enqueue_mock.assert_called_once_with(args=[test_document.id], countdown=300)

    assert db.get(Document, test_document.id) is not None
    assert document_path.exists()
    assert db.query(LibraryEntry).filter(LibraryEntry.document_id == test_document.id).count() == 0


def test_retire_pending_kg_extraction_defers_collection(db, test_user, test_document):
    document_path = Path(test_document.file_path)
    extraction_job = KgExtractionState(document_id=test_document.id, status="pending")
    db.add(extraction_job)
    db.commit()

    with patch("app.tasks.asset_tasks.collect_unreferenced_asset.apply_async") as enqueue_mock:
        retire_document_membership(db, user_id=test_user.id, doc_id=test_document.id)
        enqueue_mock.assert_called_once_with(args=[test_document.id], countdown=300)

    assert db.get(Document, test_document.id) is not None
    assert document_path.exists()
    assert db.query(LibraryEntry).filter(LibraryEntry.document_id == test_document.id).count() == 0


def test_retire_extracting_kg_defers_collection(db, test_user, test_document):
    document_path = Path(test_document.file_path)
    extraction_job = KgExtractionState(document_id=test_document.id, status="extracting")
    db.add(extraction_job)
    db.commit()

    with patch("app.tasks.asset_tasks.collect_unreferenced_asset.apply_async") as enqueue_mock:
        retire_document_membership(db, user_id=test_user.id, doc_id=test_document.id)
        enqueue_mock.assert_called_once_with(args=[test_document.id], countdown=300)

    assert db.get(Document, test_document.id) is not None
    assert document_path.exists()
    assert db.query(LibraryEntry).filter(LibraryEntry.document_id == test_document.id).count() == 0


def test_collector_retry_budget_exceeds_configured_job_limits():
    from app.tasks.embedding_tasks import _EMBED_TIME_LIMIT
    from app.tasks.kg_tasks import KG_TIME_LIMIT
    from app.tasks.summary_tasks import PAGE_SUMMARY_TIME_LIMIT

    retry_interval_seconds = 180
    total_retry_budget = collect_unreferenced_asset.max_retries * retry_interval_seconds
    assert total_retry_budget > PAGE_SUMMARY_TIME_LIMIT
    assert total_retry_budget > KG_TIME_LIMIT
    assert total_retry_budget > _EMBED_TIME_LIMIT
