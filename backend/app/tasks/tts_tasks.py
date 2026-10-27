"""Celery tasks for TTS generation."""
import hashlib
import os
import time
import wave
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import TTSCache, Document
from app.config import settings
from app.services.llm_settings import get_default_voice


def _wav_duration(path: str) -> Optional[float]:
    """Return the duration in seconds of a WAV file, or None if unreadable."""
    try:
        with wave.open(path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate) if rate else None
    except Exception:
        return None


@celery_app.task(bind=True, max_retries=2)
def generate_tts(
    self,
    user_id: int,
    text: str,
    doc_id: Optional[int] = None,
    summary_id: Optional[int] = None,
    voice: Optional[str] = None,
    speed: float = 1.0,
) -> dict:
    """Generate TTS audio for a text segment using Piper TTS.

    voice: registry name from tts/main.py's VOICE_MODELS ("default",
    "host_a", "host_b", or a raw Piper model name). Falls back to the
    configured tts.default_voice (see services/llm_settings.py) when omitted.
    speed: native synthesis speed passed straight to tts/main.py, which
    clamps it to whatever the resolved engine actually supports.
    """
    db: Session = SessionLocal()
    try:
        # doc_id is only a provenance tag on the cache row, but it is written
        # verbatim from the task's arguments. Drop it unless the document
        # really belongs to user_id, so a mismatched pair cannot attribute one
        # user's cached audio to another user's document (which would then be
        # deleted along with that document). The audio itself is already
        # isolated - text_hash includes user_id and every read path filters on
        # both - so dropping the tag is the conservative, non-fatal fix.
        if doc_id is not None:
            owns_doc = (
                db.query(Document.id)
                .filter(Document.id == doc_id, Document.user_id == user_id)
                .first()
                is not None
            )
            if not owns_doc:
                logger.warning(
                    f"generate_tts: doc {doc_id} does not belong to user {user_id}; "
                    "storing cache row without a document reference"
                )
                doc_id = None

        voice = voice or get_default_voice(user_id)
        text_hash = hashlib.md5(f"{text}{user_id}{voice}{speed}".encode()).hexdigest()

        cache = db.query(TTSCache).filter(
            TTSCache.text_hash == text_hash,
            TTSCache.user_id == user_id,
        ).first()

        if cache and cache.audio_path and os.path.exists(cache.audio_path):
            # Bump recency so this row isn't picked as an eviction candidate
            # while it's still actively being reused (see evict_tts_cache).
            cache.last_accessed = datetime.now(timezone.utc)
            db.commit()
            return {"text_hash": text_hash, "audio_path": cache.audio_path, "cached": True}

        output_dir = os.path.join(settings.data_dir, "tts-cache")
        os.makedirs(output_dir, exist_ok=True)
        audio_file = os.path.join(output_dir, f"{text_hash}.wav")

        if os.path.exists(audio_file):
            if not cache:
                cache = TTSCache(
                    user_id=user_id,
                    document_id=doc_id,
                    summary_id=summary_id,
                    text_hash=text_hash,
                    audio_path=audio_file,
                    duration=_wav_duration(audio_file),
                    size_bytes=os.path.getsize(audio_file),
                )
                db.add(cache)
                db.commit()
            return {"text_hash": text_hash, "audio_path": audio_file, "cached": True}

        response = httpx.post(
            f"{settings.piper_tts_url}/tts",
            json={
                "text": text[:5000],
                "voice": voice,
                "speed": speed,
            },
            # The "high" quality host_a/host_b models (see tts/main.py) are
            # noticeably slower than the old "medium" tier - a near-5000-char
            # page/summary could take well over 120s to synthesize at that
            # tier, so give it real headroom.
            timeout=180,
        )

        if response.status_code == 200:
            with open(audio_file, "wb") as f:
                f.write(response.content)

            cache = TTSCache(
                user_id=user_id,
                document_id=doc_id,
                summary_id=summary_id,
                text_hash=text_hash,
                audio_path=audio_file,
                duration=_wav_duration(audio_file),
                size_bytes=len(response.content),
            )
            db.add(cache)
            db.commit()

            return {"text_hash": text_hash, "audio_path": audio_file, "cached": False}
        else:
            raise RuntimeError(f"Piper TTS failed: {response.status_code}")

    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        raise self.retry(exc=e, countdown=60)
    finally:
        db.close()


ACTIVE_PLAYBACK_GRACE_SECONDS = 900  # 15 minutes to protect active playback


