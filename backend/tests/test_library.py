"""Library filtering, pagination, tenant isolation, and private preview lifecycle."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import Document, Podcast
from app.services.document_preview import preview_path, render_pdf_cover


def add_document(db, user_id, title, **values):
    doc = Document(user_id=user_id, title=title, format="pdf", file_path="/missing.pdf",
                   file_size=512, num_pages=12, processing_status="completed", **values)
    db.add(doc)
    db.commit()
    return doc


async def test_library_paginates_searches_and_keeps_recent_global(
    client, auth_header, test_user, second_document, db
):
    now = datetime.now(timezone.utc)
    recent = add_document(db, test_user.id, "Earlier reading.pdf", created_at=now - timedelta(days=2),
                          last_read_page=4, last_read_at=now)
    for index in range(27):
        add_document(db, test_user.id, f"Research {index:02}.pdf", created_at=now + timedelta(seconds=index))
    response = await client.get("/api/documents/library?limit=24", headers=auth_header)
    data = response.json()
    assert response.status_code == 200
    assert len(data["documents"]) == 24
    assert data["total"] == data["library_total"] == 28
    assert data["recent"][0]["id"] == recent.id
    assert second_document.id not in {item["id"] for item in data["documents"]}
    page_two = await client.get("/api/documents/library?limit=24&offset=24", headers=auth_header)
    assert len(page_two.json()["documents"]) == 4
    search = await client.get("/api/documents/library?search=research%2003", headers=auth_header)
    assert search.json()["total"] == 1
    assert search.json()["recent"][0]["id"] == recent.id


async def test_library_reading_shelf_and_literal_search(client, auth_header, test_user, db):
    add_document(db, test_user.id, "100% useful.pdf", last_read_at=datetime.now(timezone.utc))
    add_document(db, test_user.id, "Another title.pdf")
    reading = await client.get("/api/documents/library?shelf=reading", headers=auth_header)
    assert reading.json()["total"] == 1
    literal = await client.get("/api/documents/library", params={"search": "%"}, headers=auth_header)
    assert literal.json()["total"] == 1
    unread = await client.get("/api/documents/library?shelf=unread", headers=auth_header)
    assert unread.json()["total"] == 1


async def test_library_auth_and_validation(client, auth_header):
    assert (await client.get("/api/documents/library")).status_code == 401
    assert (await client.get("/api/documents/library?limit=101", headers=auth_header)).status_code == 422
    assert (await client.get("/api/documents/library?format=exe", headers=auth_header)).status_code == 422


async def test_preview_never_exposes_another_users_file(client, auth_header, second_document):
    assert (await client.get(f"/api/documents/{second_document.id}/preview", headers=auth_header)).status_code == 404


async def test_cached_preview_is_private(client, auth_header, test_document):
    path = preview_path(test_document.user_id, test_document.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test-preview")
    response = await client.get(f"/api/documents/{test_document.id}/preview", headers=auth_header)
    assert response.status_code == 200
    assert response.content == b"test-preview"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["content-type"] == "image/webp"


async def test_preview_queues_once_without_rendering_in_api(client, auth_header, test_document, tmp_path):
    original = tmp_path / "sample.pdf"
    original.write_bytes(b"%PDF")
    test_document.file_path = str(original)
    destination = preview_path(test_document.user_id, test_document.id)
    destination.unlink(missing_ok=True)
    with patch("redis.Redis.from_url") as redis_factory, patch("app.tasks.preview_tasks.generate_document_preview.delay") as enqueue:
        redis_client = redis_factory.return_value.__enter__.return_value
        redis_client.set.side_effect = [True, False]
        for _ in range(2):
            response = await client.get(f"/api/documents/{test_document.id}/preview", headers=auth_header)
            assert response.status_code == 202
        enqueue.assert_called_once_with(test_document.id)


async def test_document_delete_removes_preview_and_podcast(client, auth_header, test_document, test_user, db, tmp_path):
    preview = preview_path(test_user.id, test_document.id)
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"cover")
    audio = tmp_path / "podcast.wav"
    audio.write_bytes(b"audio")
    podcast = Podcast(user_id=test_user.id, document_id=test_document.id, scope="document", audio_path=str(audio))
    db.add(podcast)
    db.commit()
    response = await client.delete(f"/api/documents/{test_document.id}", headers=auth_header)
    assert response.status_code == 200
    assert not preview.exists()
    assert not audio.exists()


def test_real_pdf_cover_is_bounded_and_atomic(tmp_path):
    from PIL import Image

    source = Path(__file__).resolve().parents[2] / "e2e" / "fixtures" / "e2e-sample.pdf"
    destination = tmp_path / "cover.webp"
    render_pdf_cover(str(source), destination)
    with Image.open(destination) as image:
        assert image.format == "WEBP"
        assert image.width <= 360 and image.height <= 480
    assert list(tmp_path.iterdir()) == [destination]


async def test_only_admin_can_control_shared_gpu_or_probe_urls(client, auth_header):
    assert (await client.post("/api/tts/release-gpu")).status_code == 401
    assert (await client.post("/api/tts/release-gpu", headers=auth_header)).status_code == 403
    response = await client.post("/api/settings/llm/probe", json={"base_url": "http://127.0.0.1:9000/v1"}, headers=auth_header)
    assert response.status_code == 403
