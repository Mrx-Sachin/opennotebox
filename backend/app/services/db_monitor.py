"""Database connection pool monitor.

Reads SQLAlchemy engine pool stats at an interval and writes them to the
metrics registry. Also provides a context manager for timing individual
queries when explicit instrumentation is desired.
"""
import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text as sql_text
from loguru import logger

from app.database import engine
from app.services.metrics import get_metrics


def update_pool_stats() -> None:
    """Read the current SQLAlchemy pool state and write to metrics."""
    m = get_metrics()
    pool = engine.pool

    # SQLite in-memory engines use SingletonThreadPool which lacks the
    # QueuePool methods (checkedout, size, overflow, checkedin).
    # Guard against this so tests and local SQLite deployments don't crash.
    if not hasattr(pool, "checkedout"):
        return

    # QueuePool.checkedout() = connections currently in use
    # QueuePool.overflow() = current number of *overflow* connections open
    #   (negative means fewer overflow connections than the peak; capped at 0 for display)
    # QueuePool.size() = configured pool_size (not total capacity)
    # checkedin() = idle connections sitting in the pool
    checkedout = pool.checkedout()
    size = pool.size()
    overflow = pool.overflow()
    checkedin = pool.checkedin()

    m.db_pool_size.set(size)
    m.db_pool_checkedout.set(checkedout)
    m.db_pool_overflow.set(max(0, overflow))
    m.db_pool_idle.set(checkedin)
    m.db_pool_pending.set(max(0, checkedout - size))


async def db_pool_monitor(interval: float = 2.0) -> AsyncGenerator[None, None]:
    """Background task that samples pool stats every `interval` seconds."""
    while True:
        try:
            update_pool_stats()
        except Exception as e:
            logger.warning(f"DB pool monitor error: {e}")
        await asyncio.sleep(interval)


@asynccontextmanager
async def timed_query(db):
    """Context manager that records query latency in metrics.

    Usage:
        async with timed_query(db):
            result = db.query(Model).all()
    """
    m = get_metrics()
    start = time.perf_counter()
    try:
        yield
    except Exception:
        m.db_errors.increment()
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        m.db_query_latency.observe(elapsed_ms)


def check_db_health() -> dict:
    """Probe the database with a lightweight query and return health status."""
    m = get_metrics()
    start = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000
        m.db_connection_time.observe(latency_ms)
        update_pool_stats()
        return {
            "status": "healthy",
            "latency_ms": round(latency_ms, 1),
            "pool": {
                "size": int(m.db_pool_size.value),
                "checked_out": int(m.db_pool_checkedout.value),
                "overflow": int(m.db_pool_overflow.value),
                "idle": int(m.db_pool_idle.value),
            },
        }
    except Exception as e:
        m.db_errors.increment()
        return {
            "status": "unhealthy",
            "error": str(e),
            "latency_ms": 0,
        }
