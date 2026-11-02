"""Monitoring API router.

Provides read-only endpoints for observability:
- /api/metrics - full metrics snapshot (JSON)
- /api/metrics/summary - condensed health summary
- /api/metrics/health/deep - deep health check (DB, LLM, Redis)
"""
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.config import settings
from app.routers.auth import get_current_user_from_token
from app.models import User
from app.services.metrics import get_metrics
from app.services.db_monitor import check_db_health, update_pool_stats
from app.services.llm_monitor import check_llm_health

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _is_admin(user: User) -> bool:
    return user.is_admin


@router.get("")
def get_metrics_endpoint(
    user: User = Depends(get_current_user_from_token),
):
    """Full metrics snapshot. Available to all authenticated users.

    Returns a nested JSON object with metrics for HTTP, database, LLM, RAG,
    Celery, WebSocket, TTS, embeddings, and system resources.
    """
    update_pool_stats()
    return get_metrics().snapshot()


@router.get("/summary")
def get_metrics_summary(
    user: User = Depends(get_current_user_from_token),
):
    """Condensed health summary with computed health scores.

    Each subsystem gets a status: healthy, degraded, or unhealthy,
    based on error rates, latency thresholds, and resource utilization.
    """
    m = get_metrics()
    snap = m.snapshot()

    # Compute health indicators per subsystem
    indicators: Dict[str, Dict[str, Any]] = {}

    # HTTP health
    http = snap["http"]
    http_errors = http["total_errors"]
    http_total = http["total_requests"]
    error_rate = http_errors / max(http_total, 1)
    p95 = http["latency_ms"].get("p95", 0)
    indicators["http"] = {
        "status": _health_status(error_rate > 0.1 or p95 > 30000,
                                 error_rate > 0.05 or p95 > 10000),
        "error_rate": round(error_rate, 4),
        "p95_latency_ms": p95,
        "active_requests": http["active"],
    }

    # Database health
    db = snap["database"]
    pool = db["pool"]
    pool_exhausted = pool["checked_out"] >= pool["size"] + pool["overflow"]
    indicators["database"] = {
        "status": _health_status(
            pool_exhausted and pool["pending"] > 0,
            pool_exhausted or pool["overflow"] > pool["size"]
        ),
        "pool_usage": round(pool["checked_out"] / max(pool["size"], 1) * 100, 1),
        "overflow_conns": pool["overflow"],
        "pending": pool["pending"],
        "errors": db["errors"],
    }

    # LLM health
    llm = snap["llm"]
    llm_total = llm["total_requests"]
    llm_err_rate = llm["total_errors"] / max(llm_total, 1)
    indicators["llm"] = {
        "status": _health_status(llm_err_rate > 0.5, llm_err_rate > 0.1),
        "error_rate": round(llm_err_rate, 4),
        "p95_latency_ms": llm["latency_ms"].get("p95", 0),
        "active_calls": llm["active"],
        "total_requests": llm_total,
    }

    # Celery health
    celery = snap["celery"]
    indicators["celery"] = {
        "status": _health_status(
            celery["workers_online"] == 0 and celery["submitted"] > 0,
            celery["failed"] > celery["succeeded"] * 0.1
        ),
        "workers_online": celery["workers_online"],
        "queue_depths": celery["queues"],
        "active_tasks": celery["active"],
        "failure_rate": round(
            celery["failed"] / max(celery["submitted"], 1), 4
        ),
    }

    # System health
    sys_info = snap["system"]
    indicators["system"] = {
        "status": _health_status(
            sys_info["cpu_percent"] > 95 or sys_info["memory_percent"] > 95,
            sys_info["cpu_percent"] > 80 or sys_info["memory_percent"] > 80,
        ),
        "cpu_percent": sys_info["cpu_percent"],
        "memory_mb": sys_info["memory_used_mb"],
        "memory_percent": sys_info["memory_percent"],
        "disk_percent": sys_info["disk_used_percent"],
        "uptime_seconds": sys_info["uptime_seconds"],
    }

    # RAG health
    rag = snap["rag"]
    rag_total = rag["total_requests"]
    indicators["rag"] = {
        "status": _health_status(
            rag["errors"] > rag_total * 0.5,
            rag["errors"] > rag_total * 0.1,
        ),
        "total_requests": rag_total,
        "errors": rag["errors"],
        "p95_latency_ms": rag["latency_ms"].get("p95", 0),
    }

    # TTS health
    tts = snap["tts"]
    tts_total = tts["requests"]
    indicators["tts"] = {
        "status": _health_status(
            tts["errors"] > tts_total * 0.5,
            tts["errors"] > tts_total * 0.1,
        ),
        "requests": tts_total,
        "cache_hit_rate": round(
            tts["cache_hits"] / max(tts_total, 1), 4
        ),
        "errors": tts["errors"],
    }

    # Overall
    statuses = [v["status"] for v in indicators.values()]
    if "unhealthy" in statuses:
        overall = "unhealthy"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "overall_status": overall,
        "indicators": indicators,
    }


