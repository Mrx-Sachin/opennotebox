"""Celery tasks for TTS generation."""

from datetime import datetime, timezone
import hashlib
import os
import time
from typing import Optional
import uuid
import wave

import httpx
from loguru import logger
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models import Document, LibraryEntry, Summary, TTSCache, User
from app.services.library_access import accessible_document_query
from app.services.llm_settings import get_default_voice
from app.services.reading_audio import (
    ACTIVE_COUNTER_TTL_SECONDS,
    is_chunk_protected,
    is_producing,
    register_producer,
    safe_unlink_audio,
    unregister_producer,
)

ACTIVE_PLAYBACK_GRACE_SECONDS = 900


def _wav_duration(audio_path: str) -> Optional[float]:
    """Return the duration in seconds of a WAV file, or None if unreadable."""
    try:
        with wave.open(audio_path, "rb") as wave_file:
            total_frames = wave_file.getnframes()
            frame_rate = wave_file.getframerate()
            return total_frames / float(frame_rate) if frame_rate else None
    except Exception:
        return None


def _lock_cache_write(
    database_session: Session,
    user_id: int,
    doc_id: Optional[int],
    summary_id: Optional[int],
) -> tuple[bool, Optional[int]]:
    """Recheck ownership and hold the relevant rows through cache commit."""
    database_session.rollback()
    if not (database_session.query(User.id).filter(User.id == user_id).with_for_update().first()):
        return False, summary_id
    if doc_id is not None and not (
        database_session.query(LibraryEntry.id)
        .filter(
            LibraryEntry.user_id == user_id,
            LibraryEntry.document_id == doc_id,
        )
        .with_for_update()
        .first()
    ):
        return False, summary_id
    if summary_id is not None and not (
        database_session.query(Summary.id)
        .filter(
            Summary.id == summary_id,
            Summary.user_id == user_id,
            Summary.content != "",
        )
        .with_for_update()
        .first()
    ):
        summary_id = None
    return True, summary_id


def _parse_entry_name(entry_name: str) -> tuple[Optional[int], Optional[str]]:
    """Parse cache id and text hash from a filename."""
    raw_identifier = entry_name
    if raw_identifier.startswith("."):
        raw_identifier = raw_identifier[1:]
    if raw_identifier.endswith(".tmp"):
        raw_identifier = raw_identifier[:-4]
    elif raw_identifier.endswith(".wav"):
        raw_identifier = raw_identifier[:-4]
    if "." in raw_identifier:
        raw_identifier = raw_identifier.split(".", 1)[0]
    if "_" in raw_identifier:
        parts = raw_identifier.split("_")
        if parts[0].isdigit():
            cache_id = int(parts[0])
            text_hash = parts[1] if len(parts) > 1 else None
            return cache_id, text_hash
        return None, parts[0]
    return None, raw_identifier


def _is_entry_producing_or_protected(
    database_session: Session,
    cache_id: Optional[int],
    text_hash: Optional[str],
) -> tuple[bool, bool]:
    """Check if file has an active producer or reading session lock."""
    try:
        if cache_id is not None:
            cache_row = (
                database_session.query(TTSCache.user_id, TTSCache.text_hash)
                .filter(TTSCache.id == cache_id)
                .first()
            )
            if cache_row is not None:
                target_user_id, target_hash = cache_row[0], cache_row[1]
                if is_producing(target_user_id, target_hash) or is_chunk_protected(
                    cache_id, target_user_id, target_hash
                ):
                    return True, True
                return False, True
        if text_hash:
            matching_rows = (
                database_session.query(TTSCache.id, TTSCache.user_id)
                .filter(TTSCache.text_hash == text_hash)
                .all()
            )
            if matching_rows:
                for row_match in matching_rows:
                    matched_id, matched_user_id = row_match[0], row_match[1]
                    if is_producing(matched_user_id, text_hash) or is_chunk_protected(
                        matched_id, matched_user_id, text_hash
                    ):
                        return True, True
                return False, True
        return False, False
    except Exception as check_error:
        logger.warning(
            f"Failed verifying producer for cache_id={cache_id}, text_hash={text_hash}: {check_error}"
        )
        return True, False


