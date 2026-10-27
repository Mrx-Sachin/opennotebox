"""Process-level metrics registry for OpenNoteBox.

Thread-safe, in-process counters, gauges, histograms, and ring buffers.
No external dependencies (Prometheus push, OpenTelemetry, etc) - this is a
self-contained monitoring layer that reports JSON over the /api/metrics
endpoint. The goal is observability of the deployment's own internals without
adding a new service dependency that itself becomes a failure mode.

Design notes:
- All structures are protected by a single asyncio.Lock for the async
  collectors and a threading.Lock for the sync-safe primitives. In practice
  the lock is held for microseconds (dict mutations) so contention is
  negligible even under heavy load.
- Histograms use a sliding window of recent values (last 1000 samples per
  bucket) to compute percentiles without unbounded memory growth.
- Ring buffers have a fixed maximum length; old entries are dropped when
  full, so a spike that happened an hour ago disappears and the buffer
  reflects current system behavior.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

class _AtomicCounter:
    """Monotonically increasing counter, safe across threads."""
    __slots__ = ("_val", "_lock")

    def __init__(self) -> None:
        self._val = 0
        self._lock = threading.Lock()

    def increment(self, n: int = 1) -> None:
        with self._lock:
            self._val += n

    @property
    def value(self) -> int:
        with self._lock:
            return self._val


class _AtomicGauge:
    """Thread-safe gauge with atomic increment/decrement."""
    __slots__ = ("_val", "_lock")

    def __init__(self) -> None:
        self._val: float = 0.0
        self._lock = threading.Lock()

    def set(self, val: float) -> None:
        with self._lock:
            self._val = val

    def increment(self, n: float = 1.0) -> None:
        with self._lock:
            self._val += n

    def decrement(self, n: float = 1.0) -> None:
        with self._lock:
            self._val -= n

    @property
    def value(self) -> float:
        with self._lock:
            return self._val


class _SlidingHistogram:
    """Bounded histogram that keeps last `maxlen` observations per bucket.

    Percentiles are computed on the fly from the sorted observations, so
    there is no approximation error. The bounded deque means memory does not
    grow unboundedly.
    """
    __slots__ = ("_values", "_lock", "_count")

    def __init__(self, maxlen: int = 1000) -> None:
        self._values: Deque[float] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._count = _AtomicCounter()

    def observe(self, value: float) -> None:
        with self._lock:
            self._values.append(value)
        self._count.increment()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            vals = list(self._values)
        count = self._count.value
        n = len(vals)
        if n == 0:
            return {"count": count, "min": 0, "max": 0, "mean": 0,
                    "p50": 0, "p90": 0, "p95": 0, "p99": 0}
        # nth_element via sort is fastest in pure Python for n < 1000.
        # Keeping the deque bounded means this is O(n log n) on <=1000 items.
        vals.sort()
        s = sum(vals)
        return {
            "count": count,
            "min": round(vals[0], 3),
            "max": round(vals[-1], 3),
            "mean": round(s / n, 3),
            "p50": round(vals[int(n * 0.50)], 3),
            "p90": round(vals[int(n * 0.90)], 3),
            "p95": round(vals[int(n * 0.95)], 3),
            "p99": round(vals[int(n * 0.99)], 3),
        }


class _RingBuffer:
    """Fixed-capacity ring buffer for recent events (active requests, errors)."""
    __slots__ = ("_entries", "_lock")

    def __init__(self, maxlen: int = 200) -> None:
        self._entries: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._entries.append({**entry, "ts": time.time()})

    def snapshot(self, last_n: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._lock:
            n = len(self._entries)
            if last_n and last_n < n:
                # Copy only the tail to avoid materializing the full buffer.
                entries = [self._entries[-(last_n - i)] for i in range(last_n)]
            else:
                entries = list(self._entries)
        return entries

    @property
    def length(self) -> int:
        with self._lock:
            return len(self._entries)


# ---------------------------------------------------------------------------
# Registry - the singleton the rest of the app imports
# ---------------------------------------------------------------------------

class MetricsRegistry:
    """Central metrics registry.

    Usage:
        m = get_metrics()
        m.http_requests.increment()
        m.http_latency.observe(0.342)
    """

    def __init__(self) -> None:
        # --- HTTP ---
        self.http_requests = _AtomicCounter()
        self.http_errors = _AtomicCounter()
        self.http_active = _AtomicGauge()
        self.http_latency = _SlidingHistogram(maxlen=2000)
        self.http_recent: _RingBuffer = _RingBuffer(maxlen=200)

        # --- Database ---
        self.db_pool_size = _AtomicGauge()
        self.db_pool_checkedout = _AtomicGauge()
        self.db_pool_overflow = _AtomicGauge()
        self.db_pool_idle = _AtomicGauge()
        self.db_pool_pending = _AtomicGauge()
        self.db_query_latency = _SlidingHistogram(maxlen=1000)
        self.db_errors = _AtomicCounter()
        self.db_connection_time = _SlidingHistogram(maxlen=500)

        # --- LLM ---
        self.llm_requests = _AtomicCounter()
        self.llm_errors = _AtomicCounter()
        self.llm_latency = _SlidingHistogram(maxlen=500)
        self.llm_tokens_in = _AtomicCounter()
        self.llm_tokens_out = _AtomicCounter()
        self.llm_active = _AtomicGauge()
        self.llm_recent: _RingBuffer = _RingBuffer(maxlen=100)

        # --- RAG Retrieval ---
        self.rag_requests = _AtomicCounter()
        self.rag_latency = _SlidingHistogram(maxlen=500)
        self.rag_bm25_latency = _SlidingHistogram(maxlen=500)
        self.rag_semantic_latency = _SlidingHistogram(maxlen=500)
        self.rag_embed_latency = _SlidingHistogram(maxlen=500)
        self.rag_errors = _AtomicCounter()
        self.rag_chunk_count = _SlidingHistogram(maxlen=500)

        # --- Celery ---
        self.celery_tasks_submitted = _AtomicCounter()
        self.celery_tasks_succeeded = _AtomicCounter()
        self.celery_tasks_failed = _AtomicCounter()
        self.celery_tasks_active = _AtomicGauge()
        self.celery_tasks_reserved = _AtomicGauge()
        self.celery_tasks_retried = _AtomicCounter()
        self.celery_task_latency = _SlidingHistogram(maxlen=500)
        self.celery_queue_depth_default = _AtomicGauge()
        self.celery_queue_depth_tts = _AtomicGauge()
        self.celery_workers_online = _AtomicGauge()

        # --- WebSocket ---
        self.ws_connections = _AtomicGauge()
        self.ws_messages_in = _AtomicCounter()
        self.ws_messages_out = _AtomicCounter()
        self.ws_errors = _AtomicCounter()

        # --- TTS ---
        self.tts_requests = _AtomicCounter()
        self.tts_cache_hits = _AtomicCounter()
        self.tts_cache_misses = _AtomicCounter()
        self.tts_latency = _SlidingHistogram(maxlen=500)
        self.tts_errors = _AtomicCounter()

        # --- System ---
        self.sys_cpu_percent = _AtomicGauge()
        self.sys_memory_used_mb = _AtomicGauge()
        self.sys_memory_percent = _AtomicGauge()
        self.sys_disk_used_pct = _AtomicGauge()
        self.sys_uptime_seconds = _AtomicGauge()

        # --- Embeddings ---
        self.embed_requests = _AtomicCounter()
        self.embed_latency = _SlidingHistogram(maxlen=500)
        self.embed_errors = _AtomicCounter()

        # Startup
        self._start_time = time.time()

    def snapshot(self) -> Dict[str, Any]:
        """Return a full snapshot of all metrics as a nested dict."""
        return {
            "http": {
                "total_requests": self.http_requests.value,
                "total_errors": self.http_errors.value,
                "active": int(self.http_active.value),
                "latency_ms": self.http_latency.snapshot(),
                "recent": self.http_recent.snapshot(last_n=20),
            },
            "database": {
                "pool": {
                    "size": int(self.db_pool_size.value),
                    "checked_out": int(self.db_pool_checkedout.value),
                    "overflow": int(self.db_pool_overflow.value),
                    "idle": int(self.db_pool_idle.value),
                    "pending": int(self.db_pool_pending.value),
                },
                "query_latency_ms": self.db_query_latency.snapshot(),
                "connection_time_ms": self.db_connection_time.snapshot(),
                "errors": self.db_errors.value,
            },
            "llm": {
                "total_requests": self.llm_requests.value,
                "total_errors": self.llm_errors.value,
                "active": int(self.llm_active.value),
                "latency_ms": self.llm_latency.snapshot(),
                "tokens_in": self.llm_tokens_in.value,
                "tokens_out": self.llm_tokens_out.value,
                "recent": self.llm_recent.snapshot(last_n=10),
            },
            "rag": {
                "total_requests": self.rag_requests.value,
                "errors": self.rag_errors.value,
                "latency_ms": self.rag_latency.snapshot(),
                "bm25_latency_ms": self.rag_bm25_latency.snapshot(),
                "semantic_latency_ms": self.rag_semantic_latency.snapshot(),
                "embed_latency_ms": self.rag_embed_latency.snapshot(),
                "chunk_count": self.rag_chunk_count.snapshot(),
            },
            "celery": {
                "submitted": self.celery_tasks_submitted.value,
                "succeeded": self.celery_tasks_succeeded.value,
                "failed": self.celery_tasks_failed.value,
                "retried": self.celery_tasks_retried.value,
                "active": int(self.celery_tasks_active.value),
                "reserved": int(self.celery_tasks_reserved.value),
                "workers_online": int(self.celery_workers_online.value),
                "task_latency_ms": self.celery_task_latency.snapshot(),
                "queues": {
                    "default": int(self.celery_queue_depth_default.value),
                    "tts": int(self.celery_queue_depth_tts.value),
                },
            },
            "websocket": {
                "connections": int(self.ws_connections.value),
                "messages_in": self.ws_messages_in.value,
                "messages_out": self.ws_messages_out.value,
                "errors": self.ws_errors.value,
            },
            "tts": {
                "requests": self.tts_requests.value,
                "cache_hits": self.tts_cache_hits.value,
                "cache_misses": self.tts_cache_misses.value,
                "latency_ms": self.tts_latency.snapshot(),
                "errors": self.tts_errors.value,
            },
            "embeddings": {
                "requests": self.embed_requests.value,
                "latency_ms": self.embed_latency.snapshot(),
                "errors": self.embed_errors.value,
            },
            "system": {
                "cpu_percent": round(self.sys_cpu_percent.value, 1),
                "memory_used_mb": round(self.sys_memory_used_mb.value, 1),
                "memory_percent": round(self.sys_memory_percent.value, 1),
                "disk_used_percent": round(self.sys_disk_used_pct.value, 1),
                "uptime_seconds": round(self.sys_uptime_seconds.value, 0),
            },
        }


# Singleton
_metrics: Optional[MetricsRegistry] = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsRegistry:
    """Return the global metrics registry, creating it lazily."""
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = MetricsRegistry()
    return _metrics