@router.get("/health/deep")
async def deep_health_check(
    user: User = Depends(get_current_user_from_token),
):
    """Deep health check that probes all backend dependencies.

    Tests: PostgreSQL connectivity, Redis/Celery broker, LLM backends,
    TTS service. Returns detailed status for each dependency.

    This endpoint is intentionally more expensive than /health - use it
    for periodic monitoring, not every request.
    """
    results: Dict[str, Any] = {}

    # Database
    results["database"] = check_db_health()

    # Redis / Celery broker
    try:
        import redis
        r = redis.from_url(settings.celery_broker_url, decode_responses=True)
        start = time.perf_counter()
        pong = r.ping()
        latency = (time.perf_counter() - start) * 1000
        info = r.info("memory")
        r.close()
        results["redis"] = {
            "status": "healthy" if pong else "unhealthy",
            "latency_ms": round(latency, 1),
            "used_memory_mb": round(info.get("used_memory", 0) / (1024 * 1024), 1),
        }
    except Exception as e:
        results["redis"] = {"status": "unhealthy", "error": str(e)}

    # Celery workers
    try:
        from app.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=3)
        ping = inspect.ping()
        active_queues = inspect.active_queues()
        results["celery"] = {
            "status": "healthy" if ping else "unhealthy",
            "workers": list(ping.keys()) if ping else [],
            "active_queues": active_queues,
        }
    except Exception as e:
        results["celery"] = {"status": "unhealthy", "error": str(e)}

    # LLM backends
    llm_results = []
    # Ollama
    ollama_url = settings.ollama_base_url
    if ollama_url:
        llm_results.append(check_llm_health(
            ollama_url, settings.ollama_model_chat, "ollama"
        ))
    # vLLM
    if settings.vllm_base_url and settings.vllm_api_key != "EMPTY":
        llm_results.append(check_llm_health(
            settings.vllm_base_url, settings.vllm_model or "unknown",
            settings.vllm_api_key,
        ))
    results["llm_backends"] = llm_results

    # TTS service
    try:
        import httpx
        start = time.perf_counter()
        resp = httpx.get(f"{settings.tts_url}/", timeout=3.0)
        latency = (time.perf_counter() - start) * 1000
        results["tts"] = {
            "status": "healthy" if resp.status_code < 500 else "degraded",
            "latency_ms": round(latency, 1),
        }
    except Exception as e:
        results["tts"] = {"status": "unhealthy", "error": str(e)}

    # Overall
    statuses = [v.get("status", "unhealthy") for v in results.values()
                if isinstance(v, dict)]
    if not statuses:
        overall = "unknown"
    elif all(s == "healthy" for s in statuses):
        overall = "healthy"
    elif any(s == "unhealthy" for s in statuses):
        overall = "unhealthy"
    else:
        overall = "degraded"

    return {"overall_status": overall, "dependencies": results}


def _health_status(unhealthy: bool, degraded: bool) -> str:
    if unhealthy:
        return "unhealthy"
    if degraded:
        return "degraded"
    return "healthy"
