"""Tests for bounded TTS cache cleanup, expiry, and active playback protection."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models import TTSCache, User
from app.tasks.tts_tasks import evict_tts_cache, ACTIVE_PLAYBACK_GRACE_SECONDS


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
    return cache_dir, podcast_dir, uploads_dir


def test_evict_tts_cache_cleans_stale_tmp_and_orphans(tts_cache_dir, db: Session):
    cache_dir, podcast_dir, _ = tts_cache_dir

    # 1. Stale temporary file (> 15 min old)
    stale_tmp = cache_dir / ".oldhash.1234.tmp"
    stale_tmp.write_bytes(b"temp data")
    old_time = time.time() - (ACTIVE_PLAYBACK_GRACE_SECONDS + 100)
    os.utime(str(stale_tmp), (old_time, old_time))

    # 2. Fresh temporary file (< 15 min old) - must be protected
    fresh_tmp = cache_dir / ".newhash.5678.tmp"
    fresh_tmp.write_bytes(b"fresh temp")

    # 3. Orphaned .wav file (> 15 min old, no DB entry)
    orphaned_wav = cache_dir / "orphaned123.wav"
    orphaned_wav.write_bytes(b"dummy wav data" * 100)
    os.utime(str(orphaned_wav), (old_time, old_time))

    # 4. Fresh orphaned .wav (< 15 min old) - in-flight protection
    fresh_orphan = cache_dir / "fresh_orphan.wav"
    fresh_orphan.write_bytes(b"fresh data")

    # 5. Legitimate persisted podcast file - MUST NEVER BE TOUCHED
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
    assert podcast_file.exists()  # protected user podcast!


def test_evict_tts_cache_ttl_expiry_and_active_protection(tts_cache_dir, db: Session):
    cache_dir, _, _ = tts_cache_dir

    # User for testing
    user = db.query(User).first()
    user_id = user.id if user else None

    # Stale entry: 10 days old (> 7 days TTL)
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

    # Recently accessed entry (3 days old created, but accessed 5 minutes ago)
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

    # Set very small max cap (e.g. 1000 bytes)
    monkeypatch.setattr(settings, "tts_cache_max_gib", 0.000001)  # ~1073 bytes

    # Create 3 files of 600 bytes each
    paths = []
    for i in range(3):
        wav = cache_dir / f"cap_test_{i}.wav"
        data = b"x" * 600
        wav.write_bytes(data)
        paths.append(str(wav))
        # i=0 is 2 hours ago, i=1 is 1 hour ago, i=2 is 30 mins ago
        access_time = datetime.now(timezone.utc) - timedelta(hours=2 - i, minutes=30)
        row = TTSCache(
            user_id=user_id,
            text_hash=f"cap_test_{i}",
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
    # Oldest (cap_test_0) should be evicted first
    assert not os.path.exists(paths[0])
    assert os.path.exists(paths[2])
