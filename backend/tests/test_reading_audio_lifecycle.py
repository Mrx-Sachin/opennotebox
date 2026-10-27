from typing import Optional
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock
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
    heartbeat_session,
    set_redis_client_for_testing,
)
from app.tasks.tts_tasks import evict_tts_cache, ACTIVE_PLAYBACK_GRACE_SECONDS
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


def test_immediate_release_on_completion(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_alpha.wav"
    wav_file.write_bytes(b"dummy wav content alpha")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_alpha", str(wav_file))
    session_alpha = "reader-session-alpha"

    claim_session_chunk(test_user.id, session_alpha, cache_row.id)
    assert is_chunk_claimed_by_session(session_alpha, cache_row.id) is True

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_alpha,
        cache_ids=[cache_row.id],
    )

    assert released_count == 1
    assert deleted_count == 1
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is None
    assert not wav_file.exists()


def test_simultaneous_readers_multi_session(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_multi.wav"
    wav_file.write_bytes(b"dummy wav content multi")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_multi", str(wav_file))
    session_tab_one = "tab-session-1"
    session_tab_two = "tab-session-2"

    claim_session_chunk(test_user.id, session_tab_one, cache_row.id)
    claim_session_chunk(test_user.id, session_tab_two, cache_row.id)

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_tab_one,
        cache_ids=[cache_row.id],
    )

    assert released_count == 1
    assert deleted_count == 0
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_tab_two,
        cache_ids=[cache_row.id],
    )

    assert released_count == 1
    assert deleted_count == 1
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is None
    assert not wav_file.exists()


def test_active_reader_protection(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_active_read.wav"
    wav_file.write_bytes(b"dummy wav content active read")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_active_read", str(wav_file))
    session_name = "reader-active-read-session"

    claim_session_chunk(test_user.id, session_name, cache_row.id)
    register_reader(cache_row.id)
    assert is_reading(cache_row.id) is True

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        cache_ids=[cache_row.id],
    )

    assert released_count == 1
    assert deleted_count == 0
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()

    unregister_reader(cache_row.id)
    assert is_reading(cache_row.id) is False


def test_active_producer_live_writer_protection(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "hash_producer.wav"
    wav_file.write_bytes(b"dummy wav content producer")

    cache_row = create_dummy_cache_row(db, test_user.id, "hash_producer", str(wav_file))
    session_name = "producer-protection-session"

    claim_session_chunk(test_user.id, session_name, cache_row.id)
    register_producer(test_user.id, "hash_producer")
    assert is_producing(test_user.id, "hash_producer") is True

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        cache_ids=[cache_row.id],
    )

    assert deleted_count == 0
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()

    unregister_producer(test_user.id, "hash_producer")
    assert is_producing(test_user.id, "hash_producer") is False