def _to_timestamp(date_time_value: Optional[datetime]) -> Optional[float]:
    """Convert a naive or timezone-aware datetime to a UTC timestamp."""
    if date_time_value is None:
        return None
    if date_time_value.tzinfo is None:
        date_time_value = date_time_value.replace(tzinfo=timezone.utc)
    return date_time_value.timestamp()


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
    """Generate TTS audio for a text segment using Piper TTS."""
    database_session: Session = SessionLocal()
    temporary_file_path: Optional[str] = None
    try:
        if doc_id is not None:
            if (
                not accessible_document_query(database_session, user_id)
                .filter(Document.id == doc_id)
                .first()
            ):
                logger.warning(
                    f"generate_tts: doc {doc_id} is not accessible to user {user_id}; "
                    "storing cache row without a document reference"
                )
                doc_id = None

        if summary_id is not None:
            summary = (
                database_session.query(Summary)
                .filter(
                    Summary.id == summary_id,
                    Summary.user_id == user_id,
                    Summary.content != "",
                )
                .first()
            )
            if (
                not summary
                or not accessible_document_query(database_session, user_id)
                .filter(Document.id == summary.document_id)
                .first()
                or (doc_id is not None and doc_id != summary.document_id)
            ):
                logger.warning(
                    f"generate_tts: summary {summary_id} is not accessible to user {user_id}; "
                    "storing cache row without a summary reference"
                )
                summary_id = None
            elif doc_id is None:
                doc_id = summary.document_id

        resolved_voice = voice or get_default_voice(user_id)
        text_hash = hashlib.md5(f"{text}{user_id}{resolved_voice}{speed}".encode()).hexdigest()

        now_utc = datetime.now(timezone.utc)
        insert_statement = (
            pg_insert(TTSCache)
            .values(
                user_id=user_id,
                document_id=doc_id,
                summary_id=summary_id,
                text_hash=text_hash,
                audio_path=None,
                duration=None,
                size_bytes=None,
                created_at=now_utc,
                last_accessed=now_utc,
            )
            .on_conflict_do_nothing(index_elements=["text_hash", "user_id"])
        )
        database_session.execute(insert_statement)

        cache_record = (
            database_session.query(TTSCache)
            .filter(
                TTSCache.user_id == user_id,
                TTSCache.text_hash == text_hash,
            )
            .populate_existing()
            .with_for_update()
            .first()
        )

        if cache_record and cache_record.audio_path and os.path.exists(cache_record.audio_path):
            if summary_id is not None and cache_record.summary_id is None:
                cache_record.summary_id = summary_id
                if doc_id is not None and cache_record.document_id is None:
                    cache_record.document_id = doc_id
            cache_record.last_accessed = datetime.now(timezone.utc)
            database_session.commit()
            return {
                "text_hash": text_hash,
                "audio_path": cache_record.audio_path,
                "cached": True,
            }

        target_cache_id = cache_record.id
        register_producer(user_id, text_hash)
        try:
            database_session.commit()

            response = httpx.post(
                f"{settings.piper_tts_url}/tts",
                json={
                    "text": text[:5000],
                    "voice": resolved_voice,
                    "speed": speed,
                },
                timeout=180,
            )

            if response.status_code == 200:
                output_directory = os.path.join(settings.data_dir, "tts-cache")
                os.makedirs(output_directory, exist_ok=True)
                unique_temporary_suffix = uuid.uuid4().hex
                temporary_file_path = os.path.join(
                    output_directory,
                    f".{target_cache_id}_{text_hash}.{unique_temporary_suffix}.tmp",
                )
                final_file_path = os.path.join(
                    output_directory,
                    f"{target_cache_id}_{text_hash}.wav",
                )

                with open(temporary_file_path, "wb") as output_stream:
                    output_stream.write(response.content)

                can_write, summary_id = _lock_cache_write(
                    database_session, user_id, doc_id, summary_id
                )
                if not can_write:
                    if os.path.exists(temporary_file_path):
                        try:
                            os.remove(temporary_file_path)
                        except OSError:
                            pass
                    return {"text_hash": text_hash, "status": "not_accessible"}

                cache_record = (
                    database_session.query(TTSCache)
                    .filter(TTSCache.id == target_cache_id)
                    .populate_existing()
                    .with_for_update()
                    .first()
                )

                if cache_record:
                    if cache_record.audio_path and os.path.exists(cache_record.audio_path):
                        if os.path.exists(temporary_file_path):
                            try:
                                os.remove(temporary_file_path)
                            except OSError:
                                pass
                        if summary_id is not None and cache_record.summary_id is None:
                            cache_record.summary_id = summary_id
                            if doc_id is not None and cache_record.document_id is None:
                                cache_record.document_id = doc_id
                        cache_record.last_accessed = datetime.now(timezone.utc)
                        database_session.commit()
                        return {
                            "text_hash": text_hash,
                            "audio_path": cache_record.audio_path,
                            "cached": True,
                        }

                    os.replace(temporary_file_path, final_file_path)
                    cache_record.document_id = doc_id
                    cache_record.summary_id = summary_id
                    cache_record.audio_path = final_file_path
                    cache_record.duration = _wav_duration(final_file_path)
                    cache_record.size_bytes = len(response.content)
                    cache_record.last_accessed = datetime.now(timezone.utc)
                    database_session.commit()
                    return {
                        "text_hash": text_hash,
                        "audio_path": final_file_path,
                        "cached": False,
                    }
                else:
                    if os.path.exists(temporary_file_path):
                        try:
                            os.remove(temporary_file_path)
                        except OSError:
                            pass
                    database_session.rollback()
                    return {"text_hash": text_hash, "status": "not_accessible"}
            else:
                raise RuntimeError(f"Piper TTS failed: {response.status_code}")
        finally:
            unregister_producer(user_id, text_hash)

    except Exception as task_error:
        if temporary_file_path and os.path.exists(temporary_file_path):
            try:
                os.remove(temporary_file_path)
            except OSError:
                pass
        logger.error(f"TTS generation failed: {task_error}")
        raise self.retry(exc=task_error, countdown=60)
    finally:
        database_session.close()


