"""Celery application configuration."""
from celery import Celery
from app.config import settings

# Import signal handlers so Celery registers them before the first task runs
import app.services.celery_monitor  # noqa: F401

celery_app = Celery(
    "open_notebox",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_always_eager=False,
    worker_max_tasks_per_child=100,
    # worker_max_tasks_per_child only recycles a child after 100 *tasks* - it
    # does nothing for the single task that itself balloons memory. Observed
    # via docker stats: pdfplumber/pdfminer parsing a large PDF leaves a
    # worker child holding multiple GB that's never freed back to the OS
    # (glibc/musl allocator arenas don't return large blocks), so the process
    # sits there bloated at idle until 100 tasks accumulate. This is Celery's
    # own memory-based recycling knob - note the unit is KiB, not bytes/MB.
    # 786432 KiB (768MB) leaves headroom for a legitimate few-hundred-MB PDF
    # parse to finish without self-recycling mid-task, while still capping
    # any single child well below the multi-GB blowups we measured.
    worker_max_memory_per_child=786432,
    task_soft_time_limit=600,
    task_time_limit=900,
    task_default_retry_delay=60,
    task_max_retries=3,
    broker_connection_retry_on_startup=True,
    # Without these, a task whose worker process is killed mid-run (hard time
    # limit, OOM, container restart) is simply lost: Celery had already acked
    # it off the broker, so it never retries and any DB row it created stays
    # stuck at "generating"/"pending" forever. This was the actual mechanism
    # behind page summaries silently never finishing for large documents.
    # With acks_late + reject_on_worker_lost, a task that dies mid-run gets
    # redelivered to another worker instead of vanishing. worker_prefetch_multiplier=1
    # is set so a lost task doesn't take a batch of prefetched sibling tasks with it.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    imports=[
        "app.tasks.pdf_tasks",
        "app.tasks.summary_tasks",
        "app.tasks.kg_tasks",
        "app.tasks.tts_tasks",
        "app.tasks.doc_tasks",
        "app.tasks.epub_tasks",
        "app.tasks.podcast_tasks",
        "app.tasks.flashcard_tasks",
        "app.tasks.export_tasks",
        "app.tasks.embedding_tasks",
        "app.tasks.preview_tasks",
        "app.tasks.asset_tasks",
    ],
    # TTS/podcast synthesis is interactive-latency-sensitive (a user waiting on
    # narration) but a bulk document re-parse or batch page-summary job can
    # occupy every worker slot for minutes. Without a dedicated queue, those
    # batch jobs starve narration behind them even though narration is the
    # thing a human is actively waiting on. Everything else stays on the
    # default queue - only tts_tasks/podcast_tasks move. A worker must be
    # started consuming each queue for the split to take effect; see
    # docker-compose.yml.
    #
    # task_default_queue MUST be set explicitly. Celery's own implicit default
    # queue is named "celery", not "default", so a worker started with
    # "-Q default" consumes a queue nothing ever publishes to: uploads were
    # accepted, tasks were enqueued to "celery", and documents sat at
    # processing_status="pending" forever with an idle worker and a silent log.
    task_default_queue="default",
    task_routes={
        "app.tasks.tts_tasks.*": {"queue": "tts"},
        "app.tasks.podcast_tasks.*": {"queue": "tts"},
    },
    # Runs evict_tts_cache hourly to keep on-disk TTS audio under
    # settings.tts_cache_max_gib. Lives on the "tts" queue like the tasks it
    # cleans up after, so it needs a worker consuming that queue to fire -
    # same requirement as the routing above.
    beat_schedule={
        "evict-tts-cache-hourly": {
            "task": "app.tasks.tts_tasks.evict_tts_cache",
            "schedule": 3600.0,
            "options": {"queue": "tts"},
        },
        # Resyncs the opt-in export-to-folder mirror for every user with it
        # enabled. Stays on the default queue (unlike the tts/podcast tasks
        # above) - it's plain file I/O against Postgres data already at
        # hand, not GPU/TTS-latency-sensitive work that narration would be
        # starved behind.
        "sync-exports-hourly": {
            "task": "app.tasks.export_tasks.sync_exports",
            "schedule": 3600.0,
        },
    },
)
