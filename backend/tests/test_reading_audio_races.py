"""Concurrency and race regression tests for reading audio lifecycle."""
from typing import Optional
import os
import uuid
from datetime import datetime, timezone
import pytest
import redis
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TTSCache, User
from app.services.reading_audio import (
    register_producer,
    unregister_producer,
    is_producing,
    register_reader,
    unregister_reader,
    is_reading,
    claim_session_chunk,
    release_session_chunks,
    cleanup_aborted_or_abandoned_stream,
    is_chunk_protected,
    is_chunk_claimed_by_session,
    set_redis_client_for_testing,
)
from app.tasks.tts_tasks import evict_tts_cache
from tests.audio_test_helpers import FakeReadingAudioRedis


@pytest.fixture
def fake_redis_fixture():
    fake_client = FakeReadingAudioRedis()
    set_redis_client_for_testing(fake_client)
    yield fake_client
    set_redis_client_for_testing(None)


@pytest.fixture
def isolated_audio_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "tts-cache"
    podcast_dir = data_dir / "podcasts"
    cache_dir.mkdir(parents=True)
    podcast_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "tts_cache_max_gib", 1)
    monkeypatch.setattr(settings, "tts_cache_max_age_days", 7)
    return cache_dir, podcast_dir


def create_dummy_cache_row(
    db_session: Session,
    user_id: int,
    text_hash: str,
    wav_path: str,
    summary_id=None,
    cache_id: Optional[int] = None,
) -> TTSCache:
    now_time = datetime.now(timezone.utc)
    cache_row = TTSCache(
        id=cache_id,
        user_id=user_id,
        text_hash=text_hash,
        audio_path=wav_path,
        duration=3.5,
        size_bytes=len(b"RIFFdummydata" * 20),
        created_at=now_time,
        last_accessed=now_time,
        summary_id=summary_id,
    )
    db_session.add(cache_row)
    db_session.commit()
    db_session.refresh(cache_row)
    return cache_row


