import pytest
from unittest.mock import patch

from app.config import settings
from app.models import Document, LibraryEntry, Podcast, TTSCache
from app.services.auth_service import create_access_token
from app.tasks.podcast_tasks import generate_podcast


@pytest.fixture(autouse=True)
def configure_podcast_storage(tmp_path, monkeypatch):
    podcast_storage_dir = tmp_path / "podcasts"
    podcast_storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return podcast_storage_dir


def _header(user_identifier: int) -> dict[str, str]:
    token = create_access_token({"sub": str(user_identifier)})
    return {"Authorization": f"Bearer {token}"}


def _create_doc(db_session, user_identifier: int, title_text: str, **kwargs) -> Document:
    document = Document(
        user_id=user_identifier,
        title=title_text,
        format="pdf",
        file_path="/tmp/fake_book.pdf",
        file_size=1024,
        processing_status="completed",
        **kwargs,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def _create_podcast(db_session, user_identifier: int, document_identifier: int, **kwargs) -> Podcast:
    podcast_entry = Podcast(
        user_id=user_identifier,
        document_id=document_identifier,
        scope=kwargs.pop("scope", "full"),
        status=kwargs.pop("status", "completed"),
        length=kwargs.pop("length", "medium"),
        duration=kwargs.pop("duration", 180.0),
        **kwargs,
    )
    db_session.add(podcast_entry)
    db_session.commit()
    db_session.refresh(podcast_entry)
    return podcast_entry


async def test_delete_podcast_success(client, db, test_user, test_document, auth_header, configure_podcast_storage):
    audio_file = configure_podcast_storage / "podcast_audio_test.wav"
    audio_file.write_bytes(b"RIFF dummy audio data")
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        audio_path=str(audio_file),
    )

    response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
    assert response.status_code == 200
    assert response.json() == {"detail": "Deleted"}

    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None
    assert not audio_file.exists()


async def test_delete_podcast_unauthenticated(client, db, test_user, test_document):
    podcast = _create_podcast(db, test_user.id, test_document.id)
    response = await client.delete(f"/api/podcasts/{podcast.id}")
    assert response.status_code == 401


async def test_delete_podcast_not_found(client, auth_header):
    response = await client.delete("/api/podcasts/999999", headers=auth_header)
    assert response.status_code == 404


async def test_delete_podcast_forbidden_other_user(client, db, test_user, second_user, configure_podcast_storage):
    document = _create_doc(db, test_user.id, "Owner Document")
    audio_file = configure_podcast_storage / "owner_audio.wav"
    audio_file.write_bytes(b"RIFF owner audio")
    podcast = _create_podcast(
        db,
        test_user.id,
        document.id,
        audio_path=str(audio_file),
    )

    response = await client.delete(
        f"/api/podcasts/{podcast.id}",
        headers=_header(second_user.id),
    )
    assert response.status_code == 404

    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None or podcast.user_id == test_user.id
    assert audio_file.exists()


async def test_delete_podcast_membership_revocation(client, db, test_user, auth_header):
    document = _create_doc(db, test_user.id, "Revoked Membership Book")
    podcast = _create_podcast(db, test_user.id, document.id)

    db.query(LibraryEntry).filter(
        LibraryEntry.user_id == test_user.id,
        LibraryEntry.document_id == document.id,
    ).delete()
    db.commit()

    response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
    assert response.status_code == 404
    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is not None


async def test_delete_podcast_preserves_other_user_podcast_and_audio(
    client, db, test_user, second_user, configure_podcast_storage
):
    document_first = _create_doc(db, test_user.id, "User One Book")
    document_second = _create_doc(db, second_user.id, "User Two Book")
    audio_first = configure_podcast_storage / "first_audio.wav"
    audio_second = configure_podcast_storage / "second_audio.wav"
    audio_first.write_bytes(b"RIFF first user audio")
    audio_second.write_bytes(b"RIFF second user audio")

    podcast_first = _create_podcast(
        db,
        test_user.id,
        document_first.id,
        audio_path=str(audio_first),
    )
    podcast_second = _create_podcast(
        db,
        second_user.id,
        document_second.id,
        audio_path=str(audio_second),
    )

    response = await client.delete(
        f"/api/podcasts/{podcast_first.id}",
        headers=_header(test_user.id),
    )
    assert response.status_code == 200

    assert db.query(Podcast).filter(Podcast.id == podcast_first.id).first() is None
    assert not audio_first.exists()

    assert db.query(Podcast).filter(Podcast.id == podcast_second.id).first() is not None
    assert audio_second.exists()


