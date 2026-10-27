"""Regression tests for replay-safe document ingestion."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text as sql
from sqlalchemy.orm import sessionmaker

from app.models import EmbeddingBackfill, ExtractedText
from app.services import retrieval
from app.tasks import embedding_tasks, pdf_tasks


def test_txt_parse_replay_replaces_previous_extraction(db, test_document, tmp_path):
    """A retried initial parse replaces output instead of appending pages."""
    source = Path(tmp_path) / "notes.txt"
    source.write_text("Latest parser output with a distinctive phrase.", encoding="utf-8")
    retrieval.index_document(db, test_document.id)
    original_chunk_count = db.execute(
        sql("SELECT COUNT(*) FROM chunk WHERE document_id = :d"),
        {"d": test_document.id},
    ).scalar_one()
    assert original_chunk_count > 0
    task_session_factory = sessionmaker(bind=db.connection())

    with patch.object(pdf_tasks, "SessionLocal", task_session_factory), patch.object(
        pdf_tasks, "_chain_summaries"
    ):
        pdf_tasks.parse_txt.run(str(source), "notes", test_document.id)
        pdf_tasks.parse_txt.run(str(source), "notes", test_document.id)

    pages = (
        db.query(ExtractedText)
        .filter(ExtractedText.document_id == test_document.id)
        .order_by(ExtractedText.page_number)
        .all()
    )
    assert [(page.page_number, page.content) for page in pages] == [
        (1, "Latest parser output with a distinctive phrase.")
    ]

    assert db.execute(
        sql("SELECT COUNT(*) FROM chunk WHERE document_id = :d"),
        {"d": test_document.id},
    ).scalar_one() == 0
    retrieval.index_document(db, test_document.id)
    hits = retrieval.search(db, [test_document.id], ["distinctive phrase"])
    assert hits
    assert hits[0]["page"] == 1
    assert "Latest parser output" in hits[0]["text"]


def test_embedding_backfill_schedule_is_deduplicated(db, test_document, monkeypatch):
    retrieval.index_document(db, test_document.id)
    task_session_factory = sessionmaker(bind=db.connection())
    monkeypatch.setattr("app.database.SessionLocal", task_session_factory)

    with patch("app.tasks.embedding_tasks.embed_document_chunks.apply_async") as enqueue:
        retrieval.schedule_full_embeddings([test_document.id, test_document.id])
        retrieval.schedule_full_embeddings([test_document.id])

    enqueue.assert_called_once()
    job = (
        db.query(EmbeddingBackfill)
        .filter(EmbeddingBackfill.document_id == test_document.id)
        .one()
    )
    assert job.status == "queued"


def test_failed_embedding_dispatch_remains_retryable(db, test_document, monkeypatch):
    retrieval.index_document(db, test_document.id)
    task_session_factory = sessionmaker(bind=db.connection())
    monkeypatch.setattr("app.database.SessionLocal", task_session_factory)

    with patch(
        "app.tasks.embedding_tasks.embed_document_chunks.apply_async",
        side_effect=[RuntimeError("broker unavailable"), None],
    ) as enqueue:
        retrieval.schedule_full_embeddings([test_document.id])
        job = (
            db.query(EmbeddingBackfill)
            .filter(EmbeddingBackfill.document_id == test_document.id)
            .one()
        )
        assert job.status == "pending"

        retrieval.schedule_full_embeddings([test_document.id])

    assert enqueue.call_count == 2
    db.refresh(job)
    assert job.status == "queued"


def test_pdf_initial_parse_replay_replaces_pages(db, test_document):
    task_session_factory = sessionmaker(bind=db.connection())
    parser_result = {
        "num_pages": 2,
        "pages": [
            {"page_number": 1, "content": "Replayed PDF page one."},
            {"page_number": 2, "content": "Replayed PDF page two."},
        ],
    }

    with patch.object(pdf_tasks, "SessionLocal", task_session_factory), patch.object(
        pdf_tasks.pdf_parser, "parse_pdf", return_value=parser_result
    ), patch.object(pdf_tasks, "_chain_summaries"):
        pdf_tasks.parse_pdf_document.run(test_document.id)
        pdf_tasks.parse_pdf_document.run(test_document.id)

    pages = (
        db.query(ExtractedText)
        .filter(ExtractedText.document_id == test_document.id)
        .order_by(ExtractedText.page_number)
        .all()
    )
    assert [(page.page_number, page.content) for page in pages] == [
        (1, "Replayed PDF page one."),
        (2, "Replayed PDF page two."),
    ]


def test_embedding_backfill_task_completes_and_persists_vectors(
    db, test_document, monkeypatch
):
    retrieval.index_document(db, test_document.id)
    job = EmbeddingBackfill(document_id=test_document.id, status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    task_session_factory = sessionmaker(bind=db.connection())
    monkeypatch.setattr(embedding_tasks, "SessionLocal", task_session_factory)

    with patch.object(
        retrieval,
        "embed_texts",
        side_effect=lambda texts, is_query=False: [[1.0, 0.0] for _ in texts],
    ):
        result = embedding_tasks.embed_document_chunks.run(test_document.id, job.id)

    db.refresh(job)
    assert result["status"] == "completed"
    assert job.status == "completed"
    embedded_count = db.execute(
        sql(
            "SELECT COUNT(*) FROM chunk "
            "WHERE document_id = :d AND emb IS NOT NULL"
        ),
        {"d": test_document.id},
    ).scalar_one()
    assert embedded_count > 0