def test_two_producers_null_stub_cancel_one_preserves_other(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    tmp_file_alpha = cache_dir / ".hash_race.alpha.tmp"
    tmp_file_alpha.write_bytes(b"partial temp alpha")

    text_hash = "hash_race_concurrent"
    stub_row = TTSCache(
        user_id=test_user.id,
        text_hash=text_hash,
        audio_path=None,
        duration=None,
        size_bytes=None,
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )
    db.add(stub_row)
    db.commit()
    db.refresh(stub_row)

    session_alpha = "producer-session-alpha"
    session_beta = "producer-session-beta"

    register_producer(test_user.id, text_hash)
    register_producer(test_user.id, text_hash)
    claim_session_chunk(test_user.id, session_alpha, stub_row.id)
    claim_session_chunk(test_user.id, session_beta, stub_row.id)

    unregister_producer(test_user.id, text_hash)
    assert is_producing(test_user.id, text_hash) is True

    cleanup_aborted_or_abandoned_stream(
        db_session=db,
        user_id=test_user.id,
        text_hash=text_hash,
        cache_id=stub_row.id,
        tmp_path=str(tmp_file_alpha),
        session_id=session_alpha,
    )

    row_after_cancel = db.query(TTSCache).filter_by(id=stub_row.id).first()
    assert row_after_cancel is not None
    assert is_chunk_claimed_by_session(session_beta, stub_row.id) is True


def test_release_and_new_claim_interleave(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_interleave.wav"
    wav_file.write_bytes(b"interleaved claim and release audio")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_interleave", str(wav_file))
    session_first = "session-first-owner"
    session_second = "session-second-owner"

    claim_session_chunk(test_user.id, session_first, cache_row.id)
    claim_session_chunk(test_user.id, session_second, cache_row.id)

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_first,
        cache_ids=[cache_row.id],
    )

    assert released_count == 1
    assert deleted_count == 0
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()
    assert is_chunk_claimed_by_session(session_second, cache_row.id) is True


def test_expired_session_with_live_reader_protected_from_eviction(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_reader_guard.wav"
    wav_file.write_bytes(b"live stream guard audio")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_reader_guard", str(wav_file))
    session_name = "expired-reader-session"
    claim_session_chunk(test_user.id, session_name, cache_row.id)

    register_reader(cache_row.id)
    assert is_reading(cache_row.id) is True

    fake_redis_fixture.delete(f"tts:session_chunks:{session_name}")
    fake_redis_fixture.delete(f"tts:chunk_sessions:{cache_row.id}")
    fake_redis_fixture.delete(f"tts:session_user:{session_name}")

    assert is_chunk_protected(cache_row.id, test_user.id, "hash_reader_guard") is True

    evict_tts_cache(db=db)
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()

    unregister_reader(cache_row.id)
    assert is_reading(cache_row.id) is False


def test_duplicate_same_session_stream_claims_released_one_at_a_time(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_repeated_refrain.wav"
    wav_file.write_bytes(b"repeated refrain audio")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_repeated_refrain", str(wav_file))
    session_name = "reader-session-repeated"

    claim_session_chunk(test_user.id, session_name, cache_row.id)
    claim_session_chunk(test_user.id, session_name, cache_row.id)

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        cache_ids=[cache_row.id],
    )

    assert released_count == 1
    assert is_chunk_claimed_by_session(session_name, cache_row.id) is True
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()

    final_released, final_deleted = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        cache_ids=[cache_row.id],
    )
    assert final_released == 1
    assert final_deleted == 1
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is None
    assert not wav_file.exists()


@pytest.mark.redis_integration
def test_real_redis_two_producers_cancel_one_preserves_other(
    isolated_audio_dir, db: Session, test_user: User
):
    disposable_url = os.environ.get("DISPOSABLE_REDIS_URL")
    if not disposable_url:
        pytest.skip("DISPOSABLE_REDIS_URL not set")
    real_redis = redis.from_url(disposable_url, decode_responses=True)
    real_redis.ping()
    set_redis_client_for_testing(real_redis)

    cache_dir, ignored_podcast_dir = isolated_audio_dir
    unique_suffix = uuid.uuid4().hex
    text_hash = f"hash_real_concurrent_{unique_suffix}"
    unique_cache_id = int(uuid.uuid4().int % 10000000 + 10000)
    session_one = f"disposable-race-1-{unique_suffix}"
    session_two = f"disposable-race-2-{unique_suffix}"

    try:
        stub_row = TTSCache(
            id=unique_cache_id,
            user_id=test_user.id,
            text_hash=text_hash,
            audio_path=None,
            duration=None,
            size_bytes=None,
            created_at=datetime.now(timezone.utc),
            last_accessed=datetime.now(timezone.utc),
        )
        db.add(stub_row)
        db.commit()
        db.refresh(stub_row)

        register_producer(test_user.id, text_hash)
        register_producer(test_user.id, text_hash)
        claim_session_chunk(test_user.id, session_one, stub_row.id)
        claim_session_chunk(test_user.id, session_two, stub_row.id)

        assert is_producing(test_user.id, text_hash) is True

        unregister_producer(test_user.id, text_hash)
        assert is_producing(test_user.id, text_hash) is True

        cleanup_aborted_or_abandoned_stream(
            db_session=db,
            user_id=test_user.id,
            text_hash=text_hash,
            cache_id=stub_row.id,
            session_id=session_one,
        )

        row_after_cancel = db.query(TTSCache).filter_by(id=stub_row.id).first()
        assert row_after_cancel is not None
        assert is_chunk_claimed_by_session(session_two, stub_row.id) is True
    finally:
        real_redis.delete(
            f"tts:session_user:{session_one}",
            f"tts:session_user:{session_two}",
            f"tts:session_chunks:{session_one}",
            f"tts:session_chunks:{session_two}",
            f"tts:chunk_sessions:{unique_cache_id}",
            f"tts:producer:{test_user.id}:{text_hash}",
            f"tts:closed:{session_one}",
            f"tts:closed:{session_two}",
        )
        set_redis_client_for_testing(None)


def test_row_deleted_before_publication_cleans_temp_file(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    tmp_file = cache_dir / ".publication_race.tmp"
    tmp_file.write_bytes(b"in flight temp bytes")

    text_hash = "hash_pub_race"
    stub_row = TTSCache(
        user_id=test_user.id,
        text_hash=text_hash,
        audio_path=None,
        duration=None,
        size_bytes=None,
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )
    db.add(stub_row)
    db.commit()
    db.refresh(stub_row)

    session_name = "pub-race-session"
    claim_session_chunk(test_user.id, session_name, stub_row.id)

    db.delete(stub_row)
    db.commit()

    cleaned = cleanup_aborted_or_abandoned_stream(
        db_session=db,
        user_id=test_user.id,
        text_hash=text_hash,
        cache_id=stub_row.id,
        tmp_path=str(tmp_file),
        session_id=session_name,
    )

    assert cleaned is True
    assert not tmp_file.exists()



def test_summary_audio_preserved_during_reading_release_and_cleanup(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_summary_preserve.wav"
    wav_file.write_bytes(b"persistent summary audio")

    cache_row = create_dummy_cache_row(
        db, test_user.id, "hash_summary_preserve", str(wav_file), summary_id=456
    )
    session_name = "summary-release-session"

    claim_session_chunk(test_user.id, session_name, cache_row.id)

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        cache_ids=[cache_row.id],
    )

    assert deleted_count == 0
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()

    cleaned = cleanup_aborted_or_abandoned_stream(
        db_session=db,
        user_id=test_user.id,
        text_hash="hash_summary_preserve",
        cache_id=cache_row.id,
        session_id=session_name,
    )
    assert cleaned is False
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()


def test_expired_session_hash_partial_and_all_fallback(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file_first = cache_dir / "hash_fallback_one.wav"
    wav_file_first.write_bytes(b"fallback audio one")
    wav_file_second = cache_dir / "hash_fallback_two.wav"
    wav_file_second.write_bytes(b"fallback audio two")

    cache_row_first = create_dummy_cache_row(
        db, test_user.id, "hash_fallback_one", str(wav_file_first)
    )
    cache_row_second = create_dummy_cache_row(
        db, test_user.id, "hash_fallback_two", str(wav_file_second)
    )

    session_name = "fallback-session-expired"
    claim_session_chunk(test_user.id, session_name, cache_row_first.id)
    claim_session_chunk(test_user.id, session_name, cache_row_second.id)

    fake_redis_fixture.delete(f"tts:session_chunks:{session_name}")
    fake_redis_fixture.delete(f"tts:session_user:{session_name}")

    released_partial, deleted_partial = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        cache_ids=[cache_row_first.id],
    )
    assert released_partial == 1
    assert deleted_partial == 1
    assert db.query(TTSCache).filter_by(id=cache_row_first.id).first() is None
    assert not wav_file_first.exists()

    released_all, deleted_all = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        cache_ids=[cache_row_second.id],
        release_all_session=True,
    )
    assert deleted_all == 1
    assert db.query(TTSCache).filter_by(id=cache_row_second.id).first() is None
    assert not wav_file_second.exists()


@pytest.mark.asyncio
async def test_reading_audio_response_call_failure_runs_on_finish_and_cleans_counters(
    fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User
):
    from starlette.responses import StreamingResponse as StarletteStreamingResponse
    from app.routers.tts import ReadingAudioResponse

    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_response_call.wav"
    wav_file.write_bytes(b"response call wav")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_response_call", str(wav_file))

    register_reader(cache_row.id)
    register_producer(test_user.id, "hash_response_call")
    assert is_reading(cache_row.id) is True
    assert is_producing(test_user.id, "hash_response_call") is True

    callback_invocations = 0

    def cleanup_callback():
        nonlocal callback_invocations
        callback_invocations += 1
        unregister_reader(cache_row.id)
        unregister_producer(test_user.id, "hash_response_call")

    async def dummy_generator():
        yield b"chunk"

    response_instance = ReadingAudioResponse(
        dummy_generator(),
        on_finish=cleanup_callback,
        media_type="audio/wav",
    )

    async def mock_failed_super_call(self, scope, receive, send):
        raise RuntimeError("Simulated failure before generator iteration")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(StarletteStreamingResponse, "__call__", mock_failed_super_call)
        with pytest.raises(RuntimeError):
            await response_instance({}, None, None)

    assert callback_invocations == 1
    assert is_reading(cache_row.id) is False
    assert is_producing(test_user.id, "hash_response_call") is False