async def test_delete_podcast_preserves_source_book_and_reading_cache(
    client, db, test_user, auth_header, tmp_path, configure_podcast_storage
):
    book_file = tmp_path / "actual_book.pdf"
    book_file.write_bytes(b"%PDF- real book content")
    document = Document(
        user_id=test_user.id,
        title="Preserved Source Book",
        format="pdf",
        file_path=str(book_file),
        file_size=book_file.stat().st_size,
        processing_status="completed",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    reading_audio = tmp_path / "reading_cache.wav"
    reading_audio.write_bytes(b"RIFF reading cache audio")
    cache_entry = TTSCache(
        user_id=test_user.id,
        document_id=document.id,
        text_hash="hash_for_page_reading",
        audio_path=str(reading_audio),
    )
    db.add(cache_entry)
    db.commit()
    db.refresh(cache_entry)

    podcast_audio = configure_podcast_storage / "episode_to_delete.wav"
    podcast_audio.write_bytes(b"RIFF podcast episode audio")
    podcast = _create_podcast(
        db,
        test_user.id,
        document.id,
        audio_path=str(podcast_audio),
    )

    response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
    assert response.status_code == 200

    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None
    assert not podcast_audio.exists()

    preserved_document = db.query(Document).filter(Document.id == document.id).first()
    assert preserved_document is not None
    assert book_file.exists()

    preserved_cache = db.query(TTSCache).filter(TTSCache.id == cache_entry.id).first()
    assert preserved_cache is not None
    assert reading_audio.exists()


async def test_delete_podcast_missing_audio_file(client, db, test_user, test_document, auth_header, configure_podcast_storage):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        audio_path=str(configure_podcast_storage / "non_existent_podcast_file.wav"),
    )

    response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
    assert response.status_code == 200
    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None


async def test_delete_podcast_null_audio_path(client, db, test_user, test_document, auth_header):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="failed",
        audio_path=None,
    )

    response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
    assert response.status_code == 200
    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None


async def test_delete_generating_podcast_with_potential_worker_artifact(
    client, db, test_user, test_document, auth_header, configure_podcast_storage
):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
        audio_path=None,
    )
    worker_file = configure_podcast_storage / f"{podcast.id}.wav"
    worker_file.write_bytes(b"RIFF in-flight worker audio")

    response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
    assert response.status_code == 200
    assert not worker_file.exists()
    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None


def test_late_producer_worker_completion_after_deletion_leaves_no_orphan_wav(
    db, test_user, test_document, configure_podcast_storage
):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
        audio_path=None,
    )
    podcast_identifier = podcast.id
    db.delete(podcast)
    db.commit()

    worker_file = configure_podcast_storage / f"{podcast_identifier}.wav"
    from app.tasks import podcast_tasks
    from sqlalchemy.orm import Session as SqlAlchemySession

    def create_worker_session():
        return SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )

    with patch.object(podcast_tasks, "SessionLocal", side_effect=create_worker_session):
        task_result = generate_podcast(podcast_identifier)
        assert task_result["status"] == "failed"
        assert not worker_file.exists()


async def test_delete_podcast_refuses_to_unlink_outside_podcast_directory(
    client, db, test_user, test_document, auth_header, tmp_path
):
    external_file = tmp_path / "outside_directory_file.wav"
    external_file.write_bytes(b"RIFF protected external audio")

    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        audio_path=str(external_file),
    )

    response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
    assert response.status_code == 200
    assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None
    assert external_file.exists()


async def test_delete_podcast_db_failure_preserves_file(
    client, db, test_user, test_document, auth_header, configure_podcast_storage
):
    audio_file = configure_podcast_storage / "preserved_on_db_error.wav"
    audio_file.write_bytes(b"RIFF audio that must survive rollback")
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        audio_path=str(audio_file),
    )

    with patch("app.routers.podcasts.Session.commit", side_effect=RuntimeError("Database failure")):
        try:
            await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
        except RuntimeError:
            pass

    assert audio_file.exists()


async def test_delete_podcast_handles_unlink_os_error_gracefully(
    client, db, test_user, test_document, auth_header, configure_podcast_storage
):
    audio_file = configure_podcast_storage / "audio_causing_unlink_error.wav"
    audio_file.write_bytes(b"RIFF audio")
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        audio_path=str(audio_file),
    )

    with patch("os.remove", side_effect=OSError("Permission denied")):
        response = await client.delete(f"/api/podcasts/{podcast.id}", headers=auth_header)
        assert response.status_code == 200
        assert db.query(Podcast).filter(Podcast.id == podcast.id).first() is None


