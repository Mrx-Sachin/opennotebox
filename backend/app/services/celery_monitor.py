"""Celery task instrumentation.

Provides signal handlers and a stats collector that writes Celery worker
metrics to the shared registry. Designed to be imported by celery_app.py
and auto-registered via Celery signals.
"""
import threading

from celery.signals import (
    task_prerun,
    task_postrun,
    task_success,
    task_failure,
    task_retry,
    worker_shutdown,
)
from loguru import logger

from app.services.metrics import get_metrics

# ------------------------------------------------------------------
# Background infrastructure metrics (queue depths, worker count)

# These queries are expensive: _get_queue_depths() opens a Redis connection,
# _get_worker_count() calls inspect.ping() with a 5s timeout. Running them
# on every task signal would add seconds of latency to every Celery task.
# Instead a single background thread refreshes them every 15s.


def _refresh_infrastructure_metrics():
    """Query Redis for queue depths and worker count. Intended to run in a
    background thread - exceptions are swallowed so a transient Redis issue
    does not crash the daemon."""
    try:
        import redis
        from app.config import settings

        r = redis.from_url(settings.celery_broker_url, decode_responses=True)
        try:
            default_depth = r.llen("default")
            tts_depth = r.llen("tts")
            m = get_metrics()
            m.celery_queue_depth_default.set(default_depth)
            m.celery_queue_depth_tts.set(tts_depth)
        finally:
            r.close()
    except Exception:
        pass

    try:
        from app.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=5)
        ping_result = inspect.ping()
        if ping_result:
            m = get_metrics()
            m.celery_workers_online.set(len(ping_result))
    except Exception:
        pass


_infra_stopped = threading.Event()


def _infra_loop():
    while not _infra_stopped.wait(15):
        _refresh_infrastructure_metrics()


def _ensure_infra_thread():
    """Start the background metrics refresh thread (once)."""
    if not getattr(_ensure_infra_thread, "_started", False):
        _ensure_infra_thread._started = True
        t = threading.Thread(target=_infra_loop, daemon=True, name="celery-metrics")
        t.start()


@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, task=None, args=None, **kwargs):
    _ensure_infra_thread()
    m = get_metrics()
    m.celery_tasks_active.increment()
    m.celery_tasks_submitted.increment()


@task_postrun.connect
def on_task_postrun(sender=None, task_id=None, task=None, args=None, **kwargs):
    m = get_metrics()
    m.celery_tasks_active.decrement()


@task_success.connect
def on_task_success(sender=None, result=None, task_id=None, **kwargs):
    m = get_metrics()
    m.celery_tasks_succeeded.increment()


@task_failure.connect
def on_task_failure(sender=None, exception=None, task_id=None, **kwargs):
    m = get_metrics()
    m.celery_tasks_failed.increment()


@task_retry.connect
def on_task_retry(sender=None, reason=None, task_id=None, **kwargs):
    m = get_metrics()
    m.celery_tasks_retried.increment()


@worker_shutdown.connect
def on_worker_shutdown(sender=None, **kwargs):
    logger.info("Celery worker shutting down - final metrics snapshot:")
    m = get_metrics()
    snap = m.snapshot()
    logger.info(f"  Celery tasks: submitted={snap['celery']['submitted']} "
                f"succeeded={snap['celery']['succeeded']} "
                f"failed={snap['celery']['failed']}")


def celery_stats_snapshot():
    """Return a snapshot of Celery-specific stats (used by the metrics router)."""
    _refresh_infrastructure_metrics()
    m = get_metrics()
    return m.snapshot()["celery"]
