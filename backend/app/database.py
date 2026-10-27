from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

DATABASE_URL = settings.database_url

# Pool sizing is per PROCESS, and this image runs as several processes: the
# API, a 4-child prefork Celery worker, a 2-child TTS worker and beat. Every
# prefork child builds its own engine and therefore its own pool, so the real
# ceiling is (processes x (pool_size + max_overflow)) - not the numbers below.
# At the original 10+20 that came to roughly 240 connections against
# Postgres's default max_connections of 100, which under genuine multi-user
# load fails as "FATAL: sorry, too many clients already" rather than as a
# clean queue. Keeping the per-process pool small and letting SQLAlchemy queue
# briefly is much better behaviour than exhausting the server; the compose
# file also raises max_connections to leave real headroom.
#
# pool_pre_ping guards against connections killed while idle (Postgres restart,
# an idle-timeout proxy) being handed out and failing on first use.
# pool_recycle keeps connections from outliving a server-side idle timeout.
#
# There is intentionally no async engine. Nothing in the app uses
# AsyncSession; a second pool doubled idle connections per process for no
# benefit.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def init_db():
    """Create the data directories the app writes into.

    Schema creation/upgrade is Alembic's job now (``alembic upgrade head``
    runs from the container entrypoint before the API starts), not
    ``create_all`` plus a hand-maintained list of ALTER TABLE statements -
    that combination silently ignored every change to an existing table.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ensure_data_dirs)


def _ensure_data_dirs():
    import os
    os.makedirs(settings.data_dir, exist_ok=True)
    for d in ["uploads", "extracted", "summaries", "tts-cache"]:
        os.makedirs(os.path.join(settings.data_dir, d), exist_ok=True)
