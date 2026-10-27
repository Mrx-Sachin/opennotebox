"""Tests for bounded TTS cache cleanup, expiry, and active playback protection."""

from datetime import datetime, timedelta, timezone
import hashlib
import os
import time

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, Summary, TTSCache, User
from app.services.reading_audio import ACTIVE_COUNTER_TTL_SECONDS, set_redis_client_for_testing
from app.tasks.tts_tasks import evict_tts_cache, generate_tts
from tests.audio_test_helpers import FakeReadingAudioRedis


class NonClosingSessionWrapper:
    def __init__(self, inner_session):
        self._inner = inner_session

    def __getattr__(self, attribute_name):
        return getattr(self._inner, attribute_name)

    def close(self):
        pass


@pytest.fixture
def tts_cache_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "tts-cache"
    podcast_dir = data_dir / "podcasts"
    uploads_dir = data_dir / "uploads"
    cache_dir.mkdir(parents=True)
    podcast_dir.mkdir(parents=True)
    uploads_dir.mkdir(parents=True)

    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "tts_cache_max_gib", 1)
    monkeypatch.setattr(settings, "tts_cache_max_age_days", 7)
    fake_client = FakeReadingAudioRedis()
    set_redis_client_for_testing(fake_client)
    yield cache_dir, podcast_dir, uploads_dir
    set_redis_client_for_testing(None)


def test_evict_tts_cache_cleans_stale_tmp_and_orphans(tts_cache_dir, db: Session):
    cache_dir, podcast_dir, _ = tts_cache_dir

    stale_tmp = cache_dir / ".oldhash.1234.tmp"
    stale_tmp.write_bytes(b"temp data")
    old_time = time.time() - (ACTIVE_COUNTER_TTL_SECONDS + 100)
    os.utime(str(stale_tmp), (old_time, old_time))

    fresh_tmp = cache_dir / ".newhash.5678.tmp"
    fresh_tmp.write_bytes(b"fresh temp")

    orphaned_wav = cache_dir / "orphaned123.wav"
    orphaned_wav.write_bytes(b"dummy wav data" * 100)
    os.utime(str(orphaned_wav), (old_time, old_time))

    fresh_orphan = cache_dir / "fresh_orphan.wav"
    fresh_orphan.write_bytes(b"fresh data")

    podcast_file = podcast_dir / "101.wav"
    podcast_file.write_bytes(b"important user podcast" * 50)
    os.utime(str(podcast_file), (old_time, old_time))

    res = evict_tts_cache(db=db)
    assert res["stale_tmp_removed"] == 1
    assert res["orphans_removed"] == 1
    assert not stale_tmp.exists()
    assert fresh_tmp.exists()
    assert not orphaned_wav.exists()
    assert fresh_orphan.exists()
    assert podcast_file.exists()


def test_evict_tts_cache_ttl_expiry_and_active_protection(tts_cache_dir, db: Session):
    cache_dir, _, _ = tts_cache_dir

    user = db.query(User).first()
    user_id = user.id if user else None

    old_wav = cache_dir / "old_audio.wav"
    old_wav.write_bytes(b"old wav content" * 20)
    stale_dt = datetime.now(timezone.utc) - timedelta(days=10)
    stale_row = TTSCache(
        user_id=user_id,
        text_hash="old_audio",
        audio_path=str(old_wav),
        duration=5.0,
        size_bytes=len(b"old wav content" * 20),
        created_at=stale_dt,
        last_accessed=stale_dt,
    )
    db.add(stale_row)

    active_wav = cache_dir / "active_audio.wav"
    active_wav.write_bytes(b"active wav content" * 20)
    active_row = TTSCache(
        user_id=user_id,
        text_hash="active_audio",
        audio_path=str(active_wav),
        duration=5.0,
        size_bytes=len(b"active wav content" * 20),
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
        last_accessed=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db.add(active_row)
    db.commit()

    res = evict_tts_cache(db=db)
    assert res["expired_ttl"] == 1
    assert not old_wav.exists()
    assert active_wav.exists()
    assert db.query(TTSCache).filter_by(text_hash="old_audio").first() is None
    assert db.query(TTSCache).filter_by(text_hash="active_audio").first() is not None


def test_evict_tts_cache_lru_size_cap(tts_cache_dir, db: Session, monkeypatch):
    cache_dir, _, _ = tts_cache_dir
    user = db.query(User).first()
    user_id = user.id if user else None

    monkeypatch.setattr(settings, "tts_cache_max_gib", 0.000001)

    paths = []
    for index in range(3):
        wav = cache_dir / f"cap_test_{index}.wav"
        data = b"x" * 600
        wav.write_bytes(data)
        paths.append(str(wav))
        access_time = datetime.now(timezone.utc) - timedelta(hours=2 - index, minutes=30)
        row = TTSCache(
            user_id=user_id,
            text_hash=f"cap_test_{index}",
            audio_path=str(wav),
            duration=2.0,
            size_bytes=len(data),
            created_at=access_time,
            last_accessed=access_time,
        )
        db.add(row)
    db.commit()

    res = evict_tts_cache(db=db)
    assert res["evicted_lru"] >= 1
    assert not os.path.exists(paths[0])
    assert os.path.exists(paths[2])


def test_generate_tts_tags_summary_provenance_on_cache_hit(
    tts_cache_dir, db: Session, test_user: User, monkeypatch
):
    cache_dir, _, _ = tts_cache_dir
    session_wrapper = NonClosingSessionWrapper(db)
    monkeypatch.setattr("app.tasks.tts_tasks.SessionLocal", lambda: session_wrapper)

    document = Document(
        user_id=test_user.id,
        title="Doc for summary provenance",
        format="pdf",
        content_hash="dochash_summary_prov_123",
        file_path="/fake/doc_prov.pdf",
        file_size=1024,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    summary = Summary(
        user_id=test_user.id,
        document_id=document.id,
        mode="page",
        content="Valid summary text content for testing provenance tagging",
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)

    test_text = "This is a sentence for summary audio provenance test."
    voice = "default"
    speed = 1.0
    text_hash = hashlib.md5(f"{test_text}{test_user.id}{voice}{speed}".encode()).hexdigest()

    wav_file = cache_dir / f"stream_stub_{text_hash}.wav"
    wav_file.write_bytes(b"RIFF" + b"\x00" * 40)

    stale_timestamp = datetime.now(timezone.utc) - timedelta(hours=3)
    stream_stub = TTSCache(
        user_id=test_user.id,
        document_id=None,
        summary_id=None,
        text_hash=text_hash,
        audio_path=str(wav_file),
        duration=2.0,
        size_bytes=len(wav_file.read_bytes()),
        created_at=stale_timestamp,
        last_accessed=stale_timestamp,
    )
    db.add(stream_stub)
    db.commit()
    db.refresh(stream_stub)

    result = generate_tts(
        user_id=test_user.id,
        text=test_text,
        doc_id=document.id,
        summary_id=summary.id,
        voice=voice,
        speed=speed,
    )
    assert result["cached"] is True
    assert result["audio_path"] == str(wav_file)

    db.refresh(stream_stub)
    assert stream_stub.summary_id == summary.id
    assert stream_stub.document_id == document.id

    sweep_result = evict_tts_cache(db=db)
    assert sweep_result["stale_reading_swept"] == 0
    assert db.query(TTSCache).filter_by(id=stream_stub.id).first() is not None
    assert wav_file.exists()
