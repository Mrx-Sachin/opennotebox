"""Celery tasks for TTS generation."""
import hashlib
import os
import wave
from datetime import datetime, timezone
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


@celery_app.task
def evict_tts_cache() -> dict:
    """LRU-evict on-disk TTS audio until total cache size is back under
    settings.tts_cache_max_gib. Registered on the Celery beat schedule to
    run hourly (see celery_app.py) - this setting existed but was wired up
    nowhere, so the cache only ever grew (391MB / 511 files observed on disk
    before this task existed).

    Two passes:
    1. Backfill size_bytes for any row that predates that column (or whose
       size was never recorded for some other reason) via os.path.getsize,
       so the running total below is accurate.
    2. Delete rows (and their .wav files) ordered by last_accessed ascending
       - least-recently-used first - until under budget.

    Row and file are always deleted together: leaving either behind is
    exactly the orphaning bug this task exists to clean up (see also the
    delete_document cascade fix in routers/documents.py, which handles the
    same failure mode at document-delete time instead of on a timer).
    """
    db: Session = SessionLocal()
    try:
        unsized = db.query(TTSCache).filter(TTSCache.size_bytes.is_(None)).all()
        for row in unsized:
            if row.audio_path and os.path.exists(row.audio_path):
                try:
                    row.size_bytes = os.path.getsize(row.audio_path)
                except OSError:
                    pass
            else:
                # No file on disk and no recorded size - nothing left to
                # account for; treat as empty rather than leaving it NULL
                # forever (NULL would otherwise make it re-scan every run).
                row.size_bytes = 0
        if unsized:
            db.commit()

        max_bytes = settings.tts_cache_max_gib * (1024 ** 3)
        total = db.query(func.coalesce(func.sum(TTSCache.size_bytes), 0)).scalar()

        if total <= max_bytes:
            return {"evicted": 0, "backfilled": len(unsized), "total_bytes": int(total)}

        evicted = 0
        # nullsfirst: any row somehow still missing last_accessed (shouldn't
        # happen - the column has a default) is evicted before anything with
        # an actual recency signal, since we have no better information.
        candidates = (
            db.query(TTSCache)
            .order_by(TTSCache.last_accessed.asc().nullsfirst())
            .all()
        )
        for row in candidates:
            if total <= max_bytes:
                break
            if row.audio_path and os.path.exists(row.audio_path):
                try:
                    os.remove(row.audio_path)
                except OSError as e:
                    logger.warning(f"evict_tts_cache: could not remove {row.audio_path}: {e}")
            total -= (row.size_bytes or 0)
            db.delete(row)
            evicted += 1
        db.commit()

        return {"evicted": evicted, "backfilled": len(unsized), "total_bytes": int(total)}
    finally:
        db.close()
