"""LLM call instrumentation for the chat engine.

Wraps the chat engine's key methods to record latency, token counts, error
rates, and active-call gauges without modifying the engine itself.
"""
import time
from typing import Any, Dict, Optional


from app.services.metrics import get_metrics


async def track_llm_call(
    fn, *args, call_type: str = "chat", user_id: Optional[int] = None, **kwargs
):
    """Run an async LLM call wrapped in metrics instrumentation.

    Usage in chat_engine:
        result = await track_llm_call(
            self._model(user_id).ainvoke, messages, call_type="answer", user_id=user_id
        )
    """
    m = get_metrics()
    m.llm_requests.increment()
    m.llm_active.set(m.llm_active.value + 1)

    start = time.perf_counter()
    success = False
    try:
        result = await fn(*args, **kwargs)
        success = True
        return result
    except Exception:
        m.llm_errors.increment()
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        m.llm_active.set(m.llm_active.value - 1)
        m.llm_latency.observe(elapsed_ms)

        m.llm_recent.push({
            "type": call_type,
            "user_id": user_id,
            "duration_ms": round(elapsed_ms, 1),
            "success": success,
        })


def record_tokens(input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Record token counts from a completed LLM call."""
    m = get_metrics()
    if input_tokens:
        m.llm_tokens_in.increment(input_tokens)
    if output_tokens:
        m.llm_tokens_out.increment(output_tokens)


def check_llm_health(base_url: str, model: str, api_key: str = "ollama") -> Dict[str, Any]:
    """Probe an LLM endpoint to check reachability.

    Returns a dict with status, latency, and model info.
    """
    import httpx

    start = time.perf_counter()
    try:
        # Lightweight probe: just list models or make a minimal chat request
        resp = httpx.get(
            base_url.replace("/v1", "") + "/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            return {
                "status": "healthy",
                "latency_ms": round(latency_ms, 1),
                "base_url": base_url,
                "available_models": [m_dict.get("id", m_dict) for m_dict in models[:10]],
            }
        return {
            "status": "degraded",
            "http_status": resp.status_code,
            "latency_ms": round(latency_ms, 1),
            "base_url": base_url,
        }
    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return {
            "status": "unhealthy",
            "error": str(e),
            "latency_ms": round(latency_ms, 1),
            "base_url": base_url,
        }