def _to_timestamp(dt: Optional[datetime]) -> Optional[float]:
    """Convert a naive or timezone-aware datetime to a UTC timestamp."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@celery_app.task
def evict_tts_cache(db: Optional[Session] = None) -> dict:
    """Bounded automatic TTS cache cleanup and expiry.

    Keeps on-disk TTS audio bounded in size (settings.tts_cache_max_gib) and age
    (settings.tts_cache_max_age_days), cleans orphaned or aborted files, and
    protects active playback and legitimate user audio (podcasts, uploads).

    Execution passes:
    1. Stale temporary file cleanup (.text_hash.*.tmp older than grace period).
    2. Missing DB row cleanup (entries pointing to missing files older than grace).
    3. Orphaned file cleanup (*.wav in tts-cache with no DB row, older than grace).
    4. TTL-based expiration (entries older than settings.tts_cache_max_age_days).
    5. LRU size eviction (least-recently-accessed first when exceeding size cap,
       protecting recently accessed items).
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    try:
        now_ts = time.time()
        grace_cutoff_ts = now_ts - ACTIVE_PLAYBACK_GRACE_SECONDS
        max_age_days = getattr(settings, "tts_cache_max_age_days", 7)
        ttl_cutoff_ts = now_ts - (max_age_days * 86400)

        cache_dir = os.path.realpath(os.path.join(settings.data_dir, "tts-cache"))
        os.makedirs(cache_dir, exist_ok=True)

        stale_tmp_removed = 0
        orphans_removed = 0
        missing_pruned = 0
        expired_ttl = 0
        evicted_lru = 0

        # Pass 1: Clean stale temporary files (.hash.*.tmp)
        try:
            for entry in os.scandir(cache_dir):
                if entry.name.startswith(".") and entry.name.endswith(".tmp") and entry.is_file():
                    try:
                        if entry.stat().st_mtime < grace_cutoff_ts:
                            os.remove(entry.path)
                            stale_tmp_removed += 1
                    except OSError:
                        pass
        except OSError as e:
            logger.warning(f"evict_tts_cache: failed scanning temp files in {cache_dir}: {e}")

        # Pass 2: Backfill size_bytes and prune missing rows
        all_rows = db.query(TTSCache).all()
        unsized_count = 0
        for row in all_rows:
            if row.audio_path and os.path.exists(row.audio_path):
                if row.size_bytes is None or row.size_bytes <= 0:
                    try:
                        row.size_bytes = os.path.getsize(row.audio_path)
                        unsized_count += 1
                    except OSError:
                        pass
            else:
                created_ts = _to_timestamp(row.created_at)
                if created_ts is not None and created_ts < grace_cutoff_ts:
                    db.delete(row)
                    missing_pruned += 1
        if unsized_count or missing_pruned:
            db.commit()

        # Pass 3: Clean orphaned .wav files not in database
        valid_paths = set()
        for r in db.query(TTSCache.audio_path).filter(TTSCache.audio_path.isnot(None)).all():
            if r[0]:
                valid_paths.add(os.path.realpath(r[0]))

        try:
            for entry in os.scandir(cache_dir):
                if entry.name.endswith(".wav") and entry.is_file():
                    real_path = os.path.realpath(entry.path)
                    if real_path not in valid_paths:
                        try:
                            if entry.stat().st_mtime < grace_cutoff_ts:
                                os.remove(entry.path)
                                orphans_removed += 1
                        except OSError:
                            pass
        except OSError as e:
            logger.warning(f"evict_tts_cache: failed scanning orphaned files in {cache_dir}: {e}")

        # Pass 4: TTL expiration (older than max_age_days)
        remaining_rows = db.query(TTSCache).all()
        for row in remaining_rows:
            last_ts = _to_timestamp(row.last_accessed) or _to_timestamp(row.created_at)
            if last_ts is not None and last_ts < ttl_cutoff_ts:
                # Protect if accessed within active playback grace
                if last_ts >= grace_cutoff_ts:
                    continue
                if row.audio_path and os.path.exists(row.audio_path):
                    try:
                        os.remove(row.audio_path)
                    except OSError:
                        pass
                db.delete(row)
                expired_ttl += 1
        if expired_ttl:
            db.commit()

        # Pass 5: LRU size eviction
        max_bytes = settings.tts_cache_max_gib * (1024 ** 3)
        total = db.query(func.coalesce(func.sum(TTSCache.size_bytes), 0)).scalar() or 0

        if total > max_bytes:
            candidates = (
                db.query(TTSCache)
                .order_by(TTSCache.last_accessed.asc().nullsfirst())
                .all()
            )
            for row in candidates:
                if total <= max_bytes:
                    break
                last_ts = _to_timestamp(row.last_accessed) or _to_timestamp(row.created_at)
                if last_ts is not None and last_ts >= grace_cutoff_ts:
                    continue  # protect active playback
                if row.audio_path and os.path.exists(row.audio_path):
                    try:
                        os.remove(row.audio_path)
                    except OSError:
                        pass
                total -= (row.size_bytes or 0)
                db.delete(row)
                evicted_lru += 1
            db.commit()

        total = db.query(func.coalesce(func.sum(TTSCache.size_bytes), 0)).scalar() or 0
        return {
            "evicted": evicted_lru + expired_ttl,
            "evicted_lru": evicted_lru,
            "expired_ttl": expired_ttl,
            "orphans_removed": orphans_removed,
            "stale_tmp_removed": stale_tmp_removed,
            "missing_pruned": missing_pruned,
            "backfilled": unsized_count,
            "total_bytes": int(total),
        }
    finally:
        if should_close:
            db.close()
