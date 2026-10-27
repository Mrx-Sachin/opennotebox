"""Tests for metrics router endpoints.

Covers: /api/metrics, /api/metrics/summary, /api/metrics/health/deep.
"""
from __future__ import annotations

from unittest.mock import patch


from app.services.metrics import get_metrics


class TestGetMetrics:
    """GET /api/metrics"""

    async def test_metrics_success(self, client, auth_header):
        resp = await client.get("/api/metrics", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert "http" in data
        assert "database" in data
        assert "llm" in data
        assert "rag" in data
        assert "celery" in data
        assert "system" in data

    async def test_metrics_unauthenticated(self, client):
        resp = await client.get("/api/metrics")
        assert resp.status_code == 401

    async def test_metrics_structure_http(self, client, auth_header):
        resp = await client.get("/api/metrics", headers=auth_header)
        data = resp.json()
        http = data["http"]
        assert "total_requests" in http
        assert "total_errors" in http
        assert "active" in http
        assert "latency_ms" in http
        assert "recent" in http

    async def test_metrics_structure_database(self, client, auth_header):
        resp = await client.get("/api/metrics", headers=auth_header)
        data = resp.json()
        db = data["database"]
        assert "pool" in db
        pool = db["pool"]
        assert "size" in pool
        assert "checked_out" in pool
        assert "overflow" in pool

    async def test_metrics_includes_http_latency(self, client, auth_header):
        """After a request, HTTP latency should have at least one observation."""
        # /api/metrics is skipped by the middleware, so hit /health (also skipped)
        # and /api/auth/me which gets tracked
        await client.get("/api/auth/me", headers=auth_header)
        resp = await client.get("/api/metrics", headers=auth_header)
        data = resp.json()
        latency = data["http"]["latency_ms"]
        assert latency["count"] >= 1

    async def test_metrics_http_recent_has_entries(self, client, auth_header):
        """Recent events should populate after requests."""
        await client.get("/api/auth/me", headers=auth_header)
        resp = await client.get("/api/metrics", headers=auth_header)
        data = resp.json()
        recent = data["http"]["recent"]
        assert isinstance(recent, list)
        assert len(recent) >= 1


class TestGetMetricsSummary:
    """GET /api/metrics/summary"""

    async def test_summary_success(self, client, auth_header):
        resp = await client.get("/api/metrics/summary", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert "overall_status" in data
        assert "indicators" in data
        assert data["overall_status"] in ("healthy", "degraded", "unhealthy")

    async def test_summary_unauthenticated(self, client):
        resp = await client.get("/api/metrics/summary")
        assert resp.status_code == 401

    async def test_summary_indicators_present(self, client, auth_header):
        resp = await client.get("/api/metrics/summary", headers=auth_header)
        data = resp.json()
        indicators = data["indicators"]
        expected = {"http", "database", "llm", "celery", "system", "rag", "tts"}
        assert expected.issubset(set(indicators.keys()))

    async def test_summary_http_indicator(self, client, auth_header):
        resp = await client.get("/api/metrics/summary", headers=auth_header)
        data = resp.json()
        http = data["indicators"]["http"]
        assert "status" in http
        assert "error_rate" in http
        assert "p95_latency_ms" in http
        assert "active_requests" in http

    async def test_summary_database_indicator(self, client, auth_header):
        resp = await client.get("/api/metrics/summary", headers=auth_header)
        data = resp.json()
        db = data["indicators"]["database"]
        assert "status" in db
        assert "pool_usage" in db

    async def test_summary_system_indicator(self, client, auth_header):
        resp = await client.get("/api/metrics/summary", headers=auth_header)
        data = resp.json()
        sys_info = data["indicators"]["system"]
        assert "status" in sys_info
        assert "cpu_percent" in sys_info
        assert "memory_mb" in sys_info

    async def test_healthy_when_no_errors(self, client, auth_header):
        """With fresh metrics, overall should be healthy."""
        resp = await client.get("/api/metrics/summary", headers=auth_header)
        data = resp.json()
        # At minimum, shouldn't be unhealthy with no traffic
        assert data["overall_status"] in ("healthy", "degraded")

    async def test_degraded_on_high_error_rate(self, client, auth_header):
        """Inject high error rate and verify degraded status."""
        m = get_metrics()
        m.http_requests.increment(100)
        m.http_errors.increment(6)  # 6% error rate > 5% threshold
        resp = await client.get("/api/metrics/summary", headers=auth_header)
        data = resp.json()
        http_status = data["indicators"]["http"]["status"]
        assert http_status in ("degraded", "unhealthy")


class TestDeepHealthCheck:
    """GET /api/metrics/health/deep"""

    @staticmethod
    def _mock_deep_health():
        """Return a list of started patches that neutralize all network calls.

        Patches check_db_health (fast), check_llm_health (fast), and makes
        inline `import redis` / `import httpx` raise ImportError so the
        endpoint's try/except blocks catch them and return 'unhealthy' dicts.
        The `import celery_app` is handled by the same ImportError trick.
        """
        # ImportOrder: patch __import__ only for the specific modules
        # the endpoint tries to import inline.
        original_import = __import__

        def _side_effect(name, *args, **kwargs):
            if name in ("redis", "httpx"):
                raise ImportError(f"mocked: {name}")
            return original_import(name, *args, **kwargs)

        patches = [
            patch("app.routers.metrics.check_db_health",
                  return_value={"status": "healthy", "latency_ms": 1}),
            patch("app.routers.metrics.check_llm_health",
                  return_value={"status": "healthy", "latency_ms": 10}),
            # Prevent inline imports that would reach real services.
            # Patch app.celery_app import by making it raise.
            patch.dict("sys.modules", {"app.celery_app": None}),
            patch("builtins.__import__", side_effect=_side_effect),
        ]
        for p in patches:
            p.start()
        return patches

    @staticmethod
    def _stop_patches(patches):
        for p in reversed(patches):
            p.stop()

    async def test_deep_health_success(self, client, auth_header):
        patches = self._mock_deep_health()
        try:
            resp = await client.get("/api/metrics/health/deep", headers=auth_header)
            assert resp.status_code == 200
            data = resp.json()
            assert "overall_status" in data
            assert "dependencies" in data
            assert "database" in data["dependencies"]
        finally:
            self._stop_patches(patches)

    async def test_deep_health_unauthenticated(self, client):
        patches = self._mock_deep_health()
        try:
            resp = await client.get("/api/metrics/health/deep")
            assert resp.status_code == 401
        finally:
            self._stop_patches(patches)

    async def test_deep_health_has_database(self, client, auth_header):
        patches = self._mock_deep_health()
        try:
            resp = await client.get("/api/metrics/health/deep", headers=auth_header)
            data = resp.json()
            deps = data["dependencies"]
            assert "database" in deps
            assert deps["database"]["status"] == "healthy"
        finally:
            self._stop_patches(patches)

    async def test_deep_health_redis_unhealthy_when_mocked(self, client, auth_header):
        patches = self._mock_deep_health()
        try:
            resp = await client.get("/api/metrics/health/deep", headers=auth_header)
            data = resp.json()
            deps = data["dependencies"]
            # Redis import fails, so status should be unhealthy
            assert "redis" in deps
            assert deps["redis"]["status"] == "unhealthy"
        finally:
            self._stop_patches(patches)

    async def test_deep_health_overall_status(self, client, auth_header):
        patches = self._mock_deep_health()
        try:
            resp = await client.get("/api/metrics/health/deep", headers=auth_header)
            data = resp.json()
            assert data["overall_status"] in (
                "healthy", "degraded", "unhealthy", "unknown"
            )
        finally:
            self._stop_patches(patches)


class TestHealthStatusHelper:
    """Unit tests for _health_status helper."""

    def test_healthy(self) -> None:
        from app.routers.metrics import _health_status
        assert _health_status(False, False) == "healthy"

    def test_degraded(self) -> None:
        from app.routers.metrics import _health_status
        assert _health_status(False, True) == "degraded"

    def test_unhealthy(self) -> None:
        from app.routers.metrics import _health_status
        assert _health_status(True, True) == "unhealthy"

    def test_unhealthy_takes_precedence(self) -> None:
        from app.routers.metrics import _health_status
        assert _health_status(True, False) == "unhealthy"
