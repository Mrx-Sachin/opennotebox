"""Celery beat task for the hourly "export to folder" resync.

Mirrors tasks/tts_tasks.py::evict_tts_cache's shape: a single scheduled task
opening its own DB session (see celery_app.py's beat_schedule). Deliberately
NOT triggered on every chat message / flashcard grade / podcast completion -
those are interactive paths that were just made fast and cancellable, and
bolting synchronous file I/O onto them would undo that. Instead every
opted-in user gets resynced on a timer, plus routers/export.py offers an
immediate on-demand sync for someone who doesn't want to wait for the next
tick.
"""
from loguru import logger
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models import User, UserSettings
from app.services.export_service import EXPORT_KEY, export_user_data


@celery_app.task
def sync_exports() -> dict:
    """Re-export every user with UserSettings key="export" -> enabled=true."""
    if not settings.export_dir:
        return {"skipped": "export_dir not configured"}

    db: Session = SessionLocal()
    try:
        enabled_user_ids = [
            row.user_id
            for row in db.query(UserSettings).filter(UserSettings.key == EXPORT_KEY).all()
            if (row.value or {}).get("enabled")
        ]

        synced = 0
        errors = 0
        for user_id in enabled_user_ids:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                continue
            try:
                export_user_data(db, user)
                synced += 1
            except Exception as e:
                errors += 1
                logger.error(f"sync_exports: export failed for user {user_id}: {e}")

        return {"users_eligible": len(enabled_user_ids), "users_synced": synced, "errors": errors}
    finally:
        db.close()
