"""Tests for notes / highlights and the per-user colour palette."""
from __future__ import annotations


def _create(client, auth_header, document_id, **kwargs):
    body = {
        "document_id": document_id,
        "page_number": 1,
        "quote": "The quick brown fox jumps over the lazy dog.",
        "color": "amber",
        **kwargs,
    }
    return client.post("/api/notes", headers=auth_header, json=body)


class TestCreateNote:
    async def test_multi_page_range(self, client, auth_header, test_document):
        resp = await _create(
            client,
            auth_header,
            test_document.id,
            page_number=1,
            end_page=3,
            quote="The quick brown fox jumps over the lazy dog.",
        )
        assert resp.status_code == 200
        assert resp.json()["page_number"] == 1
        assert resp.json()["end_page"] == 3

    async def test_highlight_only(self, client, auth_header, test_document):
        resp = await _create(client, auth_header, test_document.id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["quote"].startswith("The quick brown fox")
        assert data["note"] is None
        assert data["color"] == "amber"
        assert data["page_number"] == 1
        # Server fills context + offsets from the extracted page.
        assert data["word_range"] is not None
        assert data["context_before"] is not None
        assert "testing" in (data["context_before"] or "").lower() or data["context_before"] != ""

    async def test_note_with_body(self, client, auth_header, test_document):
        resp = await _create(
            client,
            auth_header,
            test_document.id,
            note="Key claim - contrast with LSTM baselines.",
            color="green",
        )
        assert resp.status_code == 200
        assert resp.json()["note"].startswith("Key claim")
        assert resp.json()["color"] == "green"

    async def test_page_note_without_quote(self, client, auth_header, test_document):
        resp = await _create(
            client,
            auth_header,
            test_document.id,
            quote=None,
            note="A margin thought about this page.",
            color="indigo",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quote"] is None
        assert data["note"].startswith("A margin thought")

    async def test_empty_rejected(self, client, auth_header, test_document):
        resp = await _create(
            client, auth_header, test_document.id, quote="", note=""
        )
        assert resp.status_code == 400

    async def test_unknown_color_rejected(self, client, auth_header, test_document):
        resp = await _create(client, auth_header, test_document.id, color="rose")
        assert resp.status_code == 400

    async def test_other_users_document(self, client, auth_header, second_document):
        resp = await _create(client, auth_header, second_document.id)
        assert resp.status_code == 404

    async def test_unauthenticated(self, client, test_document):
        resp = await client.post(
            "/api/notes",
            json={
                "document_id": test_document.id,
                "page_number": 1,
                "quote": "hello",
            },
        )
        assert resp.status_code == 401


class TestListAndGet:
    async def test_list_page_order(self, client, auth_header, test_document):
        await _create(client, auth_header, test_document.id, page_number=3, quote="page 3")
        await _create(client, auth_header, test_document.id, page_number=1, quote="page 1")
        await _create(client, auth_header, test_document.id, page_number=2, quote="page 2")
        resp = await client.get(
            f"/api/notes?document_id={test_document.id}", headers=auth_header
        )
        assert resp.status_code == 200
        pages = [n["page_number"] for n in resp.json()]
        assert pages == [1, 2, 3]

    async def test_list_other_users_document(self, client, auth_header, second_document):
        resp = await client.get(
            f"/api/notes?document_id={second_document.id}", headers=auth_header
        )
        assert resp.status_code == 404

    async def test_get_own_note(self, client, auth_header, test_document):
        created = (await _create(client, auth_header, test_document.id)).json()
        resp = await client.get(f"/api/notes/{created['id']}", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    async def test_get_missing(self, client, auth_header):
        resp = await client.get("/api/notes/99999", headers=auth_header)
        assert resp.status_code == 404


class TestUpdateAndDelete:
    async def test_update_body_and_color(self, client, auth_header, test_document):
        created = (await _create(client, auth_header, test_document.id)).json()
        resp = await client.patch(
            f"/api/notes/{created['id']}",
            headers=auth_header,
            json={"note": "Rewritten thought.", "color": "indigo"},
        )
        assert resp.status_code == 200
        assert resp.json()["note"] == "Rewritten thought."
        assert resp.json()["color"] == "indigo"

    async def test_clear_body_keeps_highlight(self, client, auth_header, test_document):
        created = (
            await _create(client, auth_header, test_document.id, note="temp")
        ).json()
        resp = await client.patch(
            f"/api/notes/{created['id']}",
            headers=auth_header,
            json={"note": "  "},
        )
        assert resp.status_code == 200
        assert resp.json()["note"] is None
        assert resp.json()["quote"]

    async def test_cannot_empty_page_note(self, client, auth_header, test_document):
        created = (
            await _create(
                client,
                auth_header,
                test_document.id,
                quote=None,
                note="page thought",
            )
        ).json()
        resp = await client.patch(
            f"/api/notes/{created['id']}",
            headers=auth_header,
            json={"note": ""},
        )
        assert resp.status_code == 400

    async def test_update_unknown_color(self, client, auth_header, test_document):
        created = (await _create(client, auth_header, test_document.id)).json()
        resp = await client.patch(
            f"/api/notes/{created['id']}",
            headers=auth_header,
            json={"color": "nope"},
        )
        assert resp.status_code == 400

    async def test_delete(self, client, auth_header, test_document):
        created = (await _create(client, auth_header, test_document.id)).json()
        resp = await client.delete(f"/api/notes/{created['id']}", headers=auth_header)
        assert resp.status_code == 200
        listed = await client.get(
            f"/api/notes?document_id={test_document.id}", headers=auth_header
        )
        assert listed.json() == []

    async def test_cannot_delete_other_users_note(
        self, client, auth_header, second_user, second_document
    ):
        from datetime import datetime, timedelta, timezone

        from jose import jwt

        other_token = jwt.encode(
            {
                "sub": str(second_user.id),
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
                "type": "access",
            },
            "test_secret_32_chars_long_for_testing!",
            algorithm="HS256",
        )
        created = (
            await client.post(
                "/api/notes",
                headers={"Authorization": f"Bearer {other_token}"},
                json={
                    "document_id": second_document.id,
                    "page_number": 1,
                    "quote": "This belongs to another user entirely.",
                },
            )
        ).json()
        resp = await client.delete(f"/api/notes/{created['id']}", headers=auth_header)
        assert resp.status_code == 404


class TestColors:
    async def test_defaults(self, client, auth_header):
        resp = await client.get("/api/notes/colors", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        ids = [c["id"] for c in data]
        assert ids[:3] == ["amber", "green", "indigo"]
        by_id = {c["id"]: c for c in data}
        assert by_id["amber"]["name"] == "amber"
        assert by_id["amber"]["hint"] == "claim"
        assert by_id["green"]["hint"] == "definition"
        assert by_id["indigo"]["hint"] == "question"
        assert all(c["built_in"] for c in data[:3])

    async def test_add_custom_and_use(self, client, auth_header, test_document):
        created = await client.post(
            "/api/notes/colors",
            headers=auth_header,
            json={"name": "counter", "hex": "#fb7185"},
        )
        assert created.status_code == 200
        color = created.json()
        assert color["name"] == "counter"
        assert color["hex"] == "#fb7185"
        assert color["built_in"] is False
        assert color["id"]

        note = await _create(
            client, auth_header, test_document.id, color=color["id"]
        )
        assert note.status_code == 200
        assert note.json()["color"] == color["id"]

    async def test_reserved_name(self, client, auth_header):
        resp = await client.post(
            "/api/notes/colors",
            headers=auth_header,
            json={"name": "amber", "hex": "#ffffff"},
        )
        assert resp.status_code == 400

    async def test_invalid_hex(self, client, auth_header):
        resp = await client.post(
            "/api/notes/colors",
            headers=auth_header,
            json={"name": "rose", "hex": "#zzzzzz"},
        )
        assert resp.status_code == 400

    async def test_duplicate_name(self, client, auth_header):
        await client.post(
            "/api/notes/colors",
            headers=auth_header,
            json={"name": "rose", "hex": "#fb7185"},
        )
        resp = await client.post(
            "/api/notes/colors",
            headers=auth_header,
            json={"name": "Rose", "hex": "#fda4af"},
        )
        assert resp.status_code == 400

    async def test_cannot_delete_builtin(self, client, auth_header):
        resp = await client.delete("/api/notes/colors/amber", headers=auth_header)
        assert resp.status_code == 400

    async def test_cannot_rename_builtin(self, client, auth_header):
        resp = await client.patch(
            "/api/notes/colors/amber",
            headers=auth_header,
            json={"name": "claim"},
        )
        assert resp.status_code == 400

    async def test_rename_and_recolor_custom(self, client, auth_header):
        created = (
            await client.post(
                "/api/notes/colors",
                headers=auth_header,
                json={"name": "rose", "hex": "#fb7185"},
            )
        ).json()
        resp = await client.patch(
            f"/api/notes/colors/{created['id']}",
            headers=auth_header,
            json={"name": "objection", "hex": "#f43f5e"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "objection"
        assert resp.json()["hex"] == "#f43f5e"

    async def test_cannot_delete_color_in_use(self, client, auth_header, test_document):
        color = (
            await client.post(
                "/api/notes/colors",
                headers=auth_header,
                json={"name": "rose", "hex": "#fb7185"},
            )
        ).json()
        await _create(client, auth_header, test_document.id, color=color["id"])
        resp = await client.delete(
            f"/api/notes/colors/{color['id']}", headers=auth_header
        )
        assert resp.status_code == 400

    async def test_delete_unused_custom(self, client, auth_header):
        color = (
            await client.post(
                "/api/notes/colors",
                headers=auth_header,
                json={"name": "rose", "hex": "#fb7185"},
            )
        ).json()
        resp = await client.delete(
            f"/api/notes/colors/{color['id']}", headers=auth_header
        )
        assert resp.status_code == 200
        listed = await client.get("/api/notes/colors", headers=auth_header)
        assert all(c["id"] != color["id"] for c in listed.json())

    async def test_colors_unauthenticated(self, client):
        resp = await client.get("/api/notes/colors")
        assert resp.status_code == 401


class TestTagsSearchExport:
    async def test_tags_roundtrip(self, client, auth_header, test_document):
        created = (
            await _create(
                client,
                auth_header,
                test_document.id,
                tags=["claim", "ch1"],
            )
        ).json()
        assert created["tags"] == ["claim", "ch1"]
        updated = (
            await client.patch(
                f"/api/notes/{created['id']}",
                headers=auth_header,
                json={"tags": ["method"]},
            )
        ).json()
        assert updated["tags"] == ["method"]
        listed = await client.get(
            f"/api/notes?document_id={test_document.id}&tag=method",
            headers=auth_header,
        )
        assert [n["id"] for n in listed.json()] == [created["id"]]

    async def test_search(self, client, auth_header, test_document):
        await _create(
            client,
            auth_header,
            test_document.id,
            quote="entirely on attention mechanisms",
        )
        await _create(
            client,
            auth_header,
            test_document.id,
            quote="something else entirely",
            note="scale numbers",
        )
        hits = await client.get(
            f"/api/notes?document_id={test_document.id}&q=attention",
            headers=auth_header,
        )
        assert hits.status_code == 200
        assert len(hits.json()) == 1
        across = await client.get("/api/notes?q=attention", headers=auth_header)
        assert across.status_code == 200
        assert any("attention" in (n["quote"] or "").lower() for n in across.json())

    async def test_export_markdown(self, client, auth_header, test_document):
        await _create(
            client,
            auth_header,
            test_document.id,
            quote="The quick brown fox jumps over the lazy dog.",
            note="A claim to remember.",
        )
        resp = await client.get(
            f"/api/notes/export?document_id={test_document.id}",
            headers=auth_header,
        )
        assert resp.status_code == 200
        text = resp.text
        assert "A claim to remember." in text
        assert "The quick brown fox" in text
        assert "text/markdown" in resp.headers.get("content-type", "")


class TestFlashcardFromNote:
    async def test_creates_card(self, client, auth_header, test_document):
        from unittest.mock import patch

        created = (
            await _create(
                client,
                auth_header,
                test_document.id,
                quote="The quick brown fox jumps over the lazy dog.",
                note="Key claim.",
            )
        ).json()
        async def _fake(*_a, **_k):
            return '{"question": "What jumps?", "answer": "The fox."}'

        with patch(
            "app.services.note_flashcard._generate",
            side_effect=_fake,
        ):
            resp = await client.post(
                f"/api/notes/{created['id']}/flashcard",
                headers=auth_header,
            )
        assert resp.status_code == 200
        card = resp.json()
        assert card["question"] == "What jumps?"
        assert card["source"] == "note"
        assert card["source_note_id"] == created["id"]
        listed = await client.get(
            f"/api/notes?document_id={test_document.id}",
            headers=auth_header,
        )
        assert listed.json()[0]["flashcard_id"] == card["id"]

    async def test_idempotent(self, client, auth_header, test_document):
        from unittest.mock import patch

        created = (await _create(client, auth_header, test_document.id)).json()
        async def _fake(*_a, **_k):
            return '{"question": "Q", "answer": "A"}'

        with patch(
            "app.services.note_flashcard._generate",
            side_effect=_fake,
        ):
            first = await client.post(
                f"/api/notes/{created['id']}/flashcard",
                headers=auth_header,
            )
            second = await client.post(
                f"/api/notes/{created['id']}/flashcard",
                headers=auth_header,
            )
        assert first.status_code == 200
        assert second.json()["id"] == first.json()["id"]


async def test_color_hint_support_and_builtin_override(client, auth_header):
    # 1. Create with hint
    created = await client.post(
        "/api/notes/colors",
        headers=auth_header,
        json={"name": "purple", "hex": "#a78bfa", "hint": "facts"},
    )
    assert created.status_code == 200
    color = created.json()
    assert color["name"] == "purple"
    assert color["hex"] == "#a78bfa"
    assert color["hint"] == "facts"

    # 2. Update custom hint and hex
    updated = await client.patch(
        f"/api/notes/colors/{color['id']}",
        headers=auth_header,
        json={"hint": "evidence", "hex": "#8b5cf6"},
    )
    assert updated.status_code == 200
    assert updated.json()["hint"] == "evidence"
    assert updated.json()["hex"] == "#8b5cf6"

    # 3. Update built-in color hint
    builtin_updated = await client.patch(
        "/api/notes/colors/amber",
        headers=auth_header,
        json={"hint": "key finding"},
    )
    assert builtin_updated.status_code == 200
    assert builtin_updated.json()["id"] == "amber"
    assert builtin_updated.json()["name"] == "amber"
    assert builtin_updated.json()["hint"] == "key finding"

    # 4. Built-in name cannot be changed
    conflict = await client.patch(
        "/api/notes/colors/amber",
        headers=auth_header,
        json={"name": "yellow"},
    )
    assert conflict.status_code == 400

    # 5. List palette reflects both custom hint and builtin override
    palette = (await client.get("/api/notes/colors", headers=auth_header)).json()
    amber = next(c for c in palette if c["id"] == "amber")
    assert amber["hint"] == "key finding"
    custom_purple = next(c for c in palette if c["id"] == color["id"])
    assert custom_purple["hint"] == "evidence"