def test_interleaved_in_flight_synthesis_and_deletion_leaves_no_orphan_wav(
    db, test_user, test_document, configure_podcast_storage
):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
        audio_path=None,
    )
    podcast_identifier = podcast.id
    worker_file = configure_podcast_storage / f"{podcast_identifier}.wav"

    import io
    import wave
    from app.tasks import podcast_tasks
    from sqlalchemy.orm import Session as SqlAlchemySession

    def make_wav_bytes():
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00" * 400)
        return buf.getvalue()

    def create_worker_session():
        return SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )

    def perform_deletion_during_synthesis(text, voice):
        del_session = SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )
        target_row = (
            del_session.query(Podcast)
            .filter(Podcast.id == podcast_identifier)
            .with_for_update()
            .first()
        )
        if target_row:
            del_session.delete(target_row)
            del_session.commit()
        del_session.close()
        return make_wav_bytes()

    with (
        patch.object(podcast_tasks, "SessionLocal", side_effect=create_worker_session),
        patch.object(podcast_tasks, "_gather_content", return_value="Sample test content"),
        patch.object(
            podcast_tasks,
            "_generate_dialogue_segments",
            return_value=("Interleaved Episode", None, [{"speaker": "A", "text": "Hello world"}]),
        ),
        patch.object(podcast_tasks, "_synthesize_line", side_effect=perform_deletion_during_synthesis),
    ):
        task_result = podcast_tasks.generate_podcast(podcast_identifier)
        assert task_result["status"] == "not_accessible"
        assert not worker_file.exists()
        assert db.query(Podcast).filter(Podcast.id == podcast_identifier).first() is None


def test_task_commit_failure_cleans_up_partial_audio_file(
    db, test_user, test_document, configure_podcast_storage
):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
        audio_path=None,
    )
    podcast_identifier = podcast.id
    worker_file = configure_podcast_storage / f"{podcast_identifier}.wav"

    def fake_stitch_wavs(clip_list, destination_path):
        with open(destination_path, "wb") as file_handle:
            file_handle.write(b"RIFF partial stitched audio")
        return 42.0

    from app.tasks import podcast_tasks
    from sqlalchemy.orm import Session as SqlAlchemySession

    def create_failing_worker_session():
        failing_session = SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )
        def failing_commit():
            raise RuntimeError("Database commit failure")
        failing_session.commit = failing_commit
        return failing_session

    with (
        patch.object(podcast_tasks, "SessionLocal", side_effect=create_failing_worker_session),
        patch.object(podcast_tasks, "_gather_content", return_value="Sample test content"),
        patch.object(
            podcast_tasks,
            "_generate_dialogue_segments",
            return_value=("Commit Failure Episode", None, [{"speaker": "A", "text": "Testing commit fail"}]),
        ),
        patch.object(podcast_tasks, "_synthesize_line", return_value=b"RIFF clip"),
        patch.object(podcast_tasks, "_stitch_wavs", side_effect=fake_stitch_wavs),
    ):
        task_result = podcast_tasks.generate_podcast(podcast_identifier)
        assert task_result["status"] == "failed"
        assert not worker_file.exists()


def test_duplicate_task_on_completed_episode_preserves_existing_audio(
    db, test_user, test_document, configure_podcast_storage
):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
        audio_path=None,
    )
    podcast_identifier = podcast.id
    existing_audio_file = configure_podcast_storage / f"{podcast_identifier}.wav"
    existing_audio_file.write_bytes(b"RIFF completed original audio")

    import io
    import wave
    from app.tasks import podcast_tasks
    from sqlalchemy.orm import Session as SqlAlchemySession

    def make_wav_bytes():
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00" * 400)
        return buf.getvalue()

    def create_worker_session():
        return SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )

    def simulate_concurrent_completion(text, voice):
        comp_session = SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )
        target_row = (
            comp_session.query(Podcast)
            .filter(Podcast.id == podcast_identifier)
            .with_for_update()
            .first()
        )
        if target_row:
            target_row.status = "completed"
            target_row.audio_path = str(existing_audio_file)
            target_row.duration = 180.0
            comp_session.commit()
        comp_session.close()
        return make_wav_bytes()

    with (
        patch.object(podcast_tasks, "SessionLocal", side_effect=create_worker_session),
        patch.object(podcast_tasks, "_gather_content", return_value="Sample test content"),
        patch.object(
            podcast_tasks,
            "_generate_dialogue_segments",
            return_value=("Duplicate Task Episode", None, [{"speaker": "A", "text": "Duplicate run"}]),
        ),
        patch.object(podcast_tasks, "_synthesize_line", side_effect=simulate_concurrent_completion),
    ):
        task_result = podcast_tasks.generate_podcast(podcast_identifier)
        assert task_result["status"] == "completed"
        assert task_result["duration"] == 180.0
        assert existing_audio_file.exists()
        assert existing_audio_file.read_bytes() == b"RIFF completed original audio"


