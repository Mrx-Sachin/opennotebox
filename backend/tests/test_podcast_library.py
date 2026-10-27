"""Hermetic backend tests for podcast library listing and isolation."""
from datetime import datetime, timedelta, timezone

from app.models import Document, LibraryEntry, Podcast
from app.services.auth_service import create_access_token


def _header(user_id: int) -> dict[str, str]:
    token = create_access_token({"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}


def _create_doc(db, user_id: int, title: str, **kwargs) -> Document:
    doc = Document(
        user_id=user_id,
        title=title,
        format="pdf",
        file_path="/tmp/fake.pdf",
        file_size=1024,
        processing_status="completed",
        **kwargs,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _create_podcast(db, user_id: int, document_id: int, **kwargs) -> Podcast:
    podcast = Podcast(
        user_id=user_id,
        document_id=document_id,
        scope=kwargs.pop("scope", "full"),
        status=kwargs.pop("status", "completed"),
        length=kwargs.pop("length", "medium"),
        duration=kwargs.pop("duration", 180.0),
        **kwargs,
    )
    db.add(podcast)
    db.commit()
    db.refresh(podcast)
    return podcast


async def test_get_podcasts_existing_contract_with_document_id(
    client, db, test_user, test_document, auth_header
):
    pod = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        created_at=datetime.now(timezone.utc),
    )

    response = await client.get(
        f"/api/podcasts?document_id={test_document.id}",
        headers=auth_header,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    item = data[0]
    assert item["id"] == pod.id
    assert item["document_id"] == test_document.id
    assert item["user_id"] == test_user.id
    assert item["status"] == "completed"
    assert "document_title" not in item
    assert item["audio_url"] == f"/api/podcasts/{pod.id}/audio"


async def test_get_podcasts_document_id_not_found_and_other_user(
    client, db, test_user, second_user, auth_header
):
    doc_other = _create_doc(db, second_user.id, "Second User Doc")
    _create_podcast(db, second_user.id, doc_other.id)

    res_missing = await client.get("/api/podcasts?document_id=999999", headers=auth_header)
    assert res_missing.status_code == 404

    res_forbidden = await client.get(
        f"/api/podcasts?document_id={doc_other.id}",
        headers=auth_header,
    )
    assert res_forbidden.status_code == 404


async def test_get_podcasts_all_books_unauthorized(client):
    response = await client.get("/api/podcasts")
    assert response.status_code == 401


async def test_get_podcasts_all_books_empty(client, auth_header):
    response = await client.get("/api/podcasts", headers=auth_header)
    assert response.status_code == 200
    assert response.json() == []


async def test_get_podcasts_all_books_lists_multiple_ordered(
    client, db, test_user, auth_header
):
    now = datetime.now(timezone.utc)
    doc_a = _create_doc(db, test_user.id, "Book Alpha")
    doc_b = _create_doc(db, test_user.id, "Book Beta")

    pod1 = _create_podcast(
        db, test_user.id, doc_a.id, created_at=now - timedelta(minutes=10)
    )
    pod2 = _create_podcast(
        db, test_user.id, doc_b.id, created_at=now
    )

    response = await client.get("/api/podcasts", headers=auth_header)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    assert items[0]["id"] == pod2.id
    assert items[0]["document_title"] == "Book Beta"
    assert items[1]["id"] == pod1.id
    assert items[1]["document_title"] == "Book Alpha"


async def test_get_podcasts_title_override_precedence(
    client, db, test_user, second_user
):
    shared_doc = _create_doc(
        db,
        second_user.id,
        "Secret Private Name.pdf",
        is_shared=True,
        shared_title="Shared Public Title",
    )
    entry = LibraryEntry(
        user_id=test_user.id,
        document_id=shared_doc.id,
        can_publish=False,
    )
    db.add(entry)
    db.commit()

    pod = _create_podcast(db, test_user.id, shared_doc.id)

    res = await client.get("/api/podcasts", headers=_header(test_user.id))
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["id"] == pod.id and items[0]["document_title"] == "Shared Public Title"

    entry.title = "Custom Renamed Title"
    db.commit()

    res_override = await client.get("/api/podcasts", headers=_header(test_user.id))
    assert res_override.status_code == 200
    items_override = res_override.json()
    assert len(items_override) == 1
    assert items_override[0]["document_title"] == "Custom Renamed Title"


async def test_get_podcasts_membership_isolation_and_revocation(
    client, db, test_user, second_user
):
    doc = _create_doc(db, test_user.id, "Isolated Doc")
    _create_podcast(db, test_user.id, doc.id)

    res_second = await client.get("/api/podcasts", headers=_header(second_user.id))
    assert res_second.status_code == 200
    assert res_second.json() == []

    db.query(LibraryEntry).filter(
        LibraryEntry.user_id == test_user.id,
        LibraryEntry.document_id == doc.id,
    ).delete()
    db.commit()

    res_after_delete = await client.get("/api/podcasts", headers=_header(test_user.id))
    assert res_after_delete.status_code == 200
    assert res_after_delete.json() == []