@celery_app.task
def evict_tts_cache(db: Optional[Session] = None) -> dict:
    """Bounded automatic TTS cache cleanup and expiry."""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    try:
        now_timestamp = time.time()
        grace_cutoff_timestamp = now_timestamp - ACTIVE_PLAYBACK_GRACE_SECONDS
        max_age_days = getattr(settings, "tts_cache_max_age_days", 7)
        ttl_cutoff_timestamp = now_timestamp - (max_age_days * 86400)
        unassociated_cutoff_timestamp = now_timestamp - max(
            ACTIVE_PLAYBACK_GRACE_SECONDS, ACTIVE_COUNTER_TTL_SECONDS
        )

        cache_directory = os.path.realpath(os.path.join(settings.data_dir, "tts-cache"))
        os.makedirs(cache_directory, exist_ok=True)

        stale_tmp_removed = 0
        orphans_removed = 0
        stale_reading_swept = 0
        missing_pruned = 0
        expired_ttl = 0
        evicted_lru = 0
        unsized_count = 0

        try:
            for directory_entry in os.scandir(cache_directory):
                if (
                    directory_entry.name.startswith(".")
                    and directory_entry.name.endswith(".tmp")
                    and directory_entry.is_file()
                ):
                    parsed_cache_id, parsed_text_hash = _parse_entry_name(directory_entry.name)
                    is_live, is_associated = _is_entry_producing_or_protected(
                        db, parsed_cache_id, parsed_text_hash
                    )
                    if is_live:
                        continue
                    effective_cutoff = (
                        grace_cutoff_timestamp if is_associated else unassociated_cutoff_timestamp
                    )
                    try:
                        if directory_entry.stat().st_mtime < effective_cutoff:
                            os.remove(directory_entry.path)
                            stale_tmp_removed += 1
                    except OSError:
                        pass
        except OSError as scan_error:
            logger.warning(
                f"evict_tts_cache: failed scanning tmp files in {cache_directory}: {scan_error}"
            )

        all_rows = db.query(TTSCache).populate_existing().with_for_update().all()
        for row in all_rows:
            if row.audio_path and os.path.exists(row.audio_path):
                if row.size_bytes is None or row.size_bytes <= 0:
                    try:
                        row.size_bytes = os.path.getsize(row.audio_path)
                        if not row.duration:
                            row.duration = _wav_duration(row.audio_path)
                        unsized_count += 1
                    except OSError:
                        pass
            else:
                created_ts = _to_timestamp(row.created_at)
                if created_ts is not None and created_ts < grace_cutoff_timestamp:
                    if not is_producing(row.user_id, row.text_hash) and not is_chunk_protected(
                        row.id, row.user_id, row.text_hash
                    ):
                        db.delete(row)
                        missing_pruned += 1

        valid_paths = set()
        for path_row in db.query(TTSCache.audio_path).filter(TTSCache.audio_path.isnot(None)).all():
            if path_row[0]:
                valid_paths.add(os.path.realpath(path_row[0]))

        try:
            for directory_entry in os.scandir(cache_directory):
                if directory_entry.name.endswith(".wav") and directory_entry.is_file():
                    real_file_path = os.path.realpath(directory_entry.path)
                    if real_file_path not in valid_paths:
                        parsed_cache_id, parsed_text_hash = _parse_entry_name(directory_entry.name)
                        is_live, is_associated = _is_entry_producing_or_protected(
                            db, parsed_cache_id, parsed_text_hash
                        )
                        if is_live:
                            continue
                        effective_cutoff = (
                            grace_cutoff_timestamp
                            if is_associated
                            else unassociated_cutoff_timestamp
                        )
                        try:
                            if directory_entry.stat().st_mtime < effective_cutoff:
                                os.remove(directory_entry.path)
                                orphans_removed += 1
                        except OSError:
                            pass
        except OSError as scan_error:
            logger.warning(
                f"evict_tts_cache: failed scanning orphaned files in {cache_directory}: {scan_error}"
            )

        ttl_paths_to_remove = []
        remaining_rows = db.query(TTSCache).populate_existing().with_for_update().all()
        for row in remaining_rows:
            last_ts = _to_timestamp(row.last_accessed) or _to_timestamp(row.created_at)
            if last_ts is not None and last_ts < ttl_cutoff_timestamp:
                if last_ts >= grace_cutoff_timestamp or is_chunk_protected(
                    row.id, row.user_id, row.text_hash
                ):
                    continue
                if row.audio_path and os.path.exists(row.audio_path):
                    ttl_paths_to_remove.append(row.audio_path)
                db.delete(row)
                expired_ttl += 1

        reading_cutoff_timestamp = now_timestamp - 7200
        reading_rows = (
            db.query(TTSCache)
            .filter(TTSCache.summary_id.is_(None))
            .populate_existing()
            .with_for_update()
            .all()
        )
        stale_reading_paths = []
        for reading_row in reading_rows:
            last_reading_ts = _to_timestamp(reading_row.last_accessed) or _to_timestamp(
                reading_row.created_at
            )
            if last_reading_ts is not None and last_reading_ts < reading_cutoff_timestamp:
                if not is_chunk_protected(
                    reading_row.id, reading_row.user_id, reading_row.text_hash
                ) and not is_producing(reading_row.user_id, reading_row.text_hash):
                    if reading_row.audio_path and os.path.exists(reading_row.audio_path):
                        stale_reading_paths.append(reading_row.audio_path)
                    db.delete(reading_row)
                    stale_reading_swept += 1

        max_bytes = settings.tts_cache_max_gib * (1024**3)
        total_cache_bytes = db.query(func.coalesce(func.sum(TTSCache.size_bytes), 0)).scalar() or 0
        lru_paths_to_remove = []

        if total_cache_bytes > max_bytes:
            candidates = (
                db.query(TTSCache)
                .order_by(TTSCache.last_accessed.asc().nullsfirst())
                .populate_existing()
                .with_for_update()
                .all()
            )
            for row in candidates:
                if total_cache_bytes <= max_bytes:
                    break
                last_ts = _to_timestamp(row.last_accessed) or _to_timestamp(row.created_at)
                if last_ts is not None and last_ts >= grace_cutoff_timestamp:
                    continue
                if is_chunk_protected(row.id, row.user_id, row.text_hash):
                    continue
                if row.audio_path and os.path.exists(row.audio_path):
                    lru_paths_to_remove.append(row.audio_path)
                total_cache_bytes -= row.size_bytes or 0
                db.delete(row)
                evicted_lru += 1

        db.commit()

        for dead_path in ttl_paths_to_remove + stale_reading_paths + lru_paths_to_remove:
            safe_unlink_audio(dead_path)

        total_cache_bytes = db.query(func.coalesce(func.sum(TTSCache.size_bytes), 0)).scalar() or 0
        return {
            "evicted": evicted_lru + expired_ttl,
            "evicted_lru": evicted_lru,
            "expired_ttl": expired_ttl,
            "orphans_removed": orphans_removed,
            "stale_tmp_removed": stale_tmp_removed,
            "missing_pruned": missing_pruned,
            "stale_reading_swept": stale_reading_swept,
            "backfilled": unsized_count,
            "total_bytes": int(total_cache_bytes),
        }
    finally:
        if should_close:
            db.close()