def test_cancelled_stream_cleans_orphan_rows(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    tmp_file = cache_dir / ".hash_cancelled.1234.tmp"
    tmp_file.write_bytes(b"partial temp data")

    stub_row = TTSCache(
        user_id=test_user.id,
        text_hash="hash_cancelled",
        audio_path=None,
        duration=None,
        size_bytes=None,
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )
    db.add(stub_row)
    db.commit()
    db.refresh(stub_row)

    session_name = "cancelled-stream-session"
    claim_session_chunk(test_user.id, session_name, stub_row.id)

    cleaned = cleanup_aborted_or_abandoned_stream(
        db_session=db,
        user_id=test_user.id,
        text_hash="hash_cancelled",
        cache_id=stub_row.id,
        tmp_path=str(tmp_file),
        session_id=session_name,
    )

    assert cleaned is True
    assert not tmp_file.exists()
    assert db.query(TTSCache).filter_by(id=stub_row.id).first() is None


def test_late_completion_does_not_resurrect_abandoned_audio(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    tmp_wav = cache_dir / ".hash_late.tmp"
    tmp_wav.write_bytes(b"late synthesized temp data")

    stub_row = TTSCache(
        user_id=test_user.id,
        text_hash="hash_late",
        audio_path=None,
        duration=None,
        size_bytes=None,
        created_at=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
    )
    db.add(stub_row)
    db.commit()
    db.refresh(stub_row)

    session_name = "late-completion-session"
    claim_session_chunk(test_user.id, session_name, stub_row.id)

    release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id=session_name,
        release_all_session=True,
    )
    assert is_chunk_claimed_by_session(session_name, stub_row.id) is False

    cleaned = cleanup_aborted_or_abandoned_stream(
        db_session=db,
        user_id=test_user.id,
        text_hash="hash_late",
        cache_id=stub_row.id,
        tmp_path=str(tmp_wav),
        session_id=session_name,
    )

    assert cleaned is True
    assert db.query(TTSCache).filter_by(id=stub_row.id).first() is None
    assert not tmp_wav.exists()


def test_session_user_mismatch_verification(fake_redis_fixture, db: Session, test_user: User, second_user: User):
    session_name = "private-user-session"
    claim_session_chunk(test_user.id, session_name, 999)

    with pytest.raises(PermissionError):
        claim_session_chunk(second_user.id, session_name, 1000)

    with pytest.raises(PermissionError):
        release_session_chunks(db, second_user.id, session_name, [999])


def test_fail_closed_when_redis_unavailable(isolated_audio_dir, db: Session, test_user: User):
    set_redis_client_for_testing(None)

    assert is_producing(test_user.id, "any_hash") is True
    assert is_reading(123) is True
    assert is_chunk_protected(123, test_user.id, "any_hash") is True

    cache_dir, ignored_podcast_dir = isolated_audio_dir
    wav_file = cache_dir / "fail_closed.wav"
    wav_file.write_bytes(b"fail closed content")
    cache_row = create_dummy_cache_row(db, test_user.id, "fail_closed", str(wav_file))

    released_count, deleted_count = release_session_chunks(
        db_session=db,
        user_id=test_user.id,
        session_id="any_session",
        cache_ids=[cache_row.id],
    )

    assert released_count == 0
    assert deleted_count == 0
    assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
    assert wav_file.exists()


def test_heartbeat_renews_session(fake_redis_fixture, test_user: User):
    session_name = "heartbeat-test-session"
    claim_session_chunk(test_user.id, session_name, 555)
    assert heartbeat_session(test_user.id, session_name) is True
    assert heartbeat_session(99999, session_name) is False


def test_evict_tts_cache_protects_active_reading_sessions(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir

    stale_time = datetime.now(timezone.utc)
    protected_wav = cache_dir / "protected_active.wav"
    protected_wav.write_bytes(b"protected audio content")

    protected_row = TTSCache(
        user_id=test_user.id,
        text_hash="protected_active",
        audio_path=str(protected_wav),
        duration=5.0,
        size_bytes=len(b"protected audio content"),
        created_at=stale_time,
        last_accessed=stale_time,
    )
    db.add(protected_row)
    db.commit()
    db.refresh(protected_row)

    session_name = "protected-reading-session"
    claim_session_chunk(test_user.id, session_name, protected_row.id)

    evict_tts_cache(db=db)
    assert db.query(TTSCache).filter_by(id=protected_row.id).first() is not None
    assert protected_wav.exists()


def test_pass2_preserves_fresh_null_reservation(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    fresh_time = datetime.now(timezone.utc)
    fresh_stub = TTSCache(
        user_id=test_user.id,
        text_hash="fresh_pending_hash",
        audio_path=None,
        duration=None,
        size_bytes=None,
        created_at=fresh_time,
        last_accessed=fresh_time,
    )
    db.add(fresh_stub)
    db.commit()
    db.refresh(fresh_stub)

    evict_tts_cache(db=db)
    assert db.query(TTSCache).filter_by(id=fresh_stub.id).first() is not None

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=ACTIVE_PLAYBACK_GRACE_SECONDS + 100)
    fresh_stub.created_at = stale_time
    fresh_stub.last_accessed = stale_time
    db.commit()

    evict_tts_cache(db=db)
    assert db.query(TTSCache).filter_by(id=fresh_stub.id).first() is None


def test_fallback_reading_cache_sweep(fake_redis_fixture, isolated_audio_dir, db: Session, test_user: User):
    cache_dir, ignored_podcast_dir = isolated_audio_dir
    stale_time = datetime.now(timezone.utc) - timedelta(hours=3)

    reading_wav = cache_dir / "stale_reading.wav"
    reading_wav.write_bytes(b"stale reading audio")
    reading_row = TTSCache(
        user_id=test_user.id,
        text_hash="stale_reading_hash",
        audio_path=str(reading_wav),
        duration=4.0,
        size_bytes=len(b"stale reading audio"),
        created_at=stale_time,
        last_accessed=stale_time,
        summary_id=None,
    )
    db.add(reading_row)

    summary_wav = cache_dir / "summary_audio.wav"
    summary_wav.write_bytes(b"summary persistent audio")
    summary_row = TTSCache(
        user_id=test_user.id,
        text_hash="summary_hash",
        audio_path=str(summary_wav),
        duration=10.0,
        size_bytes=len(b"summary persistent audio"),
        created_at=stale_time,
        last_accessed=stale_time,
        summary_id=888,
    )
    db.add(summary_row)
    db.commit()

    evict_res = evict_tts_cache(db=db)
    assert evict_res["stale_reading_swept"] == 1
    assert db.query(TTSCache).filter_by(id=reading_row.id).first() is None
    assert not reading_wav.exists()

    assert db.query(TTSCache).filter_by(id=summary_row.id).first() is not None
    assert summary_wav.exists()


class NonClosingSessionWrapper:
    def __init__(self, inner_session):
        self._inner = inner_session

    def __getattr__(self, attribute_name):
        return getattr(self._inner, attribute_name)

    def close(self):
        pass


async def test_http_stream_and_release_lifecycle(
    fake_redis_fixture,
    isolated_audio_dir,
    db: Session,
    test_user: User,
    client,
    auth_header: dict,
    monkeypatch,
):
    headers = auth_header
    session_id = "http-session-123"

    session_wrapper = NonClosingSessionWrapper(db)
    monkeypatch.setattr("app.routers.tts.SessionLocal", lambda: session_wrapper)

    async def _mock_bytes():
        yield b"RIFF" + b"\x00" * 36

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.aiter_bytes = _mock_bytes
    mock_resp.aread = AsyncMock(return_value=b"")

    with patch("httpx.AsyncClient.stream") as mock_stream:
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_resp
        mock_ctx.__aexit__.return_value = None
        mock_stream.return_value = mock_ctx

        response = await client.post(
            "/api/tts/stream",
            json={"text": "Hello test world", "session_id": session_id},
            headers=headers,
        )
        assert response.status_code == 200
        assert "x-tts-cache-id" in response.headers
        cache_id = int(response.headers["x-tts-cache-id"])

        assert is_chunk_claimed_by_session(session_id, cache_id) is True

        db.expire_all()
        committed_row = db.query(TTSCache).filter_by(id=cache_id).first()
        assert committed_row is not None
        assert committed_row.audio_path is not None
        assert os.path.exists(committed_row.audio_path)
        audio_file_path = committed_row.audio_path

        heartbeat_resp = await client.post(
            "/api/tts/heartbeat",
            json={"session_id": session_id},
            headers=headers,
        )
        assert heartbeat_resp.status_code == 200
        assert heartbeat_resp.json()["renewed"] is True

        release_resp = await client.post(
            "/api/tts/release",
            json={"session_id": session_id, "cache_ids": [cache_id]},
            headers=headers,
        )
        assert release_resp.status_code == 200
        assert release_resp.json()["deleted_count"] == 1
        assert not os.path.exists(audio_file_path)
        assert db.query(TTSCache).filter_by(id=cache_id).first() is None


@pytest.mark.redis_integration
def test_real_disposable_redis_claim_and_release(isolated_audio_dir, db: Session, test_user: User):
    disposable_url = os.environ.get("DISPOSABLE_REDIS_URL")
    if not disposable_url:
        pytest.skip("Set DISPOSABLE_REDIS_URL to an isolated Redis instance")
    real_redis = redis.from_url(disposable_url, decode_responses=True)
    real_redis.ping()
    set_redis_client_for_testing(real_redis)

    cache_dir, ignored_podcast_dir = isolated_audio_dir
    unique_suffix = uuid.uuid4().hex
    real_wav = cache_dir / f"real_{unique_suffix}.wav"
    real_wav.write_bytes(b"real disposable redis audio")
    unique_cache_id = int(uuid.uuid4().int % 10000000 + 10000)
    session_one = f"disposable-session-1-{unique_suffix}"
    session_two = f"disposable-session-2-{unique_suffix}"

    try:
        cache_row = create_dummy_cache_row(
            db, test_user.id, f"hash_{unique_suffix}", str(real_wav), cache_id=unique_cache_id
        )

        claim_session_chunk(test_user.id, session_one, cache_row.id)
        claim_session_chunk(test_user.id, session_two, cache_row.id)
        assert is_chunk_claimed_by_session(session_one, cache_row.id) is True
        assert is_chunk_claimed_by_session(session_two, cache_row.id) is True

        released_first, deleted_first = release_session_chunks(
            db_session=db,
            user_id=test_user.id,
            session_id=session_one,
            cache_ids=[cache_row.id],
        )
        assert released_first == 1
        assert deleted_first == 0
        assert db.query(TTSCache).filter_by(id=cache_row.id).first() is not None
        assert real_wav.exists()

        released_second, deleted_second = release_session_chunks(
            db_session=db,
            user_id=test_user.id,
            session_id=session_two,
            cache_ids=[cache_row.id],
        )
        assert released_second == 1
        assert deleted_second == 1
        assert db.query(TTSCache).filter_by(id=cache_row.id).first() is None
        assert not real_wav.exists()
    finally:
        real_redis.delete(
            f"tts:session_user:{session_one}",
            f"tts:session_user:{session_two}",
            f"tts:session_chunks:{session_one}",
            f"tts:session_chunks:{session_two}",
            f"tts:chunk_sessions:{unique_cache_id}",
            f"tts:closed:{session_one}",
            f"tts:closed:{session_two}",
        )
        set_redis_client_for_testing(None)
