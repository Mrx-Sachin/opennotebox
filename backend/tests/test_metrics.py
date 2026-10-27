"""Unit tests for metrics primitives and registry.

Tests thread safety, bounded capacity, percentile accuracy, and snapshot
structure without touching any external service.
"""
from __future__ import annotations

import threading
import time
from typing import List

from app.services.metrics import (
    _AtomicCounter,
    _AtomicGauge,
    _SlidingHistogram,
    _RingBuffer,
    get_metrics,
)


class TestAtomicCounter:
    def test_initial_value(self) -> None:
        c = _AtomicCounter()
        assert c.value == 0

    def test_increment_by_one(self) -> None:
        c = _AtomicCounter()
        c.increment()
        assert c.value == 1

    def test_increment_by_n(self) -> None:
        c = _AtomicCounter()
        c.increment(5)
        assert c.value == 5

    def test_multiple_increments(self) -> None:
        c = _AtomicCounter()
        for _ in range(100):
            c.increment()
        assert c.value == 100

    def test_thread_safety(self) -> None:
        """Concurrent increments from 10 threads must not lose counts."""
        c = _AtomicCounter()
        num_threads = 10
        increments_per_thread = 10_000

        def inc() -> None:
            for _ in range(increments_per_thread):
                c.increment()

        threads = [threading.Thread(target=inc) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert c.value == num_threads * increments_per_thread


class TestAtomicGauge:
    def test_initial_value(self) -> None:
        g = _AtomicGauge()
        assert g.value == 0.0

    def test_set_and_read(self) -> None:
        g = _AtomicGauge()
        g.set(42.5)
        assert g.value == 42.5

    def test_overwrite(self) -> None:
        g = _AtomicGauge()
        g.set(1.0)
        g.set(2.0)
        assert g.value == 2.0

    def test_negative_values(self) -> None:
        g = _AtomicGauge()
        g.set(-5.0)
        assert g.value == -5.0

    def test_thread_safety(self) -> None:
        """Concurrent sets should not corrupt the value."""
        g = _AtomicGauge()
        errors: List[str] = []

        def set_val(v: float) -> None:
            try:
                for _ in range(1000):
                    g.set(v)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=set_val, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Final value should be one of the values we set
        assert 0.0 <= g.value <= 9.0


class TestSlidingHistogram:
    def test_empty_snapshot(self) -> None:
        h = _SlidingHistogram(maxlen=100)
        snap = h.snapshot()
        assert snap["count"] == 0
        assert snap["min"] == 0
        assert snap["max"] == 0
        assert snap["mean"] == 0

    def test_single_observation(self) -> None:
        h = _SlidingHistogram(maxlen=100)
        h.observe(42.0)
        snap = h.snapshot()
        assert snap["count"] == 1
        assert snap["min"] == 42.0
        assert snap["max"] == 42.0
        assert snap["mean"] == 42.0
        assert snap["p50"] == 42.0
        assert snap["p99"] == 42.0

    def test_percentiles(self) -> None:
        h = _SlidingHistogram(maxlen=1000)
        values = list(range(1, 101))  # 1..100
        for v in values:
            h.observe(float(v))
        snap = h.snapshot()
        assert snap["count"] == 100
        assert snap["min"] == 1.0
        assert snap["max"] == 100.0
        assert snap["mean"] == 50.5
        # p50 should be ~50
        assert 49.0 <= snap["p50"] <= 51.0
        # p90 should be ~90
        assert 89.0 <= snap["p90"] <= 91.0
        # p99 should be ~99
        assert 98.0 <= snap["p99"] <= 100.0

    def test_bounded_capacity(self) -> None:
        """Old observations must be evicted once maxlen is reached."""
        h = _SlidingHistogram(maxlen=10)
        for v in range(20):
            h.observe(float(v))
        snap = h.snapshot()
        # Only last 10 should remain: 10..19
        assert snap["min"] == 10.0
        assert snap["max"] == 19.0
        assert snap["count"] == 20  # Total count never resets

    def test_thread_safety(self) -> None:
        """Concurrent observations should not lose data."""
        h = _SlidingHistogram(maxlen=100_000)
        num_threads = 10
        per_thread = 1000

        def obs() -> None:
            for _ in range(per_thread):
                h.observe(1.0)

        threads = [threading.Thread(target=obs) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = h.snapshot()
        assert snap["count"] == num_threads * per_thread

    def test_observe_zero(self) -> None:
        h = _SlidingHistogram(maxlen=100)
        h.observe(0.0)
        snap = h.snapshot()
        assert snap["min"] == 0.0
        assert snap["count"] == 1


class TestRingBuffer:
    def test_empty(self) -> None:
        rb = _RingBuffer(maxlen=10)
        assert rb.length == 0
        assert rb.snapshot() == []

    def test_push_and_snapshot(self) -> None:
        rb = _RingBuffer(maxlen=10)
        rb.push({"event": "test"})
        assert rb.length == 1
        snap = rb.snapshot()
        assert len(snap) == 1
        assert snap[0]["event"] == "test"
        assert "ts" in snap[0]

    def test_bounded_capacity(self) -> None:
        rb = _RingBuffer(maxlen=5)
        for i in range(10):
            rb.push({"idx": i})
        assert rb.length == 5
        snap = rb.snapshot()
        assert snap[0]["idx"] == 5
        assert snap[-1]["idx"] == 9

    def test_snapshot_last_n(self) -> None:
        rb = _RingBuffer(maxlen=100)
        for i in range(20):
            rb.push({"idx": i})
        snap = rb.snapshot(last_n=5)
        assert len(snap) == 5
        assert snap[0]["idx"] == 15
        assert snap[-1]["idx"] == 19

    def test_timestamp_monotonic(self) -> None:
        rb = _RingBuffer(maxlen=10)
        rb.push({"a": 1})
        time.sleep(0.01)
        rb.push({"a": 2})
        snap = rb.snapshot()
        assert snap[0]["ts"] < snap[1]["ts"]


class TestMetricsRegistry:
    def test_singleton(self) -> None:
        """get_metrics should return the same instance."""
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2

    def test_snapshot_structure(self) -> None:
        """Snapshot should contain all expected top-level keys."""
        m = get_metrics()
        snap = m.snapshot()
        expected_keys = {
            "http", "database", "llm", "rag", "celery",
            "websocket", "tts", "embeddings", "system",
        }
        assert set(snap.keys()) == expected_keys

    def test_http_counters(self) -> None:
        m = get_metrics()
        m.http_requests.increment(5)
        snap = m.snapshot()
        assert snap["http"]["total_requests"] == 5

    def test_http_latency(self) -> None:
        m = get_metrics()
        m.http_latency.observe(150.0)
        snap = m.snapshot()
        assert snap["http"]["latency_ms"]["count"] == 1
        assert snap["http"]["latency_ms"]["mean"] == 150.0

    def test_db_pool_gauges(self) -> None:
        m = get_metrics()
        m.db_pool_size.set(10)
        m.db_pool_checkedout.set(3)
        snap = m.snapshot()
        assert snap["database"]["pool"]["size"] == 10
        assert snap["database"]["pool"]["checked_out"] == 3

    def test_llm_tracking(self) -> None:
        m = get_metrics()
        m.llm_requests.increment()
        m.llm_tokens_in.increment(100)
        m.llm_tokens_out.increment(200)
        snap = m.snapshot()
        assert snap["llm"]["total_requests"] == 1
        assert snap["llm"]["tokens_in"] == 100
        assert snap["llm"]["tokens_out"] == 200

    def test_rag_tracking(self) -> None:
        m = get_metrics()
        m.rag_requests.increment()
        m.rag_latency.observe(50.0)
        m.rag_chunk_count.observe(8)
        snap = m.snapshot()
        assert snap["rag"]["total_requests"] == 1
        assert snap["rag"]["chunk_count"]["count"] == 1

    def test_celery_tracking(self) -> None:
        m = get_metrics()
        m.celery_tasks_submitted.increment(3)
        m.celery_tasks_succeeded.increment(2)
        m.celery_tasks_failed.increment(1)
        snap = m.snapshot()
        assert snap["celery"]["submitted"] == 3
        assert snap["celery"]["succeeded"] == 2
        assert snap["celery"]["failed"] == 1

    def test_tts_tracking(self) -> None:
        m = get_metrics()
        m.tts_requests.increment()
        m.tts_cache_hits.increment()
        snap = m.snapshot()
        assert snap["tts"]["requests"] == 1
        assert snap["tts"]["cache_hits"] == 1

    def test_system_gauges(self) -> None:
        m = get_metrics()
        m.sys_cpu_percent.set(75.5)
        m.sys_memory_used_mb.set(512.0)
        snap = m.snapshot()
        assert snap["system"]["cpu_percent"] == 75.5
        assert snap["system"]["memory_used_mb"] == 512.0

    def test_uptime_positive(self) -> None:
        m = get_metrics()
        snap = m.snapshot()
        assert snap["system"]["uptime_seconds"] >= 0

    def test_http_recent_ring_buffer(self) -> None:
        m = get_metrics()
        m.http_recent.push({"method": "GET", "path": "/test", "status": 200})
        snap = m.snapshot()
        recent = snap["http"]["recent"]
        assert len(recent) == 1
        assert recent[0]["method"] == "GET"
