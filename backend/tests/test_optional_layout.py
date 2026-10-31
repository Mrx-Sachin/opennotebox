"""Exercise optional layout through HTTP and the real PDF enrichment task."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import Document, ExtractedText, LibraryEntry
from app.tasks import pdf_tasks


@pytest.fixture
def layout_pdf(db, test_document, monkeypatch):
    """Persist actual fixture extraction, with task writes in the test database."""
    fixture = Path(__file__).resolve().parents[2] / "e2e/fixtures/docling-narration.pdf"
    result = pdf_tasks.pdf_parser._fallback_parse(str(fixture))
    test_document.file_path = str(fixture)
    test_document.num_pages = result["num_pages"]
    pdf_tasks._replace_extracted_pages(db, test_document.id, result["pages"])
    db.commit()
    monkeypatch.setattr(pdf_tasks, "SessionLocal", sessionmaker(bind=db.connection(), join_transaction_mode="create_savepoint"))
    queue = Mock()
    monkeypatch.setattr(pdf_tasks.analyze_document_layout, "apply_async", queue)
    return test_document, queue


@pytest.mark.asyncio
async def test_http_claim_task_enrichment_and_repeat_clicks(client, auth_header, db, layout_pdf, monkeypatch):
    doc, queue = layout_pdf
    before = [(p.id, p.content, p.bounding_boxes) for p in db.query(ExtractedText).filter_by(document_id=doc.id).all()]
    assert (await client.get(f"/api/documents/{doc.id}/layout", headers=auth_header)).json() == {"status": "not_requested"}
    url = f"/api/documents/{doc.id}/analyze-layout"
    assert (await client.post(url, headers=auth_header)).json() == {"status": "processing"}
    task_id = queue.call_args.kwargs["task_id"]
    assert queue.call_args.kwargs["args"] == [doc.id, task_id]
    assert (await client.post(url, headers=auth_header)).json() == {"status": "processing"}
    detector = Mock(return_value={1: [{"label": "table", "narrate": False, "bbox": [0, 0, 1, 1], "source": "docling"}]})
    monkeypatch.setattr(pdf_tasks.pdf_parser, "_detect_docling_regions", detector)
    monkeypatch.setattr(pdf_tasks.pdf_parser, "parse_pdf", Mock(side_effect=AssertionError("Must not re-extract")))
    import pdfplumber
    monkeypatch.setattr(pdfplumber.page.Page, "extract_words", Mock(side_effect=AssertionError("Must not extract words")))
    assert pdf_tasks.analyze_document_layout.run(doc.id, task_id)["status"] == "ready"
    assert pdf_tasks.analyze_document_layout.run(doc.id, task_id)["status"] == "ignored"
    detector.assert_called_once_with(doc.file_path, 3)
    db.expire_all()
    assert (await client.get(f"/api/documents/{doc.id}/layout", headers=auth_header)).json() == {"status": "ready"}
    assert (await client.post(url, headers=auth_header)).json() == {"status": "ready"}
    queue.assert_called_once()
    db.expire_all()
    pages = db.query(ExtractedText).filter_by(document_id=doc.id).order_by(ExtractedText.page_number).all()
    assert [(p.id, p.content, p.bounding_boxes) for p in pages] == before
    assert pages[0].narration_exclusions
    assert pages[1].layout_regions == []
    assert pages[2].narration_exclusions == []
    assert db.get(Document, doc.id).processing_status == "extracted"


@pytest.mark.asyncio
async def test_failure_requires_explicit_click_and_empty_success_is_ready(client, auth_header, db, layout_pdf, monkeypatch):
    doc, queue = layout_pdf
    url = f"/api/documents/{doc.id}/analyze-layout"
    await client.post(url, headers=auth_header)
    task_id = queue.call_args.kwargs["task_id"]
    detector = Mock(side_effect=RuntimeError("private filesystem details"))
    monkeypatch.setattr(pdf_tasks.pdf_parser, "_detect_docling_regions", detector)
    assert pdf_tasks.analyze_document_layout.run(doc.id, task_id)["status"] == "failed"
    db.expire_all()
    status = (await client.get(f"/api/documents/{doc.id}/layout", headers=auth_header)).json()
    assert status == {"status": "failed", "error": "Layout analysis failed. Try again."}
    queue.assert_called_once()
    assert pdf_tasks.analyze_document_layout.run(doc.id, task_id)["status"] == "ignored"
    assert (await client.post(url, headers=auth_header)).json() == {"status": "processing"}
    next_id = queue.call_args.kwargs["task_id"]
    assert next_id != task_id
    detector.side_effect = None
    detector.return_value = {}
    assert pdf_tasks.analyze_document_layout.run(doc.id, next_id)["status"] == "ready"
    db.expire_all()
    assert (await client.get(f"/api/documents/{doc.id}/layout", headers=auth_header)).json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_running_duplicate_and_reparse_discard_stale_result(client, auth_header, db, layout_pdf, monkeypatch):
    doc, queue = layout_pdf
    await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)
    task_id = queue.call_args.kwargs["task_id"]

    def detect(*args):
        assert pdf_tasks.analyze_document_layout.run(doc.id, task_id)["status"] == "ignored"
        current = db.get(Document, doc.id)
        pdf_tasks.invalidate_layout(current)
        current.processing_status = "re-parsing"
        db.commit()
        return {1: [{"narrate": False, "bbox": [0, 0, 1, 1]}]}

    monkeypatch.setattr(pdf_tasks.pdf_parser, "_detect_docling_regions", detect)
    assert pdf_tasks.analyze_document_layout.run(doc.id, task_id)["status"] == "ignored"
    db.expire_all()
    assert db.get(Document, doc.id).layout_status == "not_requested"
    assert not db.query(ExtractedText).filter_by(document_id=doc.id).first().narration_exclusions
    response = await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_reparse_route_invalidates_queued_task(client, auth_header, db, layout_pdf, monkeypatch):
    doc, queue = layout_pdf
    monkeypatch.setattr(pdf_tasks.reparse_pdf_document, "delay", Mock())
    await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)
    task_id = queue.call_args.kwargs["task_id"]
    assert (await client.post(f"/api/documents/{doc.id}/re-parse", headers=auth_header)).status_code == 200
    assert pdf_tasks.analyze_document_layout.run(doc.id, task_id)["status"] == "ignored"
    assert (await client.get(f"/api/documents/{doc.id}/layout", headers=auth_header)).json() == {"status": "not_requested"}


@pytest.mark.asyncio
async def test_membership_auth_and_shared_asset_single_build(client, auth_header, db, layout_pdf, second_document, second_user, monkeypatch):
    doc, queue = layout_pdf
    for method, suffix in [("get", "layout"), ("post", "analyze-layout")]:
        url = f"/api/documents/{second_document.id}/{suffix}"
        assert (await getattr(client, method)(url, headers=auth_header)).status_code == 404
        assert (await getattr(client, method)(f"/api/documents/{doc.id}/{suffix}")).status_code == 401
    db.add(LibraryEntry(user_id=second_user.id, document_id=doc.id, title="Shared copy"))
    db.commit()
    from app.routers.auth import create_access_token
    headers = {"Authorization": f"Bearer {create_access_token({'sub': str(second_user.id)})}"}
    assert (await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=headers)).json() == {"status": "processing"}
    assert (await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)).json() == {"status": "processing"}
    queue.assert_called_once()


@pytest.mark.asyncio
async def test_enqueue_failure_and_private_conditional_file(client, auth_header, layout_pdf):
    doc, queue = layout_pdf
    queue.side_effect = RuntimeError("broker down")
    response = await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)
    assert response.json()["status"] == "failed"
    file = await client.get(f"/api/documents/{doc.id}/file", headers=auth_header)
    assert file.status_code == 200
    assert file.content.startswith(b"%PDF")
    assert file.headers["cache-control"].startswith("private")
    headers = {**auth_header, "If-None-Match": file.headers["etag"]}
    assert (await client.get(f"/api/documents/{doc.id}/file", headers=headers)).status_code == 304
    assert (await client.get(f"/api/documents/{doc.id}/file", headers={"If-None-Match": file.headers["etag"]})).status_code == 401


def test_summary_dispatch_failure_preserves_extracted_pdf(db, layout_pdf, monkeypatch):
    doc, _ = layout_pdf
    from app.tasks.summary_tasks import generate_section_summaries
    monkeypatch.setattr(generate_section_summaries, "delay", Mock(side_effect=RuntimeError("broker down")))
    result = pdf_tasks.parse_pdf_document.run(doc.id)
    assert result["status"] == "extracted"
    db.expire_all()
    assert db.get(Document, doc.id).processing_status == "extracted"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["queued", "running"])
async def test_abandoned_task_expires_and_next_click_retries(client, auth_header, db, layout_pdf, state):
    doc, queue = layout_pdf
    await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)
    old_id = queue.call_args.kwargs["task_id"]
    doc.layout_status = state
    doc.layout_requested_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()
    response = await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)
    assert response.json() == {"status": "failed", "error": "Layout analysis timed out. Try again."}
    queue.assert_called_once()
    assert pdf_tasks.analyze_document_layout.run(doc.id, old_id)["status"] == "ignored"
    assert (await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)).json() == {"status": "processing"}
    assert queue.call_count == 2
    assert queue.call_args.kwargs["task_id"] != old_id


def test_migration_backfills_empty_success_and_preserves_unanalyzed(tmp_path):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    path = Path(__file__).resolve().parents[1] / "alembic/versions/a91c2e7d408b_optional_document_layout.py"
    spec = importlib.util.spec_from_file_location("optional_layout_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'layout-migration.sqlite'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE documents (id INTEGER PRIMARY KEY, format TEXT, num_pages INTEGER)"))
            connection.execute(text("CREATE TABLE extracted_text (id INTEGER PRIMARY KEY, document_id INTEGER, layout_regions JSON, narration_exclusions JSON)"))
            connection.execute(text("INSERT INTO documents VALUES (1, 'pdf', 1), (2, 'pdf', 1), (3, 'pdf', 1), (4, 'pdf', 2), (5, 'txt', 1)"))
            connection.execute(text("INSERT INTO extracted_text VALUES (1, 1, '[]', '[]'), (2, 2, 'null', 'null'), (3, 3, NULL, NULL), (4, 4, '[]', '[]'), (5, 5, '[]', '[]')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert connection.execute(text("SELECT id, layout_status FROM documents ORDER BY id")).all() == [
                (1, "ready"), (2, "not_requested"), (3, "not_requested"),
                (4, "not_requested"), (5, "not_requested"),
            ]
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "layout_status" not in {column["name"] for column in inspect(connection).get_columns("documents")}
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_http_clicks_claim_one_build(app, tmp_path, monkeypatch):
    import asyncio
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import create_engine
    from app.database import Base, get_db
    from app.models import User
    from app.routers.auth import create_access_token

    engine = create_engine(f"sqlite:///{tmp_path / 'layout-race.sqlite'}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = User(username="layout_race", password_hash="unused")
        db.add(user)
        db.flush()
        doc = Document(user_id=user.id, title="race.pdf", format="pdf", file_path="/unused.pdf", file_size=1, num_pages=1, processing_status="extracted")
        db.add(doc)
        db.flush()
        db.add(ExtractedText(document_id=doc.id, page_number=1, content="Persisted page"))
        db.commit()
        user_id, doc_id = user.id, doc.id

    def separate_request_session():
        with sessions() as db:
            yield db

    old_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = separate_request_session
    queue = Mock()
    monkeypatch.setattr(pdf_tasks.analyze_document_layout, "apply_async", queue)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': str(user_id)})}"}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            responses = await asyncio.gather(*[
                http.post(f"/api/documents/{doc_id}/analyze-layout", headers=headers)
                for _ in range(8)
            ])
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json() == {"status": "processing"} for response in responses)
        queue.assert_called_once()
        with sessions() as db:
            assert db.get(Document, doc_id).layout_task_id == queue.call_args.kwargs["task_id"]
    finally:
        app.dependency_overrides[get_db] = old_override
        engine.dispose()


@pytest.mark.asyncio
async def test_crop_rotation_uses_persisted_extraction_dimensions(client, auth_header, db, layout_pdf, tmp_path, monkeypatch):
    import pypdfium2 as pdfium

    doc, queue = layout_pdf
    cropped = tmp_path / "cropped-rotated.pdf"
    with pdfium.PdfDocument(doc.file_path) as pdf:
        page = pdf[0]
        try:
            page.set_cropbox(20, 30, 550, 730)
            page.set_rotation(90)
        finally:
            page.close()
        pdf.save(str(cropped))
    parsed = pdf_tasks.pdf_parser._fallback_parse(str(cropped))
    doc.file_path = str(cropped)
    pdf_tasks._replace_extracted_pages(db, doc.id, parsed["pages"])
    db.commit()
    await client.post(f"/api/documents/{doc.id}/analyze-layout", headers=auth_header)
    compute = Mock(wraps=pdf_tasks.compute_narration_exclusions)
    monkeypatch.setattr(pdf_tasks, "compute_narration_exclusions", compute)
    monkeypatch.setattr(pdf_tasks.pdf_parser, "_detect_docling_regions", Mock(return_value={}))
    assert pdf_tasks.analyze_document_layout.run(doc.id, queue.call_args.kwargs["task_id"])["status"] == "ready"
    assert compute.call_args_list[0].kwargs == {
        "page_width": parsed["pages"][0]["page_width"],
        "page_height": parsed["pages"][0]["page_height"],
    }