def test_initial_fetch_completed_duplicate_task_early_returns_without_processing(
    db, test_user, test_document, configure_podcast_storage
):
    completed_file = configure_podcast_storage / "already_done.wav"
    completed_file.write_bytes(b"RIFF pre-existing audio")
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="completed",
        audio_path=str(completed_file),
        duration=240.0,
    )
    podcast_identifier = podcast.id

    from app.tasks import podcast_tasks
    from sqlalchemy.orm import Session as SqlAlchemySession

    def create_worker_session():
        return SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )

    with (
        patch.object(podcast_tasks, "SessionLocal", side_effect=create_worker_session),
        patch.object(
            podcast_tasks,
            "_gather_content",
            side_effect=AssertionError("Should not gather content for completed episode"),
        ),
    ):
        task_result = podcast_tasks.generate_podcast(podcast_identifier)
        assert task_result["status"] == "completed"
        assert task_result["duration"] == 240.0
        assert completed_file.exists()


def test_partial_stitch_failure_cleans_up_partial_audio_file(
    db, test_user, test_document, configure_podcast_storage
):
    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
        audio_path=None,
    )
    podcast_identifier = podcast.id
    worker_file = configure_podcast_storage / f"{podcast_identifier}.wav"

    def failing_stitch(clip_list, destination_path):
        with open(destination_path, "wb") as file_handle:
            file_handle.write(b"RIFF partial incomplete data")
        raise RuntimeError("Stitch interrupted by disk error")

    from app.tasks import podcast_tasks
    from sqlalchemy.orm import Session as SqlAlchemySession

    def create_worker_session():
        return SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )

    with (
        patch.object(podcast_tasks, "SessionLocal", side_effect=create_worker_session),
        patch.object(podcast_tasks, "_gather_content", return_value="Sample test content"),
        patch.object(
            podcast_tasks,
            "_generate_dialogue_segments",
            return_value=("Stitch Fail Episode", None, [{"speaker": "A", "text": "Fail stitch"}]),
        ),
        patch.object(podcast_tasks, "_synthesize_line", return_value=b"RIFF clip"),
        patch.object(podcast_tasks, "_stitch_wavs", side_effect=failing_stitch),
    ):
        task_result = podcast_tasks.generate_podcast(podcast_identifier)
        assert task_result["status"] == "failed"
        assert not worker_file.exists()

        refreshed_podcast = (
            create_worker_session()
            .query(Podcast)
            .filter(Podcast.id == podcast_identifier)
            .first()
        )
        assert refreshed_podcast is not None
        assert refreshed_podcast.status == "failed"


def test_task_commit_failure_preserves_unrelated_completed_audio(
    db, test_user, test_document, configure_podcast_storage
):
    unrelated_file = configure_podcast_storage / "unrelated_completed.wav"
    unrelated_file.write_bytes(b"RIFF unrelated audio")
    _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="completed",
        audio_path=str(unrelated_file),
    )

    podcast = _create_podcast(
        db,
        test_user.id,
        test_document.id,
        status="generating",
        audio_path=None,
    )
    podcast_identifier = podcast.id
    worker_file = configure_podcast_storage / f"{podcast_identifier}.wav"

    from app.tasks import podcast_tasks
    from sqlalchemy.orm import Session as SqlAlchemySession

    def create_worker_session():
        return SqlAlchemySession(
            bind=db.connection(),
            join_transaction_mode="create_savepoint",
        )

    active_worker_session = create_worker_session()
    real_commit = active_worker_session.commit

    def fake_stitch_wavs(clip_list, destination_path):
        with open(destination_path, "wb") as file_handle:
            file_handle.write(b"RIFF partial stitched audio")
        commit_call_count = 0
        def fail_publication_commit_only():
            nonlocal commit_call_count
            commit_call_count += 1
            if commit_call_count == 1:
                raise RuntimeError("Database commit conflict")
            return real_commit()
        active_worker_session.commit = fail_publication_commit_only
        return 42.0

    with (
        patch.object(podcast_tasks, "SessionLocal", return_value=active_worker_session),
        patch.object(podcast_tasks, "_gather_content", return_value="Sample test content"),
        patch.object(
            podcast_tasks,
            "_generate_dialogue_segments",
            return_value=("Commit Fail Episode", None, [{"speaker": "A", "text": "Testing commit fail"}]),
        ),
        patch.object(podcast_tasks, "_synthesize_line", return_value=b"RIFF clip"),
        patch.object(podcast_tasks, "_stitch_wavs", side_effect=fake_stitch_wavs),
    ):
        task_result = podcast_tasks.generate_podcast(podcast_identifier)
        assert task_result["status"] == "failed"
        assert not worker_file.exists()
        assert unrelated_file.exists()
        assert unrelated_file.read_bytes() == b"RIFF unrelated audio"

        final_podcast = (
            create_worker_session()
            .query(Podcast)
            .filter(Podcast.id == podcast_identifier)
            .first()
        )
        assert final_podcast is not None
        assert final_podcast.status == "failed"
