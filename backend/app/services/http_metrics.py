"""HTTP middleware that instruments every request through the metrics registry.

Tracks: active request count, total requests, errors, latency histograms,
and a recent-events ring buffer with method, path, status, and duration.
"""
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.services.metrics import get_metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP request metrics. Skips /health and /metrics themselves."""

    async def dispatch(self, request: Request, call_next):
        m = get_metrics()
        path = request.url.path

        # Skip self-referential and health endpoints
        if path in ("/health", "/api/metrics", "/api/metrics/summary"):
            return await call_next(request)

        m.http_active.increment()
        m.http_requests.increment()

        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            m.http_active.decrement()
            m.http_latency.observe(elapsed_ms)

            if status >= 400:
                m.http_errors.increment()

            m.http_recent.push({
                "method": request.method,
                "path": path,
                "status": status,
                "duration_ms": round(elapsed_ms, 1),
            })

        return response
