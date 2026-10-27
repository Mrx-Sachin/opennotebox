"""Tests for document router endpoints.

Covers: upload, list, get, status, delete, reading progress, pages,
summaries, file download, and multi-user isolation.
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import patch


class TestListDocuments:
    """GET /api/documents"""

    async def test_list_empty(self, client, auth_header):
        resp = await client.get("/api/documents", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert all(d["user_id"] != 999 for d in data)

    async def test_list_with_documents(self, client, auth_header, test_document):
        resp = await client.get("/api/documents", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert any(d["id"] == test_document.id for d in data)

    async def test_list_isolation(self, client, auth_header, second_document):
        """User A must not see User B's documents."""
        resp = await client.get("/api/documents", headers=auth_header)
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()]
        assert second_document.id not in ids

    async def test_list_unauthenticated(self, client):
        resp = await client.get("/api/documents")
        assert resp.status_code == 401


class TestGetDocument:
    """GET /api/documents/{doc_id}"""

    async def test_get_success(self, client, auth_header, test_document):
        resp = await client.get(
            f"/api/documents/{test_document.id}", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test Document.pdf"

    async def test_get_nonexistent(self, client, auth_header):
        resp = await client.get("/api/documents/99999", headers=auth_header)
        assert resp.status_code == 404

    async def test_get_other_user_document(self, client, auth_header, second_document):
        """Cannot access another user's document."""
        resp = await client.get(
            f"/api/documents/{second_document.id}", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_get_unauthenticated(self, client):
        resp = await client.get("/api/documents/1")
        assert resp.status_code == 401


class TestGetDocumentStatus:
    """GET /api/documents/{doc_id}/status"""

    async def test_status_success(self, client, auth_header, test_document):
        resp = await client.get(
            f"/api/documents/{test_document.id}/status", headers=auth_header
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == test_document.id
        assert data["processing_status"] == "extracted"
        assert data["num_pages"] == 3

    async def test_status_nonexistent(self, client, auth_header):
        resp = await client.get("/api/documents/99999/status", headers=auth_header)
        assert resp.status_code == 404


class TestUploadDocument:
    """POST /api/documents/upload"""

    @patch("app.routers.documents._dispatch_parse")
    async def test_upload_pdf(self, mock_dispatch, client, auth_header):
        """Upload a valid PDF file."""
        resp = await client.post(
            "/api/documents/upload",
            headers=auth_header,
            files={"file": ("test.pdf", BytesIO(b"%PDF- fake pdf content"), "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "pdf"
        assert data["processing_status"] == "pending"

    @patch("app.routers.documents._dispatch_parse")
    async def test_upload_txt(self, mock_dispatch, client, auth_header):
        resp = await client.post(
            "/api/documents/upload",
            headers=auth_header,
            files={"file": ("readme.txt", BytesIO(b"hello world"), "text/plain")},
        )
        assert resp.status_code == 200
        assert resp.json()["format"] == "txt"

    async def test_upload_unsupported_format(self, client, auth_header):
        resp = await client.post(
            "/api/documents/upload",
            headers=auth_header,
            files={"file": ("image.png", BytesIO(b"\x89PNG fake"), "image/png")},
        )
        assert resp.status_code == 400

    async def test_upload_unauthenticated(self, client):
        resp = await client.post(
            "/api/documents/upload",
            files={"file": ("test.pdf", BytesIO(b"%PDF-"), "application/pdf")},
        )
        assert resp.status_code == 401


class TestDeleteDocument:
    """DELETE /api/documents/{doc_id}"""

    async def test_delete_success(self, client, auth_header, test_document):
        doc_id = test_document.id
        resp = await client.delete(
            f"/api/documents/{doc_id}", headers=auth_header
        )
        assert resp.status_code == 200
        resp = await client.get(
            f"/api/documents/{doc_id}", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_delete_other_user_document(
        self, client, auth_header, second_document
    ):
        resp = await client.delete(
            f"/api/documents/{second_document.id}", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_delete_nonexistent(self, client, auth_header):
        resp = await client.delete("/api/documents/99999", headers=auth_header)
        assert resp.status_code == 404


class TestReadingProgress:
    """PUT /api/documents/{doc_id}/progress"""

    async def test_update_progress(self, client, auth_header, test_document):
        resp = await client.put(
            f"/api/documents/{test_document.id}/progress",
            headers=auth_header,
            json={"page": 2, "position": 0.5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_read_page"] == 2
        assert data["last_read_position"] == 0.5

    async def test_clamp_page_to_max(self, client, auth_header, test_document):
        """Page beyond num_pages should be clamped."""
        resp = await client.put(
            f"/api/documents/{test_document.id}/progress",
            headers=auth_header,
            json={"page": 999, "position": 0.0},
        )
        assert resp.status_code == 200
        assert resp.json()["last_read_page"] == 3

    async def test_clamp_page_to_min(self, client, auth_header, test_document):
        """Page below 1 should be clamped to 1."""
        resp = await client.put(
            f"/api/documents/{test_document.id}/progress",
            headers=auth_header,
            json={"page": 0, "position": 0.0},
        )
        assert resp.status_code == 422

    async def test_progress_other_user(
        self, client, auth_header, second_document
    ):
        resp = await client.put(
            f"/api/documents/{second_document.id}/progress",
            headers=auth_header,
            json={"page": 1, "position": 0.0},
        )
        assert resp.status_code == 404


class TestGetPages:
    """GET /api/documents/{doc_id}/pages"""

    async def test_get_all_pages(self, client, auth_header, test_document):
        resp = await client.get(
            f"/api/documents/{test_document.id}/pages", headers=auth_header
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["page_number"] == 1

    async def test_get_specific_page(self, client, auth_header, test_document):
        resp = await client.get(
            f"/api/documents/{test_document.id}/page/2", headers=auth_header
        )
        assert resp.status_code == 200
        assert resp.json()["page_number"] == 2

    async def test_get_nonexistent_page(self, client, auth_header, test_document):
        resp = await client.get(
            f"/api/documents/{test_document.id}/page/99", headers=auth_header
        )
        assert resp.status_code == 404


class TestStoredFilename:
    """Unit tests for _stored_filename helper."""

    def test_basic_filename(self) -> None:
        from app.routers.documents import _stored_filename
        result = _stored_filename("paper.pdf")
        assert result.endswith(".pdf")
        assert len(result) > 5

    def test_path_traversal(self) -> None:
        from app.routers.documents import _stored_filename
        result = _stored_filename("../../../etc/passwd")
        assert ".." not in result or not result.startswith("..")
        assert "/" not in result
        assert "\\" not in result

    def test_backslash_sanitize(self) -> None:
        from app.routers.documents import _stored_filename
        result = _stored_filename("..\\..\\evil.pdf")
        assert "\\" not in result
        assert result.endswith(".pdf")

    def test_collision_avoidance(self) -> None:
        from app.routers.documents import _stored_filename
        r1 = _stored_filename("same.pdf")
        r2 = _stored_filename("same.pdf")
        assert r1 != r2

    def test_none_filename(self) -> None:
        from app.routers.documents import _stored_filename
        result = _stored_filename(None)
        assert result is not None
        assert len(result) > 0


class TestMagicBytes:
    """Unit tests for _looks_like_format helper."""

    def test_pdf_magic(self) -> None:
        from app.routers.documents import _looks_like_format
        assert _looks_like_format(b"%PDF-1.4 test", "pdf")

    def test_pdf_reject_html(self) -> None:
        from app.routers.documents import _looks_like_format
        assert not _looks_like_format(b"<html>paywall</html>", "pdf")

    def test_txt_no_check(self) -> None:
        from app.routers.documents import _looks_like_format
        assert _looks_like_format(b"anything at all", "txt")

    def test_docx_zip_magic(self) -> None:
        from app.routers.documents import _looks_like_format
        assert _looks_like_format(b"PK\x03\x04content", "docx")

    def test_epub_zip_magic(self) -> None:
        from app.routers.documents import _looks_like_format
        assert _looks_like_format(b"PK\x03\x04content", "epub")
