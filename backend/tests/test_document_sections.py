"""Chapter backfill preserves generated content and member privacy."""

from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.models import LibraryEntry, Summary
from app.services.auth_service import create_access_token
from app.services.document_sections import persist_section_stubs
from app.tasks import summary_tasks


def test_persistence_is_idempotent_and_repairs_only_metadata(db, test_document, test_user):
    generated = Summary(
        document_id=test_document.id,
        user_id=test_user.id,
        mode="section",
        section_title="Introduction",
        content="Already generated",
        start_page=1,
        end_page=2,
        level=None,
    )
    db.add(generated)
    db.commit()
    metadata = [
        {"title": "Introduction", "start_page": 1, "end_page": 3, "level": 1},
        {"title": "Methods", "start_page": 3, "end_page": 3, "level": 0},
        {"title": "Methods", "start_page": 3, "end_page": 3, "level": 0},
    ]

    assert persist_section_stubs(db, test_document.id, metadata, test_user.id) == 1
    assert persist_section_stubs(db, test_document.id, metadata, test_user.id) == 0
    db.refresh(generated)
    assert generated.content == "Already generated"
    assert generated.end_page == 2
    assert generated.level == 1
    assert db.query(Summary).filter_by(document_id=test_document.id).count() == 2


async def test_reader_backfills_chapters_without_replacing_generated_content(
    client, db, auth_header, test_document, test_user,
):
    generated = Summary(
        document_id=test_document.id,
        user_id=test_user.id,
        mode="section",
        section_title="Introduction",
        content="Keep this explanation",
        start_page=1,
        end_page=1,
        level=None,
    )
    obsolete = Summary(
        document_id=test_document.id,
        user_id=test_user.id,
        mode="section",
        section_title="Full Document",
        content="",
        level=None,
    )
    db.add_all([generated, obsolete])
    db.commit()
    metadata = [
        {"title": "Introduction", "start_page": 1, "end_page": 1, "level": 0},
        {"title": "Methods", "start_page": 2, "end_page": 3, "level": 1},
    ]
    with patch(
        "app.services.document_sections.summarizer.detect_sections_metadata",
        return_value=metadata,
    ) as detect:
        response = await client.get(
            f"/api/documents/{test_document.id}/summaries?mode=section", headers=auth_header,
        )
        assert response.status_code == 200
        rows = {row["section_title"]: row for row in response.json()}
        assert set(rows) == {"Introduction", "Methods"}
        assert rows["Introduction"]["content"] == "Keep this explanation"
        assert rows["Introduction"]["level"] == 0
        assert rows["Methods"]["content"] == ""
        repeated = await client.get(
            f"/api/documents/{test_document.id}/summaries?mode=section", headers=auth_header,
        )
        assert repeated.json() == response.json()
        detect.assert_called_once()


async def test_member_gets_chapter_metadata_without_uploader_content(
    client, db, test_document, test_user, second_user,
):
    db.add(LibraryEntry(user_id=second_user.id, document_id=test_document.id))
    db.add(Summary(
        document_id=test_document.id,
        user_id=test_user.id,
        mode="section",
        section_title="Introduction",
        content="Uploader private teaching notes",
        start_page=1,
        end_page=3,
        level=0,
    ))
    db.commit()
    token = create_access_token({"sub": str(second_user.id)})
    headers = {"Authorization": f"Bearer {token}"}
    with patch("app.services.document_sections.summarizer.detect_sections_metadata") as detect:
        response = await client.get(
            f"/api/documents/{test_document.id}/summaries?mode=section", headers=headers,
        )
        assert response.status_code == 200
        assert len(response.json()) == 1
        chapter = response.json()[0]
        assert chapter["section_title"] == "Introduction"
        assert chapter["user_id"] == second_user.id
        assert chapter["content"] == ""
        detect.assert_not_called()
    original = db.query(Summary).filter_by(user_id=test_user.id).one()
    assert original.content == "Uploader private teaching notes"


async def test_unextracted_reader_does_not_discover_chapters(
    client, db, auth_header, test_document,
):
    test_document.processing_status = "pending"
    db.commit()
    with patch("app.services.document_sections.summarizer.detect_sections_metadata") as detect:
        response = await client.get(
            f"/api/documents/{test_document.id}/summaries?mode=section", headers=auth_header,
        )
        assert response.status_code == 200
        assert response.json() == []
        detect.assert_not_called()


def test_background_discovery_persists_private_chapters_on_replay(
    db, test_document, test_user,
):
    task_session_factory = sessionmaker(bind=db.connection())
    metadata = [
        {"title": "Introduction", "start_page": 1, "end_page": 1, "level": 0},
        {"title": "Methods", "start_page": 2, "end_page": 3, "level": 1},
    ]
    with patch.object(summary_tasks, "SessionLocal", task_session_factory), patch.object(
        summary_tasks.summarizer, "detect_sections_metadata", return_value=metadata,
    ):
        for attempt in range(2):
            result = summary_tasks.generate_section_summaries.run(test_document.id, test_user.id)
            assert result == {"doc_id": test_document.id, "section_count": 2}
    rows = db.query(Summary).filter_by(document_id=test_document.id).all()
    assert len(rows) == 2
    assert all(row.user_id == test_user.id and row.content == "" for row in rows)
    db.refresh(test_document)
    assert test_document.processing_status == "completed"


async def test_on_demand_generation_backfills_and_queues_only_the_requested_chapter(
    client, db, auth_header, test_document, test_user,
):
    metadata = [
        {"title": "Introduction", "start_page": 1, "end_page": 1, "level": 0},
        {"title": "Methods", "start_page": 2, "end_page": 3, "level": 1},
    ]
    with patch(
        "app.services.document_sections.summarizer.detect_sections_metadata",
        return_value=metadata,
    ), patch.object(summary_tasks.generate_single_section_summary, "delay") as dispatch:
        response = await client.post(
            f"/api/documents/{test_document.id}/summaries/section",
            headers=auth_header,
            json={"section_title": "Methods"},
        )
        assert response.status_code == 200
        assert response.json()["section_title"] == "Methods"
        assert response.json()["status"] == "generating"
        assert response.json()["user_id"] == test_user.id
        repeated = await client.post(
            f"/api/documents/{test_document.id}/summaries/section",
            headers=auth_header,
            json={"section_title": "Methods"},
        )
        assert repeated.status_code == 200
        dispatch.assert_called_once_with(test_document.id, "Methods", test_user.id)
    introduction = db.query(Summary).filter_by(
        document_id=test_document.id, user_id=test_user.id, section_title="Introduction",
    ).one()
    assert introduction.status != "generating"
