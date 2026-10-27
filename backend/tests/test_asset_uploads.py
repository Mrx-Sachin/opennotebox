"""Exact upload reuse, private membership state, and shared asset retention."""
from pathlib import Path
from unittest.mock import patch

from app.models import Document, EmbeddingBackfill, ExtractedText, LibraryEntry, Notebook, Summary
from app.services.auth_service import create_access_token

PDF = b"%PDF-1.4\nshared testing bytes\n%%EOF"


def user_headers(user):
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


async def test_upload_reuses_asset_without_parse_or_private_filename_leak(
    client, auth_header, second_user, db
):
    with patch("app.routers.documents._dispatch_parse") as dispatch:
        first = await client.post("/api/documents/upload", headers=auth_header,
                                  files={"file": ("private-first.pdf", PDF, "application/pdf")})
        assert first.status_code == 200
        second = await client.post("/api/documents/upload", headers=user_headers(second_user),
                                   files={"file": ("private-second.pdf", PDF, "application/pdf")})
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["title"] == "private-first.pdf"
        assert second.json()["title"] == "private-second.pdf"
        assert second.json()["user_id"] == second_user.id
        assert second.json()["original_url"] is None
        assert second.json()["read_only"] is True
        assert dispatch.call_count == 1
    assert db.query(Document).count() == 1
    assert db.query(LibraryEntry).count() == 2
    assert db.query(LibraryEntry).filter(LibraryEntry.can_publish.is_(True)).count() == 2
    asset = db.query(Document).one()
    assert len(asset.content_hash) == 64
    assert Path(asset.file_path).read_bytes() == PDF


async def test_shared_members_keep_independent_notebooks_and_reading_state(
    client, auth_header, test_user, second_user, db
):
    with patch("app.routers.documents._dispatch_parse"):
        first = await client.post("/api/documents/upload", headers=auth_header,
                                  files={"file": ("A.pdf", PDF, "application/pdf")})
        second = await client.post("/api/documents/upload", headers=user_headers(second_user),
                                   files={"file": ("B.pdf", PDF, "application/pdf")})
    doc_id = first.json()["id"]
    assert second.json()["id"] == doc_id
    doc = db.get(Document, doc_id)
    doc.num_pages = 10
    doc.processing_status = "completed"
    db.add(ExtractedText(document_id=doc_id, page_number=1, content="Reusable extracted text"))
    a = Notebook(user_id=test_user.id, name="A")
    b = Notebook(user_id=second_user.id, name="B")
    db.add_all([a, b])
    db.commit()
    for headers, notebook, page in [(auth_header, a, 2), (user_headers(second_user), b, 7)]:
        response = await client.post("/api/notebooks/move-documents", headers=headers,
                                     json={"document_ids": [doc_id], "notebook_id": notebook.id})
        assert response.status_code == 200
        response = await client.put(f"/api/documents/{doc_id}/progress", headers=headers,
                                    json={"page": page, "position": 0.25})
        assert response.status_code == 200
        assert response.json()["last_read_page"] == page
        assert response.json()["notebook_id"] == notebook.id
    assert (await client.get(f"/api/documents/{doc_id}", headers=auth_header)).json()["last_read_page"] == 2
    assert (await client.get(f"/api/documents/{doc_id}", headers=user_headers(second_user))).json()["last_read_page"] == 7
    path = Path(doc.file_path)
    removed = await client.delete(f"/api/documents/{doc_id}", headers=auth_header)
    assert removed.status_code == 200
    assert path.exists()
    assert db.query(ExtractedText).filter(ExtractedText.document_id == doc_id).count() == 1
    assert (await client.get(f"/api/documents/{doc_id}/file", headers=user_headers(second_user))).status_code == 200
    assert (await client.get(f"/api/documents/{doc_id}", headers=auth_header)).status_code == 404
    assert (await client.delete(f"/api/documents/{doc_id}", headers=user_headers(second_user))).status_code == 200
    assert not path.exists()
    assert db.get(Document, doc_id) is None


async def test_shared_version_cannot_be_reparsed(client, auth_header, test_document, db):
    test_document.is_shared = True
    db.commit()
    with patch("app.routers.documents.reparse_pdf_document.delay") as dispatch:
        response = await client.post(f"/api/documents/{test_document.id}/re-parse", headers=auth_header)
        assert response.status_code == 409
        dispatch.assert_not_called()


