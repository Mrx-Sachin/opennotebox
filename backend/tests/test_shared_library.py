"""Shared-library catalog and membership behavior."""
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.config import settings
from app.models import LibraryEntry
from app.services.library_access import document_response, get_document_for_user


def _header_for_user(user_id: int) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": str(user_id),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "type": "access",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


async def _publish(client, headers, document_id: int, **metadata):
    payload = {
        "is_shared": True,
        "title": "Published title",
        "author": "A. Author",
        "description": "A public description",
        "topics": [" Machine Learning ", "AI", "ai"],
        "document_type": "Research paper",
        "language": "en",
    }
    payload.update(metadata)
    return await client.patch(
        f"/api/shared-library/{document_id}", headers=headers, json=payload
    )


async def test_publish_search_add_is_idempotent_and_unshare_keeps_membership(
    client, auth_header, test_user, second_user, test_document, db
):
    published = await _publish(client, auth_header, test_document.id)
    assert published.status_code == 200
    assert published.json()["can_share"] is True
    assert published.json()["read_only"] is True
    assert published.json()["topics"] == ["machine learning", "ai"]

    recipient_headers = _header_for_user(second_user.id)
    catalog = await client.get(
        "/api/shared-library",
        params={"search": "published title", "topic": "machine learning"},
        headers=recipient_headers,
    )
    assert catalog.status_code == 200
    assert catalog.json()["total"] == 1
    assert catalog.json()["documents"][0]["title"] == "Published title"
    assert catalog.json()["documents"][0]["added"] is False
    topic_search = await client.get(
        "/api/shared-library", params={"search": "learning"}, headers=recipient_headers
    )
    assert topic_search.status_code == 200
    assert topic_search.json()["documents"][0]["id"] == test_document.id

    notebook = await client.post(
        "/api/notebooks", headers=recipient_headers, json={"name": "Reading"}
    )
    assert notebook.status_code == 201
    notebook_id = notebook.json()["id"]

    added = await client.post(
        "/api/shared-library/add",
        headers=recipient_headers,
        json={"document_ids": [test_document.id], "notebook_id": notebook_id},
    )
    assert added.status_code == 200
    assert added.json() == {
        "added": 1,
        "already_added": 0,
        "document_ids": [test_document.id],
    }

    entry = db.query(LibraryEntry).filter_by(
        user_id=second_user.id, document_id=test_document.id
    ).one()
    entry.last_read_page = 2
    entry.last_read_position = 0.75
    db.commit()
    # Asset progress remains the uploader's legacy state. The recipient's
    # serializer reads the membership row instead.
    test_document.last_read_page = 1
    db.commit()
    recipient_view = document_response(test_document, entry, member_count=2)
    assert recipient_view["user_id"] == second_user.id
    assert recipient_view["title"] == "Published title"
    assert recipient_view["notebook_id"] == notebook_id
    assert recipient_view["last_read_page"] == 2
    assert recipient_view["last_read_position"] == 0.75
    assert recipient_view["original_url"] is None
    assert recipient_view["read_only"] is True
    assert recipient_view["can_share"] is False

    other_notebook = await client.post(
        "/api/notebooks", headers=recipient_headers, json={"name": "Other"}
    )
    repeated_add = await client.post(
        "/api/shared-library/add",
        headers=recipient_headers,
        json={
            "document_ids": [test_document.id, test_document.id],
            "notebook_id": other_notebook.json()["id"],
        },
    )
    assert repeated_add.status_code == 200
    assert repeated_add.json() == {
        "added": 0,
        "already_added": 1,
        "document_ids": [],
    }
    db.refresh(entry)
    assert entry.notebook_id == notebook_id

    unpublished = await client.patch(
        f"/api/shared-library/{test_document.id}",
        headers=auth_header,
        json={"is_shared": False},
    )
    assert unpublished.status_code == 200
    assert (await client.get("/api/shared-library", headers=recipient_headers)).json()["total"] == 0
    assert get_document_for_user(db, second_user.id, test_document.id).id == test_document.id


async def test_recommendations_match_notebook_name_and_shared_topics(
    client, auth_header, test_user, second_user, test_document, db
):
    assert (await _publish(
        client,
        auth_header,
        test_document.id,
        title="Neural Networks Paper",
        topics=["machine learning"],
    )).status_code == 200
    recipient_headers = _header_for_user(second_user.id)
    notebook = await client.post(
        "/api/notebooks", headers=recipient_headers, json={"name": "Machine Learning"}
    )
    response = await client.get(
        "/api/shared-library",
        params={"notebook_id": notebook.json()["id"]},
        headers=recipient_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["documents"][0]["reason"] == "Shares the notebook topic machine learning"


async def test_publish_accepts_blank_optional_metadata_as_empty(
    client, auth_header, test_document
):
    response = await _publish(
        client,
        auth_header,
        test_document.id,
        author="",
        description="   ",
        language="",
    )
    assert response.status_code == 200
    assert response.json()["author"] is None
    assert response.json()["description"] is None
    assert response.json()["language"] is None


async def test_only_upload_member_can_publish_and_public_file_has_safe_name(
    client, auth_header, test_user, second_user, test_document
):
    recipient_headers = _header_for_user(second_user.id)
    unpublished = await client.patch(
        f"/api/shared-library/{test_document.id}",
        headers=auth_header,
        json={"is_shared": False},
    )
    assert unpublished.status_code == 200

    missing = await client.get(
        f"/api/shared-library/{test_document.id}/file", headers=recipient_headers
    )
    assert missing.status_code == 404

    published = await _publish(client, auth_header, test_document.id)
    assert published.status_code == 200
    downloaded = await client.get(
        f"/api/shared-library/{test_document.id}/file", headers=recipient_headers
    )
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")
    assert "shared-document.pdf" in downloaded.headers["content-disposition"]

    added = await client.post(
        "/api/shared-library/add",
        headers=recipient_headers,
        json={"document_ids": [test_document.id]},
    )
    assert added.status_code == 200
    impersonated_publish = await _publish(
        client, recipient_headers, test_document.id, title="Takeover"
    )
    assert impersonated_publish.status_code == 404
