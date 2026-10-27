"""Notebook CRUD, tenant isolation, and document filing behavior."""
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch

from app.models import Document, Notebook


def add_notebook(db, user_id: int, name: str = "Research") -> Notebook:
    notebook = Notebook(user_id=user_id, name=name)
    db.add(notebook)
    db.commit()
    db.refresh(notebook)
    return notebook


def add_document(db, user_id: int, title: str, **values) -> Document:
    document = Document(
        user_id=user_id,
        title=title,
        format=values.pop("format", "pdf"),
        file_path=values.pop("file_path", "/missing.pdf"),
        file_size=values.pop("file_size", 100),
        processing_status=values.pop("processing_status", "completed"),
        **values,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


async def test_notebook_create_trim_duplicate_and_list_empty(
    client, auth_header, second_user, db
):
    created = await client.post(
        "/api/notebooks",
        headers=auth_header,
        json={"name": "  Reading list  ", "description": "Papers to read"},
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Reading list"
    assert created.json()["description"] == "Papers to read"
    assert created.json()["document_count"] == 0

    duplicate = await client.post(
        "/api/notebooks", headers=auth_header, json={"name": "reading LIST"}
    )
    assert duplicate.status_code == 409
    blank = await client.post("/api/notebooks", headers=auth_header, json={"name": "  "})
    assert blank.status_code == 422

    listed = await client.get("/api/notebooks", headers=auth_header)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]
    assert listed.json()[0]["document_count"] == 0

    # Another owner may use the same name.
    other = add_notebook(db, user_id=second_user.id, name="Reading list")
    assert other.user_id == second_user.id


async def test_notebook_rename_and_delete_preserve_document_file(
    client, auth_header, test_document, tmp_path
):
    notebook = await client.post(
        "/api/notebooks", headers=auth_header, json={"name": "Old name"}
    )
    notebook_id = notebook.json()["id"]
    original_file = tmp_path / "test.pdf"
    assert original_file.exists()

    move = await client.post(
        "/api/notebooks/move-documents",
        headers=auth_header,
        json={"document_ids": [test_document.id], "notebook_id": notebook_id},
    )
    assert move.status_code == 200
    assert move.json() == {"moved": 1}

    renamed = await client.patch(
        f"/api/notebooks/{notebook_id}",
        headers=auth_header,
        json={"name": "  New name  ", "description": "Updated"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "New name"
    assert renamed.json()["description"] == "Updated"
    assert renamed.json()["document_count"] == 1

    deleted = await client.delete(f"/api/notebooks/{notebook_id}", headers=auth_header)
    assert deleted.status_code == 204
    document_response = await client.get(
        f"/api/documents/{test_document.id}", headers=auth_header
    )
    assert document_response.status_code == 200
    assert document_response.json()["notebook_id"] is None
    assert original_file.exists()


async def test_notebook_and_document_tenant_isolation(
    client, auth_header, second_user, second_document, db
):
    foreign_notebook = add_notebook(db, second_user.id, "Private")
    assert second_document.user_id == second_user.id

    listed = await client.get("/api/notebooks", headers=auth_header)
    assert all(item["id"] != foreign_notebook.id for item in listed.json())
    assert (
        await client.patch(
            f"/api/notebooks/{foreign_notebook.id}",
            headers=auth_header,
            json={"name": "Stolen"},
        )
    ).status_code == 404
    assert (
        await client.delete(
            f"/api/notebooks/{foreign_notebook.id}", headers=auth_header
        )
    ).status_code == 404
    assert (
        await client.get(
            "/api/documents/library",
            params={"notebook_id": foreign_notebook.id},
            headers=auth_header,
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/api/documents/{second_document.id}", headers=auth_header
        )
    ).status_code == 404


async def test_move_batch_is_atomic_when_a_document_is_foreign(
    client, auth_header, test_user, second_document, db
):
    notebook = add_notebook(db, test_user.id)
    owned_document = add_document(db, test_user.id, "Owned.pdf")

    response = await client.post(
        "/api/notebooks/move-documents",
        headers=auth_header,
        json={
            "document_ids": [owned_document.id, second_document.id],
            "notebook_id": notebook.id,
        },
    )
    assert response.status_code == 404
    db.refresh(owned_document)
    assert owned_document.notebook_id is None

    bad_destination = await client.post(
        "/api/notebooks/move-documents",
        headers=auth_header,
        json={"document_ids": [owned_document.id], "notebook_id": 999999},
    )
    assert bad_destination.status_code == 404
    db.refresh(owned_document)
    assert owned_document.notebook_id is None


async def test_library_notebook_and_unfiled_scopes_all_aggregates(
    client, auth_header, test_user, db
):
    notebook = add_notebook(db, test_user.id)
    now = datetime.now(timezone.utc)
    add_document(
        db,
        test_user.id,
        "Read.pdf",
        notebook_id=notebook.id,
        format="pdf",
        last_read_at=now,
    )
    add_document(
        db,
        test_user.id,
        "Broken.txt",
        notebook_id=notebook.id,
        format="txt",
        processing_status="failed",
    )
    add_document(db, test_user.id, "Loose.epub", format="epub")

    scoped = await client.get(
        "/api/documents/library",
        params={"notebook_id": notebook.id},
        headers=auth_header,
    )
    data = scoped.json()
    assert scoped.status_code == 200
    assert data["total"] == data["library_total"] == 2
    assert data["reading_count"] == 1
    assert data["failed_count"] == 1
    assert data["formats"] == {"pdf": 1, "txt": 1}
    assert len(data["recent"]) == 1
    assert all(document["notebook_id"] == notebook.id for document in data["documents"])

    unfiled = await client.get(
        "/api/documents/library", params={"unfiled": "true"}, headers=auth_header
    )
    assert unfiled.json()["library_total"] == 1
    assert unfiled.json()["formats"] == {"epub": 1}
    assert unfiled.json()["documents"][0]["notebook_id"] is None

    conflict = await client.get(
        "/api/documents/library",
        params={"notebook_id": notebook.id, "unfiled": "true"},
        headers=auth_header,
    )
    assert conflict.status_code == 422


@patch("app.routers.documents._dispatch_parse")
async def test_upload_assigns_owned_notebook_and_rejects_foreign_notebook(
    mock_dispatch, client, auth_header, test_user, second_user, db
):
    notebook = add_notebook(db, test_user.id)
    foreign_notebook = add_notebook(db, second_user.id, "Foreign")

    uploaded = await client.post(
        "/api/documents/upload",
        headers=auth_header,
        data={"notebook_id": str(notebook.id)},
        files={"file": ("in-folder.pdf", BytesIO(b"%PDF- content"), "application/pdf")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["notebook_id"] == notebook.id

    before = await client.get("/api/documents", headers=auth_header)
    response = await client.post(
        "/api/documents/upload",
        headers=auth_header,
        data={"notebook_id": str(foreign_notebook.id)},
        files={"file": ("rejected.pdf", BytesIO(b"%PDF- content"), "application/pdf")},
    )
    assert response.status_code == 404
    after = await client.get("/api/documents", headers=auth_header)
    assert len(after.json()) == len(before.json())


async def test_url_upload_rejects_foreign_notebook_before_fetch(
    client, auth_header, second_user, db
):
    foreign_notebook = add_notebook(db, second_user.id, "Foreign")
    response = await client.post(
        "/api/documents/upload-url",
        headers=auth_header,
        data={"url": "http://127.0.0.1/private.pdf", "notebook_id": str(foreign_notebook.id)},
    )
    assert response.status_code == 404


async def test_reader_document_list_scope_and_ownership(client, auth_header, second_user, db):
    from app.models import User
    owner = db.query(User).filter(User.id != second_user.id).first()
    notebook = add_notebook(db, owner.id)
    foreign = add_notebook(db, second_user.id, "Private")
    filed = add_document(db, owner.id, "Filed", notebook_id=notebook.id)
    loose = add_document(db, owner.id, "Loose")
    response = await client.get(f"/api/documents?notebook_id={notebook.id}", headers=auth_header)
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [filed.id]
    response = await client.get("/api/documents?unfiled=true", headers=auth_header)
    assert [item["id"] for item in response.json()] == [loose.id]
    response = await client.get(f"/api/documents?notebook_id={foreign.id}", headers=auth_header)
    assert response.status_code == 404
    response = await client.get(f"/api/documents?notebook_id={notebook.id}&unfiled=true", headers=auth_header)
    assert response.status_code == 422