async def test_last_reference_waits_for_running_embedding_before_file_collection(
    client, auth_header, test_document, db
):
    path = Path(test_document.file_path)
    job = EmbeddingBackfill(document_id=test_document.id, status="running")
    db.add(job)
    db.commit()
    with patch("app.tasks.asset_tasks.collect_unreferenced_asset.apply_async") as enqueue:
        response = await client.delete(f"/api/documents/{test_document.id}", headers=auth_header)
        assert response.status_code == 200
        enqueue.assert_called_once()
    assert path.exists()
    assert db.query(LibraryEntry).filter(LibraryEntry.document_id == test_document.id).count() == 0
    assert db.get(Document, test_document.id) is not None
    job.status = "completed"
    db.commit()
    from app.services.asset_lifecycle import delete_unreferenced_asset, remove_asset_files
    doc_id = test_document.id
    collected = delete_unreferenced_asset(db, test_document)
    assert collected == str(path)
    db.commit()
    remove_asset_files(doc_id, collected)
    assert not path.exists()


async def test_page_summary_job_is_private_durable_and_blocks_asset_collection(
    client, auth_header, test_document, db
):
    from app.services.asset_lifecycle import asset_has_active_jobs

    with patch("app.tasks.summary_tasks.generate_page_summaries.delay") as enqueue:
        response = await client.post(
            f"/api/documents/{test_document.id}/summaries/pages/all",
            headers=auth_header,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        enqueue.assert_called_once_with(test_document.id, test_document.user_id)

    marker = db.query(Summary).filter(
        Summary.document_id == test_document.id,
        Summary.user_id == test_document.user_id,
        Summary.mode == "page_job",
    ).one()
    assert marker.status == "generating"
    assert test_document.processing_status == "extracted"
    assert asset_has_active_jobs(db, test_document)
    assert (await client.get(
        f"/api/documents/{test_document.id}/summaries", headers=auth_header
    )).json() == []

    duplicate = await client.post(
        f"/api/documents/{test_document.id}/summaries/pages/all",
        headers=auth_header,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_generating"
    enqueue.assert_called_once()


async def test_personal_summary_failure_does_not_hide_published_asset(
    client, auth_header, test_document, db
):
    from app.tasks.summary_tasks import _finish_page_summary_job

    published = await client.patch(
        f"/api/shared-library/{test_document.id}",
        headers=auth_header,
        json={"is_shared": True, "title": "Shared reading"},
    )
    assert published.status_code == 200
    # Reproduce completion of a legacy whole-book job that changed asset status.
    test_document.processing_status = "summarizing_pages"
    marker = Summary(
        document_id=test_document.id, user_id=test_document.user_id,
        mode="page_job", page_number=0, content="", status="generating",
    )
    db.add_all([marker, Summary(
        document_id=test_document.id, user_id=test_document.user_id,
        mode="page", page_number=1, content="", status="generating",
    )])
    db.commit()
    _finish_page_summary_job(db, test_document.id, test_document.user_id, final_status="failed")

    catalog = await client.get("/api/shared-library", headers=auth_header)
    assert catalog.status_code == 200
    assert [book["id"] for book in catalog.json()["documents"]] == [test_document.id]
    assert marker.status == "failed"
    assert test_document.processing_status == "completed"


async def test_personal_summary_enqueue_failure_preserves_asset_readiness(
    client, auth_header, test_document, db
):
    with patch("app.tasks.summary_tasks.generate_page_summaries.delay", side_effect=RuntimeError):
        response = await client.post(
            f"/api/documents/{test_document.id}/summaries/pages/all", headers=auth_header,
        )
    assert response.status_code == 503
    assert test_document.processing_status == "extracted"
    assert db.query(Summary).filter(Summary.mode == "page_job").one().status == "failed"


async def test_whole_book_summary_rejects_unprocessed_asset(
    client, auth_header, test_document, db
):
    test_document.processing_status = "pending"
    db.commit()
    with patch("app.tasks.summary_tasks.generate_page_summaries.delay") as enqueue:
        response = await client.post(
            f"/api/documents/{test_document.id}/summaries/pages/all", headers=auth_header,
        )
    assert response.status_code == 409
    assert not db.query(Summary).filter(Summary.mode == "page_job").first()
    enqueue.assert_not_called()
