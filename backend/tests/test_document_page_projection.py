from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import Document, ExtractedText, User


@pytest.fixture()
def rich_document(db: Session, test_user: User, tmp_path) -> Document:
    file_path = tmp_path / "rich_doc.pdf"
    file_path.write_bytes(b"%PDF- sample")
    doc = Document(
        user_id=test_user.id,
        title="Rich Document.pdf",
        format="pdf",
        file_path=str(file_path),
        file_size=file_path.stat().st_size,
        num_pages=3,
        processing_status="extracted",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    for page_num in range(1, 4):
        page = ExtractedText(
            document_id=doc.id,
            page_number=page_num,
            content=f"Page {page_num} text content with details.",
            headings=[{"title": f"Section {page_num}", "level": 1}],
            bounding_boxes=[
                {"box": [10.0, 20.0, 100.0, 50.0], "text": f"Box on page {page_num}"}
            ],
            layout_regions=[
                {"type": "paragraph", "bbox": [10.0, 20.0, 100.0, 50.0]}
            ],
            narration_exclusions=[
                {"start": 0, "end": 4, "reason": "header"}
            ],
        )
        db.add(page)
    db.commit()
    return doc


class TestDocumentPageProjection:

    async def test_default_pages_retains_full_geometry_and_narration(
        self, client, auth_header, rich_document
    ):
        resp = await client.get(
            f"/api/documents/{rich_document.id}/pages", headers=auth_header
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        for item in data:
            assert "id" in item
            assert "document_id" in item
            assert "page_number" in item
            assert "content" in item
            assert "headings" in item
            assert "bounding_boxes" in item
            assert "layout_regions" in item
            assert "narration_exclusions" in item
            assert item["bounding_boxes"] is not None
            assert len(item["bounding_boxes"]) > 0
            assert item["layout_regions"] is not None
            assert item["narration_exclusions"] is not None

    async def test_lightweight_query_param_projection(
        self, client, auth_header, rich_document
    ):
        resp = await client.get(
            f"/api/documents/{rich_document.id}/pages?lightweight=true",
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        expected_keys = {"id", "document_id", "page_number", "content", "headings"}
        for item in data:
            assert set(item.keys()) == expected_keys
            assert "bounding_boxes" not in item
            assert "layout_regions" not in item
            assert "narration_exclusions" not in item

    async def test_explicit_false_preserves_full_response(
        self, client, auth_header, rich_document
    ):
        resp = await client.get(
            f"/api/documents/{rich_document.id}/pages?lightweight=false",
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        for item in data:
            assert "bounding_boxes" in item
            assert "layout_regions" in item
            assert "narration_exclusions" in item

    async def test_field_equivalence_between_projections(
        self, client, auth_header, rich_document
    ):
        full_resp = await client.get(
            f"/api/documents/{rich_document.id}/pages", headers=auth_header
        )
        light_resp = await client.get(
            f"/api/documents/{rich_document.id}/pages?lightweight=true",
            headers=auth_header,
        )
        assert full_resp.status_code == 200
        assert light_resp.status_code == 200
        full_data = full_resp.json()
        light_data = light_resp.json()
        assert len(full_data) == len(light_data) == 3

        shared_fields = ["id", "document_id", "page_number", "content", "headings"]
        for full_page, light_page in zip(full_data, light_data):
            for field in shared_fields:
                assert light_page[field] == full_page[field]

    async def test_single_page_endpoint_retains_geometry(
        self, client, auth_header, rich_document
    ):
        resp = await client.get(
            f"/api/documents/{rich_document.id}/page/1", headers=auth_header
        )
        assert resp.status_code == 200
        item = resp.json()
        assert item["page_number"] == 1
        assert "bounding_boxes" in item
        assert item["bounding_boxes"] is not None
        assert "layout_regions" in item
        assert "narration_exclusions" in item

    async def test_no_n_plus_one_on_lightweight_pages(
        self, client, auth_header, rich_document, test_engine
    ):
        executed_statements = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            executed_statements.append(statement)

        event.listen(test_engine, "before_cursor_execute", capture_sql)
        try:
            resp = await client.get(
                f"/api/documents/{rich_document.id}/pages?lightweight=true",
                headers=auth_header,
            )
            assert resp.status_code == 200
            page_selects = [
                s for s in executed_statements if "extracted_text" in s.lower()
            ]
            assert len(page_selects) == 1
            first_sql = page_selects[0].lower()
            assert "bounding_boxes" not in first_sql
            assert "layout_regions" not in first_sql
            assert "narration_exclusions" not in first_sql
        finally:
            event.remove(test_engine, "before_cursor_execute", capture_sql)

    async def test_pagination_with_projection(
        self, client, auth_header, rich_document
    ):
        resp = await client.get(
            f"/api/documents/{rich_document.id}/pages?lightweight=true&limit=2&offset=1",
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["page_number"] == 2
        assert data[1]["page_number"] == 3

    async def test_unauthenticated_request_rejected(self, client, rich_document):
        resp = await client.get(
            f"/api/documents/{rich_document.id}/pages?lightweight=true"
        )
        assert resp.status_code == 401

    async def test_nonexistent_document_returns_404(
        self, client, auth_header
    ):
        resp = await client.get(
            "/api/documents/99999/pages?lightweight=true", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_user_isolation_with_projection(
        self, client, auth_header, second_document
    ):
        resp = await client.get(
            f"/api/documents/{second_document.id}/pages?lightweight=true",
            headers=auth_header,
        )
        assert resp.status_code == 404

    async def test_lightweight_payload_size_reduction(
        self, client, auth_header, rich_document
    ):
        full_resp = await client.get(
            f"/api/documents/{rich_document.id}/pages", headers=auth_header
        )
        light_resp = await client.get(
            f"/api/documents/{rich_document.id}/pages?lightweight=true",
            headers=auth_header,
        )
        assert full_resp.status_code == 200
        assert light_resp.status_code == 200
        assert len(light_resp.content) < len(full_resp.content)
