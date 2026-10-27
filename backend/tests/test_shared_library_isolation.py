"""Shared assets keep annotations, summaries, chat, and exports private."""
import json

from app.models import (
    Bookmark,
    Conversation,
    Flashcard,
    LibraryEntry,
    Message,
    Note,
    Podcast,
    Summary,
)
from app.services.auth_service import create_access_token
from app.services.export_service import export_user_data


def _header(user_id: int) -> dict[str, str]:
    token = create_access_token({"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}


def _add_membership(db, user_id: int, document_id: int) -> None:
    db.add(LibraryEntry(user_id=user_id, document_id=document_id, can_publish=False))
    db.commit()


async def test_shared_document_personal_data_stays_isolated(
    client, db, test_user, second_user, test_document, monkeypatch, tmp_path
):
    _add_membership(db, second_user.id, test_document.id)
    scoped_document = await client.get(
        f"/api/documents/{test_document.id}", headers=_header(second_user.id)
    )
    assert scoped_document.status_code == 200
    assert scoped_document.json()["title"] == f"Document {test_document.id}"

    owner_summary = Summary(
        user_id=test_user.id,
        document_id=test_document.id,
        mode="page",
        page_number=1,
        content="Owner-only teaching summary",
    )
    owner_note = Note(
        user_id=test_user.id,
        document_id=test_document.id,
        page_number=1,
        quote="Owner-only annotation",
        color="amber",
    )
    owner_bookmark = Bookmark(
        user_id=test_user.id,
        document_id=test_document.id,
        page_number=1,
        label="Owner bookmark",
    )
    owner_card = Flashcard(
        user_id=test_user.id,
        document_id=test_document.id,
        question="Owner question",
        answer="Owner answer",
        source="manual",
    )
    owner_podcast = Podcast(
        user_id=test_user.id,
        document_id=test_document.id,
        scope="document",
        status="completed",
        transcript={"lines": [{"text": "Owner transcript"}]},
    )
    owner_conversation = Conversation(
        user_id=test_user.id,
        title="Owner conversation",
        document_ids=[test_document.id],
    )
    db.add_all([
        owner_summary,
        owner_note,
        owner_bookmark,
        owner_card,
        owner_podcast,
        owner_conversation,
    ])
    db.commit()
    db.refresh(owner_summary)
    db.refresh(owner_note)
    db.refresh(owner_bookmark)
    db.refresh(owner_conversation)
    db.add(Message(
        conversation_id=owner_conversation.id,
        role="assistant",
        content="Owner chat answer",
    ))
    db.commit()

    second_header = _header(second_user.id)
    notes = await client.get(
        f"/api/notes?document_id={test_document.id}", headers=second_header
    )
    assert notes.status_code == 200
    assert notes.json() == []
    assert (await client.get(
        f"/api/notes/{owner_note.id}", headers=second_header
    )).status_code == 404

    bookmarks = await client.get(
        f"/api/bookmarks?document_id={test_document.id}", headers=second_header
    )
    assert bookmarks.status_code == 200
    assert bookmarks.json() == []

    cards = await client.get(
        f"/api/flashcards?document_id={test_document.id}", headers=second_header
    )
    assert cards.status_code == 200
    assert cards.json() == []
    podcasts = await client.get(
        f"/api/podcasts?document_id={test_document.id}", headers=second_header
    )
    assert podcasts.status_code == 200
    assert podcasts.json() == []

    sessions = await client.get("/api/chat/sessions", headers=second_header)
    assert sessions.status_code == 200
    assert all(item["id"] != owner_conversation.id for item in sessions.json())
    assert (await client.get(
        f"/api/chat/sessions/{owner_conversation.id}/messages", headers=second_header
    )).status_code == 404

    summaries = await client.get(
        f"/api/documents/{test_document.id}/summaries?mode=page",
        headers=second_header,
    )
    assert summaries.status_code == 200
    assert all(row["content"] != "Owner-only teaching summary" for row in summaries.json())

    owner_tts = await client.post(
        f"/api/tts/play/{owner_summary.id}", headers=second_header
    )
    assert owner_tts.status_code == 404

    own_card = Flashcard(
        user_id=second_user.id,
        document_id=test_document.id,
        question="Second question",
        answer="Second answer",
        source="manual",
    )
    own_podcast = Podcast(
        user_id=second_user.id,
        document_id=test_document.id,
        scope="document",
        status="completed",
        transcript={"lines": [{"text": "Second transcript"}]},
    )
    db.add_all([own_card, own_podcast])
    db.commit()

    monkeypatch.setattr("app.services.export_service.settings.export_dir", str(tmp_path))
    export = export_user_data(db, second_user)
    assert export["flashcard_files_written"] == 1
    assert export["podcasts_exported"] == 1
    # This imported membership has no publisher-visible title. Export uses a
    # stable generic asset label and must not reveal the uploader's filename.
    user_dir = tmp_path / second_user.username / f"document-{test_document.id}"
    card_data = json.loads((user_dir / "flashcards.json").read_text())
    podcast_data = json.loads(next(user_dir.glob("podcast-*.json")).read_text())
    assert [card["question"] for card in card_data] == ["Second question"]
    assert "Second transcript" in json.dumps(podcast_data)
    assert "Owner" not in json.dumps(card_data) + json.dumps(podcast_data)


async def test_deleting_uploader_keeps_shared_document_and_other_membership(
    client, db, admin_auth_header, test_user, second_user, test_document
):
    _add_membership(db, second_user.id, test_document.id)
    test_document.is_shared = True
    test_document.shared_by_user_id = test_user.id
    db.add_all([
        Note(
            user_id=test_user.id,
            document_id=test_document.id,
            page_number=1,
            note="Uploader private note",
            color="amber",
        ),
        Note(
            user_id=second_user.id,
            document_id=test_document.id,
            page_number=1,
            note="Other member note",
            color="amber",
        ),
        Summary(
            user_id=test_user.id,
            document_id=test_document.id,
            mode="page",
            page_number=1,
            content="Uploader private summary",
        ),
    ])
    db.commit()
    document_id = test_document.id

    deleted = await client.delete(
        f"/api/auth/users/{test_user.id}", headers=admin_auth_header
    )
    assert deleted.status_code == 200

    db.expire_all()
    retained = db.query(type(test_document)).filter_by(id=document_id).first()
    assert retained is not None
    assert retained.user_id is None
    assert retained.is_shared is False
    assert retained.shared_by_user_id is None
    assert db.query(LibraryEntry).filter_by(
        user_id=second_user.id,
        document_id=document_id,
    ).first() is not None
    assert db.query(Note).filter_by(user_id=test_user.id).count() == 0
    assert db.query(Note).filter_by(user_id=second_user.id).count() == 1
    assert db.query(Summary).filter_by(user_id=test_user.id).count() == 0
